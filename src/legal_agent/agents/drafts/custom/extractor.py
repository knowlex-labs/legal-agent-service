"""Extract plain text from uploaded template files.

Priority chain:
1. Plain text / markdown  → direct UTF-8 decode
2. PDF                    → PyMuPDF (fitz) text extraction
3. PDF fallback           → pdfplumber if fitz yields < 100 chars
4. Image-only PDF / image → pytesseract OCR (via pdf2image for PDFs)
"""

import io
import logging

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


def _extract_with_pdfplumber(data: bytes) -> str:
    import pdfplumber

    with pdfplumber.open(io.BytesIO(data)) as pdf:
        parts = [page.extract_text() or "" for page in pdf.pages]
    return "\n".join(parts).strip()


def _ocr_pdf(data: bytes) -> str:
    from pdf2image import convert_from_bytes
    import pytesseract

    images = convert_from_bytes(data)
    parts = [pytesseract.image_to_string(img) for img in images]
    return "\n".join(parts).strip()


def _ocr_image(data: bytes) -> str:
    from PIL import Image
    import pytesseract

    img = Image.open(io.BytesIO(data))
    return pytesseract.image_to_string(img).strip()


def extract_text_from_bytes(data: bytes, filename: str) -> str:
    """Extract text from raw file bytes.

    Args:
        data: Raw file bytes downloaded from S3.
        filename: Original filename — used to determine file type.

    Returns:
        Extracted plain text (non-empty).

    Raises:
        ExtractionError: If no strategy produced usable text.
    """
    filename = filename.lower()

    # Plain text / markdown
    if not _is_pdf(data) and not _is_image(filename):
        try:
            text = data.decode("utf-8", errors="replace").strip()
            if len(text) >= _MIN_TEXT_CHARS:
                logger.debug(f"Extracted {len(text)} chars via plain text decode")
                return text
        except Exception as exc:
            logger.debug(f"Plain text decode failed: {exc}")

    if _is_pdf(data):
        # Try fitz first
        try:
            text = _extract_with_fitz(data)
            if len(text) >= _MIN_TEXT_CHARS:
                logger.debug(f"Extracted {len(text)} chars via PyMuPDF")
                return text
            logger.debug(f"PyMuPDF yielded only {len(text)} chars, trying pdfplumber")
        except Exception as exc:
            logger.debug(f"PyMuPDF extraction failed: {exc}")

        # Try pdfplumber
        try:
            text = _extract_with_pdfplumber(data)
            if len(text) >= _MIN_TEXT_CHARS:
                logger.debug(f"Extracted {len(text)} chars via pdfplumber")
                return text
            logger.debug(f"pdfplumber yielded only {len(text)} chars, trying OCR")
        except Exception as exc:
            logger.debug(f"pdfplumber extraction failed: {exc}")

        # OCR fallback for image-based PDFs
        try:
            text = _ocr_pdf(data)
            if len(text) >= _MIN_TEXT_CHARS:
                logger.debug(f"Extracted {len(text)} chars via OCR (pdf2image + tesseract)")
                return text
            logger.debug(f"OCR yielded only {len(text)} chars")
        except Exception as exc:
            logger.debug(f"OCR (PDF) failed: {exc}")

        raise ExtractionError(
            f"Could not extract usable text from PDF '{filename}'. "
            "All strategies (PyMuPDF, pdfplumber, OCR) returned too little text."
        )

    if _is_image(filename):
        try:
            text = _ocr_image(data)
            if len(text) >= _MIN_TEXT_CHARS:
                logger.debug(f"Extracted {len(text)} chars via image OCR")
                return text
        except Exception as exc:
            logger.debug(f"Image OCR failed: {exc}")

        raise ExtractionError(
            f"Could not extract usable text from image '{filename}' via OCR."
        )

    raise ExtractionError(
        f"Unsupported file type for '{filename}'. Provide a PDF, image, or plain text file."
    )
