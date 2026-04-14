"""Shared Gemini Vision OCR utility for extracting text from document images.

Supports two output formats:
- "markdown": Structured output preserving headings, bold, tables, lists (for translation)
- "plain": Flat text transcription (for RAG indexing)
"""

import logging
from typing import Literal

import fitz  # PyMuPDF

from legal_agent.config import get_settings

logger = logging.getLogger(__name__)

_PROMPT_MARKDOWN = (
    "You are an expert legal document OCR system. Extract ALL text from this document page "
    "and output it in clean **markdown** format.\n\n"
    "Rules:\n"
    "- Preserve the document's visual hierarchy using markdown headings (##, ###)\n"
    "- Use **bold** for text that appears bold, underlined, or emphasized\n"
    "- Preserve numbered lists (1. 2. 3.) and bullet points (- )\n"
    "- Reproduce tables using markdown table syntax (| col1 | col2 |)\n"
    "- Keep all dates, numbers, case citations, statute references exactly as they appear\n"
    "- Include stamps, seals (describe as [STAMP: ...] or [SEAL: ...]), and handwritten notes\n"
    "- Preserve paragraph breaks and clause structure\n"
    "- Do NOT add any commentary, preamble, or explanation — output ONLY the extracted content\n"
    "- If text is in a non-English script (Hindi, Tamil, Bengali, etc.), transcribe it accurately "
    "in the original script"
)

_PROMPT_PLAIN = (
    "Transcribe all text from this document page exactly as it appears. "
    "Include all visible text: headers, body, stamps, seals, dates, names, "
    "numbers, handwritten notes, and footers. Preserve structure and formatting."
)


def ocr_pdf_with_gemini(
    pdf_data: bytes,
    output_format: Literal["markdown", "plain"] = "markdown",
) -> str:
    """OCR a PDF using Gemini Vision, page by page.

    Args:
        pdf_data: Raw PDF bytes.
        output_format: "markdown" for structured output, "plain" for flat text.

    Returns:
        Extracted text (all pages concatenated).
    """
    from google import genai
    from google.genai import types

    settings = get_settings()
    client = genai.Client(api_key=settings.gemini_api_key or "")
    prompt = _PROMPT_MARKDOWN if output_format == "markdown" else _PROMPT_PLAIN

    doc = fitz.open(stream=pdf_data, filetype="pdf")
    page_texts: list[str] = []

    for page_num in range(doc.page_count):
        page = doc[page_num]
        pixmap = page.get_pixmap(matrix=fitz.Matrix(2, 2))
        image_bytes = pixmap.tobytes("png")

        response = client.models.generate_content(
            model=settings.gemini_model,
            contents=[
                types.Part.from_bytes(data=image_bytes, mime_type="image/png"),
                prompt,
            ],
            config=types.GenerateContentConfig(
                temperature=0.1,
                max_output_tokens=settings.gemini_max_tokens,
            ),
        )
        page_text = response.text.strip()
        logger.info(
            f"[gemini-ocr] Page {page_num + 1}/{doc.page_count}: "
            f"{len(page_text)} chars extracted"
        )
        page_texts.append(page_text)

    doc.close()
    return "\n\n".join(page_texts)


def ocr_image_with_gemini(
    image_bytes: bytes,
    mime_type: str = "image/png",
    output_format: Literal["markdown", "plain"] = "markdown",
) -> str:
    """OCR a single image using Gemini Vision.

    Args:
        image_bytes: Raw image bytes (PNG, JPEG, etc.).
        mime_type: MIME type of the image.
        output_format: "markdown" for structured output, "plain" for flat text.

    Returns:
        Extracted text.
    """
    from google import genai
    from google.genai import types

    settings = get_settings()
    client = genai.Client(api_key=settings.gemini_api_key or "")
    prompt = _PROMPT_MARKDOWN if output_format == "markdown" else _PROMPT_PLAIN

    response = client.models.generate_content(
        model=settings.gemini_model,
        contents=[
            types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
            prompt,
        ],
        config=types.GenerateContentConfig(
            temperature=0.1,
            max_output_tokens=settings.gemini_max_tokens,
        ),
    )
    return response.text.strip()
