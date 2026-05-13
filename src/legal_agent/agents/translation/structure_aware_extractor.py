"""Text extraction from PDFs and images.

Used by: draft services (extract_for_translation) and OCR fallback in layout_extract.py.

Extraction layers for extract_for_translation (tried in order):
1. pymupdf4llm -- headings from font-size ratios, bold/italic from font flags
2. Conservative PyMuPDF get_text("text") + tables
3. Flat PyMuPDF fallback
4. pdfplumber
5. Vision OCR (Gemini)
6. pytesseract OCR
"""

from __future__ import annotations

import io
import logging
import re
import unicodedata
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from legal_agent.models.documents import DocumentType

logger = logging.getLogger(__name__)

_MIN_TEXT_CHARS = 100


def _is_pdf(data: bytes) -> bool:
    return data[:4] == b"%PDF"


def _is_image(filename: str) -> bool:
    return filename.lower().rsplit(".", 1)[-1] in {
        "jpg", "jpeg", "png", "tiff", "tif", "bmp", "webp",
    }



def _conservative_extract(data: bytes) -> str:
    """PyMuPDF get_text("text") + tables via find_tables()."""
    import fitz

    doc = fitz.open(stream=data, filetype="pdf")
    if doc.page_count == 0:
        doc.close()
        return ""

    page_chunks: list[str] = []
    for page in doc:
        text = cast(str, page.get_text("text")).rstrip()
        tables_md = _extract_tables_markdown(page)
        if tables_md:
            text = f"{text}\n\n{tables_md}".strip()
        if text:
            page_chunks.append(text)

    doc.close()
    result = "\n\n".join(page_chunks).strip()
    result = re.sub(r"\n{3,}", "\n\n", result)
    return unicodedata.normalize("NFC", result)


def _markdown_extract(data: bytes) -> str:
    """Full-document markdown via pymupdf4llm."""
    import fitz
    import pymupdf4llm

    doc = fitz.open(stream=data, filetype="pdf")
    if doc.page_count == 0:
        doc.close()
        return ""
    chunks = pymupdf4llm.to_markdown(doc, page_chunks=True, show_progress=False)
    doc.close()
    parts: list[str] = []
    for chunk in chunks:
        md = chunk.get("text", "").rstrip()
        md = re.sub(r"\n{3,}", "\n\n", md).strip()
        if md:
            parts.append(md)
    result = "\n\n".join(parts)
    result = re.sub(r"\n{3,}", "\n\n", result).strip()
    return unicodedata.normalize("NFC", result)


def _extract_flat_with_fitz(data: bytes) -> str:
    import fitz
    doc = fitz.open(stream=data, filetype="pdf")
    parts = [cast(str, page.get_text()) for page in doc]
    doc.close()
    return "\n".join(parts).strip()


def _extract_with_pdfplumber(data: bytes) -> str:
    import pdfplumber
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        parts = [page.extract_text() or "" for page in pdf.pages]
    return "\n".join(parts).strip()


def _ocr_pdf_vision(data: bytes, *, language: str | None = None, output_format: str = "markdown") -> str:
    """OCR a PDF via the configured Vision provider (Sarvam Vision by default).

    The translation IR pipeline overrides `output_format="html"` so structure
    (headings, paragraphs, lists, tables) and image regions are exposed as
    discrete tags — letting the caller drop image descriptions cleanly instead
    of regex-filtering Sarvam meta-commentary. Other callers (markdown fallback
    chain in extract_for_translation) keep the markdown default.
    """
    from legal_agent.utils.ocr import ocr_pdf
    return ocr_pdf(data, output_format=output_format, language=language)  # type: ignore[arg-type]


def _ocr_pdf_tesseract(data: bytes) -> str:
    from pdf2image import convert_from_bytes
    import pytesseract
    images = convert_from_bytes(data)
    parts = [pytesseract.image_to_string(img, lang="hin+eng") for img in images]
    return "\n".join(parts).strip()


