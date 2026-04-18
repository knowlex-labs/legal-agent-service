"""Extract text from uploaded files with structure preservation.

Priority chain:
1. Plain text / markdown  → direct UTF-8 decode
2. PDF (text-based)       → PyMuPDF with structure detection → pdfplumber fallback
3. PDF (scanned/image)    → Gemini Vision OCR (markdown output) → pytesseract fallback
4. Image files            → Gemini Vision OCR → pytesseract fallback
"""

import io
import logging
import re

logger = logging.getLogger(__name__)

_MIN_TEXT_CHARS = 100  # below this we consider the extraction failed and try next strategy


class ExtractionError(Exception):
    """Raised when no extraction strategy could produce usable text."""


def _is_pdf(data: bytes) -> bool:
    return data[:4] == b"%PDF"


def _is_image(filename: str) -> bool:
    return filename.lower().rsplit(".", 1)[-1] in {"jpg", "jpeg", "png", "tiff", "tif", "bmp", "webp"}


def _extract_with_fitz(data: bytes) -> str:
    import fitz  # PyMuPDF

    doc = fitz.open(stream=data, filetype="pdf")
    parts = [page.get_text() for page in doc]
    doc.close()
    return "\n".join(parts).strip()


def _extract_structured_with_fitz(data: bytes) -> str:
    """Extract text from a text-based PDF preserving structure as markdown.

    Uses font size analysis to detect headings and bold text.
    """
    import fitz

    doc = fitz.open(stream=data, filetype="pdf")
    if doc.page_count == 0:
        doc.close()
        return ""

    # Gather font sizes across first few pages to find the body size
    all_sizes: list[float] = []
    for page in doc[:min(5, doc.page_count)]:
        blocks = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)["blocks"]
        for block in blocks:
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    if span["text"].strip():
                        all_sizes.append(round(span["size"], 1))

    if not all_sizes:
        doc.close()
        return ""

    # Body size = most common font size
    from collections import Counter
    size_counts = Counter(all_sizes)
    body_size = size_counts.most_common(1)[0][0]

    md_parts: list[str] = []
    for page in doc:
        blocks = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)["blocks"]
        for block in blocks:
            lines = block.get("lines", [])
            if not lines:
                continue

            block_texts: list[str] = []
            for line in lines:
                line_text = ""
                for span in line.get("spans", []):
                    text = span["text"]
                    if not text.strip():
                        line_text += text
                        continue

                    size = round(span["size"], 1)
                    is_bold = "bold" in span.get("font", "").lower()
                    flags = span.get("flags", 0)
                    if flags & 2 ** 4:  # bit 4 = bold
                        is_bold = True

                    if size >= body_size + 4:
                        line_text += f"## {text.strip()}"
                    elif size >= body_size + 2:
                        line_text += f"### {text.strip()}"
                    elif is_bold:
                        line_text += f"**{text.strip()}**"
                    else:
                        line_text += text

                if line_text.strip():
                    block_texts.append(line_text.rstrip())

            if block_texts:
                md_parts.append("\n".join(block_texts))

        md_parts.append("")  # page break

    doc.close()
    result = "\n\n".join(md_parts).strip()
    # Clean up excessive whitespace while preserving paragraph breaks
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result


def _extract_with_pdfplumber(data: bytes) -> str:
    import pdfplumber

    with pdfplumber.open(io.BytesIO(data)) as pdf:
        parts = [page.extract_text() or "" for page in pdf.pages]
    return "\n".join(parts).strip()


def _ocr_pdf_gemini(data: bytes) -> str:
    """OCR a scanned PDF using Gemini Vision with markdown output."""
    from legal_agent.utils.ocr import ocr_pdf_with_gemini

    return ocr_pdf_with_gemini(data, output_format="markdown")


def _ocr_image_gemini(data: bytes, filename: str) -> str:
    """OCR an image file using Gemini Vision."""
    from legal_agent.utils.ocr import ocr_image_with_gemini

    ext = filename.lower().rsplit(".", 1)[-1]
    mime_map = {
        "jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
        "tiff": "image/tiff", "tif": "image/tiff", "bmp": "image/bmp",
        "webp": "image/webp",
    }
    mime_type = mime_map.get(ext, "image/png")
    return ocr_image_with_gemini(data, mime_type=mime_type, output_format="markdown")


def _ocr_pdf_tesseract(data: bytes) -> str:
    """Fallback OCR using pytesseract (offline, lower quality)."""
    from pdf2image import convert_from_bytes
    import pytesseract

    images = convert_from_bytes(data)
    parts = [pytesseract.image_to_string(img, lang="hin+eng") for img in images]
    return "\n".join(parts).strip()