def _ocr_image_vision(data: bytes, filename: str) -> str:
    from legal_agent.utils.ocr import ocr_image
    ext = filename.lower().rsplit(".", 1)[-1]
    mime_map = {
        "jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
        "tiff": "image/tiff", "tif": "image/tiff", "bmp": "image/bmp",
        "webp": "image/webp",
    }
    return ocr_image(data, mime_type=mime_map.get(ext, "image/png"), output_format="markdown")


def _ocr_image_tesseract(data: bytes) -> str:
    from PIL import Image
    import pytesseract
    img = Image.open(io.BytesIO(data))
    return pytesseract.image_to_string(img, lang="hin+eng").strip()


def _extract_tables_markdown(page) -> str:
    """Render each table on the page as a markdown table."""
    try:
        finder = page.find_tables()
    except Exception:
        return ""

    out: list[str] = []
    tables = getattr(finder, "tables", []) or []
    for table in tables:
        try:
            rows = table.extract()
        except Exception:
            continue
        if not rows or not rows[0]:
            continue

        header = [_clean_cell(c) for c in rows[0]]
        if not any(header):
            continue
        body = [[_clean_cell(c) for c in row] for row in rows[1:] if row]

        out.append("| " + " | ".join(header) + " |")
        out.append("|" + "|".join(["---"] * len(header)) + "|")
        for row in body:
            row = (row + [""] * len(header))[: len(header)]
            out.append("| " + " | ".join(row) + " |")
        out.append("")

    return "\n".join(out).strip()


def _clean_cell(value) -> str:
    if value is None:
        return ""
    text = str(value).replace("\n", " ").replace("|", "/").strip()
    return re.sub(r"\s+", " ", text)


def extract_for_translation(
    data: bytes,
    filename: str,
    document_type: "DocumentType | None" = None,
) -> tuple[str, list]:
    """Extract text from PDF/image/plaintext for downstream use (drafts, etc).

    Returns (text, []) -- the second element is kept for backward compatibility
    with callers that destructure as (text, _ledger).
    """
    del document_type
    filename_lower = filename.lower()

    if not _is_pdf(data) and not _is_image(filename_lower):
        try:
            text = data.decode("utf-8", errors="replace").strip()
            if len(text) >= _MIN_TEXT_CHARS:
                return text, []
        except Exception as exc:
            logger.debug("Plain text decode failed: %s", exc)

    if _is_pdf(data):
        for label, fn in (
            ("markdown PyMuPDF", lambda: _markdown_extract(data)),
            ("conservative PyMuPDF", lambda: _conservative_extract(data)),
            ("flat PyMuPDF", lambda: _extract_flat_with_fitz(data)),
            ("pdfplumber", lambda: _extract_with_pdfplumber(data)),
            ("Vision OCR", lambda: _ocr_pdf_vision(data)),
            ("pytesseract", lambda: _ocr_pdf_tesseract(data)),
        ):
            try:
                text = fn()
            except Exception as exc:
                logger.debug("%s failed: %s", label, exc)
                continue
            if len(text) >= _MIN_TEXT_CHARS:
                logger.debug("Extracted %d chars via %s", len(text), label)
                return text, []
            logger.debug("%s yielded only %d chars, trying next", label, len(text))
        raise RuntimeError(
            f"Could not extract usable text from PDF '{filename}'. "
            "All strategies (PyMuPDF, pdfplumber, Vision OCR, tesseract) returned too little."
        )

    if _is_image(filename_lower):
        for label, fn in (
            ("Vision OCR (image)", lambda: _ocr_image_vision(data, filename_lower)),
            ("pytesseract (image)", lambda: _ocr_image_tesseract(data)),
        ):
            try:
                text = fn()
            except Exception as exc:
                logger.debug("%s failed: %s", label, exc)
                continue
            if len(text) >= _MIN_TEXT_CHARS:
                return text, []
        raise RuntimeError(
            f"Could not extract usable text from image '{filename}' via OCR."
        )

    raise RuntimeError(
        f"Unsupported file type for '{filename}'. Provide a PDF, image, or plain text file."
    )