def _ocr_image_tesseract(data: bytes) -> str:
    """Fallback image OCR using pytesseract (offline, lower quality)."""
    from PIL import Image
    import pytesseract

    img = Image.open(io.BytesIO(data))
    return pytesseract.image_to_string(img, lang="hin+eng").strip()


def extract_text_from_bytes(data: bytes, filename: str) -> str:
    """Extract text from raw file bytes with structure preservation.

    For text-based PDFs, returns markdown with headings and bold detected from font metadata.
    For scanned PDFs and images, uses Gemini Vision OCR with markdown output.
    Falls back to pytesseract if Gemini is unavailable.

    Args:
        data: Raw file bytes downloaded from S3.
        filename: Original filename — used to determine file type.

    Returns:
        Extracted text (markdown-formatted when possible).

    Raises:
        ExtractionError: If no strategy produced usable text.
    """
    filename = filename.lower()

    # Plain text / markdown — return as-is
    if not _is_pdf(data) and not _is_image(filename):
        try:
            text = data.decode("utf-8", errors="replace").strip()
            if len(text) >= _MIN_TEXT_CHARS:
                logger.debug(f"Extracted {len(text)} chars via plain text decode")
                return text
        except Exception as exc:
            logger.debug(f"Plain text decode failed: {exc}")

    if _is_pdf(data):
        # Try structured extraction with fitz first (text-based PDFs)
        try:
            text = _extract_structured_with_fitz(data)
            if len(text) >= _MIN_TEXT_CHARS:
                logger.debug(f"Extracted {len(text)} chars via structured PyMuPDF")
                return text
            logger.debug(f"Structured PyMuPDF yielded only {len(text)} chars, trying flat fitz")
        except Exception as exc:
            logger.debug(f"Structured PyMuPDF failed: {exc}")

        # Flat fitz extraction
        try:
            text = _extract_with_fitz(data)
            if len(text) >= _MIN_TEXT_CHARS:
                logger.debug(f"Extracted {len(text)} chars via flat PyMuPDF")
                return text
            logger.debug(f"Flat PyMuPDF yielded only {len(text)} chars, trying pdfplumber")
        except Exception as exc:
            logger.debug(f"Flat PyMuPDF extraction failed: {exc}")

        # pdfplumber fallback
        try:
            text = _extract_with_pdfplumber(data)
            if len(text) >= _MIN_TEXT_CHARS:
                logger.debug(f"Extracted {len(text)} chars via pdfplumber")
                return text
            logger.debug(f"pdfplumber yielded only {len(text)} chars, trying Gemini Vision OCR")
        except Exception as exc:
            logger.debug(f"pdfplumber extraction failed: {exc}")

        # Gemini Vision OCR (primary OCR for scanned PDFs)
        try:
            text = _ocr_pdf_gemini(data)
            if len(text) >= _MIN_TEXT_CHARS:
                logger.debug(f"Extracted {len(text)} chars via Gemini Vision OCR")
                return text
            logger.debug(f"Gemini Vision OCR yielded only {len(text)} chars")
        except Exception as exc:
            logger.warning(f"Gemini Vision OCR failed: {exc}")

        # pytesseract offline fallback
        try:
            text = _ocr_pdf_tesseract(data)
            if len(text) >= _MIN_TEXT_CHARS:
                logger.debug(f"Extracted {len(text)} chars via pytesseract (fallback)")
                return text
            logger.debug(f"pytesseract yielded only {len(text)} chars")
        except Exception as exc:
            logger.debug(f"pytesseract OCR failed: {exc}")

        raise ExtractionError(
            f"Could not extract usable text from PDF '{filename}'. "
            "All strategies (PyMuPDF, pdfplumber, Gemini Vision, tesseract) returned too little text."
        )

    if _is_image(filename):
        # Gemini Vision OCR (primary)
        try:
            text = _ocr_image_gemini(data, filename)
            if len(text) >= _MIN_TEXT_CHARS:
                logger.debug(f"Extracted {len(text)} chars via Gemini Vision OCR (image)")
                return text
        except Exception as exc:
            logger.warning(f"Gemini Vision OCR (image) failed: {exc}")

        # pytesseract fallback
        try:
            text = _ocr_image_tesseract(data)
            if len(text) >= _MIN_TEXT_CHARS:
                logger.debug(f"Extracted {len(text)} chars via pytesseract (image fallback)")
                return text
        except Exception as exc:
            logger.debug(f"Image pytesseract OCR failed: {exc}")

        raise ExtractionError(
            f"Could not extract usable text from image '{filename}' via OCR."
        )

    raise ExtractionError(
        f"Unsupported file type for '{filename}'. Provide a PDF, image, or plain text file."
    )
