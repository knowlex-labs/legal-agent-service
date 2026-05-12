"""Structure-aware text extraction for the translation pipeline.

Extraction layers (tried in order):

1. **pymupdf4llm** (`_markdown_extract`) — primary path. Uses pymupdf4llm.to_markdown()
   which detects headings from font-size ratios, bold/italic from font flags, and
   multi-column layouts. Returns proper markdown with `# heading`, `**bold**`, `*italic*`.

2. **Conservative emitter** (`_conservative_extract`) — fallback. PyMuPDF
   `get_text("text")` only. Paragraph breaks survive; tables come from
   `page.find_tables()`. NO heading guesses, NO bold spans.

3. **OCR fallbacks** — Vision OCR (Gemini), then pytesseract for scanned PDFs.

4. **Optional LLM structure pass** (`enhance_with_llm_structure`) — fires only for
   the `court_filing` family on short documents WITHOUT existing headings. Adds
   H1 cause-title and H2 section headers plus a do-not-translate ledger. Skipped
   when pymupdf4llm already produced headings.

The result `(markdown, ledger)` is consumed by `service.py`.
"""

from __future__ import annotations

import io
import json
import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from legal_agent.models.documents import DocumentType

logger = logging.getLogger(__name__)


_MIN_TEXT_CHARS = 100
_LLM_STRUCTURE_MAX_CHARS = 8000
_LEDGER_SENTINEL_OPEN = "<<<LEDGER_JSON>>>"
_LEDGER_SENTINEL_CLOSE = "<<<END>>>"
_TOKEN_OVERLAP_THRESHOLD = 0.6


@dataclass
class LedgerEntry:
    """A span of source text the translator must preserve verbatim.

    `kind` is one of: citation, statute, date, monetary, name, case_no, other.
    `text` is the literal source span (used for substring + render-guard verification).
    `note` is an optional hint shown to the translator (e.g. "FIR number").
    """

    kind: str
    text: str
    note: str = ""


def _is_pdf(data: bytes) -> bool:
    return data[:4] == b"%PDF"


def _is_image(filename: str) -> bool:
    return filename.lower().rsplit(".", 1)[-1] in {
        "jpg", "jpeg", "png", "tiff", "tif", "bmp", "webp",
    }


# ── Layer 1: conservative deterministic emitter ──────────────────────────────


def _conservative_extract(data: bytes) -> str:
    """PyMuPDF `get_text("text")` over every page, then append tables from
    `page.find_tables()` as markdown. No bold/heading invention.

    Returns plain markdown — paragraph breaks survive (PyMuPDF inserts blank
    lines between paragraphs), numbered list items already start with `1.` /
    `(a)` because the source PDF rendered them that way.
    """
    import fitz  # PyMuPDF

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
    # Collapse runs of 3+ blank lines (some PDFs sprinkle them) but keep
    # paragraph breaks intact.
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result


def _markdown_extract_pages(data: bytes) -> list[str]:
    """pymupdf4llm per-page extraction — headings from font sizes, bold/italic
    from font flags, multi-column layout detection. Returns one markdown string
    per page.
    """
    import fitz
    import pymupdf4llm

    doc = fitz.open(stream=data, filetype="pdf")
    if doc.page_count == 0:
        doc.close()
        return []
    chunks = pymupdf4llm.to_markdown(doc, page_chunks=True, show_progress=False)
    doc.close()
    pages: list[str] = []
    for chunk in chunks:
        md = chunk.get("text", "").rstrip()
        md = re.sub(r"\n{3,}", "\n\n", md).strip()
        if md:
            pages.append(md)
    return pages


def _markdown_extract(data: bytes) -> str:
    """Full-document markdown via _markdown_extract_pages."""
    pages = _markdown_extract_pages(data)
    result = "\n\n".join(pages)
    return re.sub(r"\n{3,}", "\n\n", result).strip()


def extract_page_texts(data: bytes) -> list[str]:
    """Return per-page rich markdown from PyMuPDF. Used for parallel chunking.
    Returns [] on failure or if data is not a PDF.
    """
    if not _is_pdf(data):
        return []
    try:
        pages = _markdown_extract_pages(data)
        if pages:
            return pages
    except Exception:
        pass
    # Fallback: conservative plain-text pages for chunking boundaries.
    try:
        import fitz
        doc = fitz.open(stream=data, filetype="pdf")
        pages = [cast(str, page.get_text("text")).rstrip() for page in doc]
        doc.close()
        return [p for p in pages if p.strip()]
    except Exception:
        return []


def extract_html_pages(data: bytes) -> list[str]:
    """Return per-page HTML from PyMuPDF preserving absolute-positioned layout.
    Each element is the full HTML document for one page; the caller strips the
    outer wrapper and injects its own CSS before rendering.
    Returns [] on failure or non-PDF input.
    """
    if not _is_pdf(data):
        return []
    try:
        import fitz
        doc = fitz.open(stream=data, filetype="pdf")
        pages = []
        for page in doc:
            html = page.get_text("html")
            # Remove white-space:pre so Indic translated text can reflow when
            # it's longer than the original English (Devanagari words are often shorter
            # than Latin but sentence-level wrapping still needs to work).
            html = html.replace("white-space:pre;", "")
            pages.append(html)
        doc.close()
        return pages
    except Exception:
        return []


def _extract_tables_markdown(page) -> str:
    """Render each table on the page as a markdown table. Best-effort — if
    `find_tables` raises (older PyMuPDF, malformed PDF) we just skip."""
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
            # Pad / truncate to header width so misaligned tables still render.
            row = (row + [""] * len(header))[: len(header)]
            out.append("| " + " | ".join(row) + " |")
        out.append("")

    return "\n".join(out).strip()


def _clean_cell(value) -> str:
    if value is None:
        return ""
    text = str(value).replace("\n", " ").replace("|", "/").strip()
    return re.sub(r"\s+", " ", text)


# ── Fallback chain (mirrors drafts/custom/extractor.py but without bold guessing). ──


def _extract_flat_with_fitz(data: bytes) -> str:
    import fitz  # PyMuPDF
    doc = fitz.open(stream=data, filetype="pdf")
    parts = [cast(str, page.get_text()) for page in doc]
    doc.close()
    return "\n".join(parts).strip()


def _extract_with_pdfplumber(data: bytes) -> str:
    import pdfplumber
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        parts = [page.extract_text() or "" for page in pdf.pages]
    return "\n".join(parts).strip()


def _ocr_pdf_vision(data: bytes) -> str:
    from legal_agent.utils.ocr import ocr_pdf
    return ocr_pdf(data, output_format="markdown")


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


# ── Public entry point ──────────────────────────────────────────────────────


def extract_for_translation(
    data: bytes,
    filename: str,
    document_type: "DocumentType | None" = None,
) -> tuple[str, list[LedgerEntry]]:
    """Extract structured markdown for translation.

    `document_type` is currently consumed only by the optional LLM structure
    pass (Step 6 — `enhance_with_llm_structure`). The conservative path here
    ignores it. Kept on the signature so the call site (`service.py`) can
    drive the LLM enhancement after this returns.

    Returns:
        (markdown, ledger). Ledger is empty for the conservative path; populated
        only by `enhance_with_llm_structure` when invoked downstream.
    """
    del document_type  # consumed by the LLM pass, not here
    filename_lower = filename.lower()

    # Plain text / markdown
    if not _is_pdf(data) and not _is_image(filename_lower):
        try:
            text = data.decode("utf-8", errors="replace").strip()
            if len(text) >= _MIN_TEXT_CHARS:
                return text, []
        except Exception as exc:
            logger.debug(f"Plain text decode failed: {exc}")

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
                logger.debug(f"{label} failed: {exc}")
                continue
            if len(text) >= _MIN_TEXT_CHARS:
                logger.debug(f"Extracted {len(text)} chars via {label}")
                return text, []
            logger.debug(f"{label} yielded only {len(text)} chars, trying next")
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
                logger.debug(f"{label} failed: {exc}")
                continue
            if len(text) >= _MIN_TEXT_CHARS:
                return text, []
        raise RuntimeError(
            f"Could not extract usable text from image '{filename}' via OCR."
        )

    raise RuntimeError(
        f"Unsupported file type for '{filename}'. Provide a PDF, image, or plain text file."
    )


# ── Layer 2: optional LLM structure pass (Step 6) ───────────────────────────


async def enhance_with_llm_structure(
    text: str,
    document_type: "DocumentType | None",
) -> tuple[str, list[LedgerEntry]]:
    """Run a cheap LLM pass to add H1/H2 structure + a do-not-translate ledger.

    Triggers ONLY when:
      - document_type is set
      - layout_family resolves to 'court_filing'
      - text length ≤ _LLM_STRUCTURE_MAX_CHARS

    On any failure (empty output, validation miss, exception) returns the input
    unchanged with an empty ledger. Best-effort by design — never blocks the
    translation pipeline.
    """
    if not document_type or len(text) > _LLM_STRUCTURE_MAX_CHARS:
        return text, []

    # Rich extraction already produced headings — running the LLM structure pass
    # would be redundant and risks flattening what PyMuPDF markdown detected.
    if re.search(r'^#{1,3}\s', text, re.MULTILINE):
        return text, []

    # Late import to avoid a top-level cycle (doc_profiles imports DocumentType).
    from legal_agent.agents.translation.doc_profiles import resolve_profile

    profile = resolve_profile(document_type)
    if profile.layout_family != "court_filing":
        return text, []

    try:
        from langchain.chat_models import init_chat_model
        from langchain_core.messages import HumanMessage, SystemMessage

        llm = init_chat_model(
            "gemini-3.1-flash-lite-preview",
            model_provider="google-genai",
            max_tokens=8192,
        )
        system = _llm_structure_system_prompt()
        user = _llm_structure_user_prompt(text)

        response = await llm.ainvoke([
            SystemMessage(content=system),
            HumanMessage(content=user),
        ])
        raw = response.content
        if isinstance(raw, list):
            raw = "".join(
                part if isinstance(part, str) else part.get("text", "")
                for part in raw
            )

        structured_md, ledger = _parse_structure_response(raw)
        if not structured_md:
            return text, []

        if not _content_preserved(text, structured_md):
            logger.info("LLM structure pass dropped — content-preservation check failed")
            return text, []

        # Verify each ledger entry actually appears in the source text. Drop
        # the ones that don't (model hallucination); keep the rest.
        ledger = [e for e in ledger if e.text and e.text in text]

        return structured_md, ledger
    except Exception as exc:
        logger.warning(f"LLM structure pass failed, falling back to conservative: {exc}")
        return text, []


def _llm_structure_system_prompt() -> str:
    return f"""You are restructuring a court filing for downstream translation. Your ONLY job is to:

1. Identify the cause-title (court name + parties + case number) and put it as `# Heading`.
2. Identify major sections (FACTS, GROUNDS, PRAYER, VERIFICATION) and emit each as `## SECTION`.
3. Preserve numbered grounds as `1.`, `2.` (start each on its own line).
4. Preserve every other character of the source EXACTLY. Do NOT translate, paraphrase, summarise, or reorder content.
5. Build a `do_not_translate` ledger of spans the translator must preserve verbatim:
   citations (e.g. `Section 438 CrPC, 1973`, `(2023) 5 SCC 45`), FIR numbers, dates,
   monetary amounts, party names, statute references.

OUTPUT FORMAT — exactly this, no extra prose:

<structured markdown>

{_LEDGER_SENTINEL_OPEN}
{{"entries": [{{"kind": "citation", "text": "Section 438 CrPC, 1973", "note": ""}}, ...]}}
{_LEDGER_SENTINEL_CLOSE}

If no ledger entries: emit `{_LEDGER_SENTINEL_OPEN}\\n{{"entries": []}}\\n{_LEDGER_SENTINEL_CLOSE}`.

If you cannot identify a clear cause-title, return the input unchanged with an empty ledger.
"""


def _llm_structure_user_prompt(text: str) -> str:
    return f"Restructure the following court filing as instructed.\n\n---\n{text}\n---"


def _parse_structure_response(raw: str) -> tuple[str, list[LedgerEntry]]:
    """Split the LLM output at the sentinels. Returns ('', []) if malformed."""
    if _LEDGER_SENTINEL_OPEN not in raw:
        return raw.strip(), []

    head, _, tail = raw.partition(_LEDGER_SENTINEL_OPEN)
    json_blob, _, _ = tail.partition(_LEDGER_SENTINEL_CLOSE)

    structured_md = head.strip()
    ledger: list[LedgerEntry] = []
    try:
        parsed = json.loads(json_blob.strip() or "{}")
        for entry in parsed.get("entries", []) or []:
            kind = str(entry.get("kind", "other"))
            text = str(entry.get("text", "")).strip()
            note = str(entry.get("note", ""))
            if text:
                ledger.append(LedgerEntry(kind=kind, text=text, note=note))
    except Exception as exc:
        logger.debug(f"Ledger JSON parse failed: {exc}")

    return structured_md, ledger


def _content_preserved(source: str, structured: str) -> bool:
    """Token-overlap (Jaccard-style) sanity check. Catches the LLM rewriting,
    summarising, or translating instead of just adding structure markers."""
    src_tokens = set(_tokens(source))
    out_tokens = set(_tokens(structured))
    if not src_tokens:
        return False
    overlap = len(src_tokens & out_tokens) / len(src_tokens)
    if overlap < _TOKEN_OVERLAP_THRESHOLD:
        logger.debug(f"Token overlap {overlap:.2f} below threshold {_TOKEN_OVERLAP_THRESHOLD}")
        return False
    return True


def _tokens(text: str) -> list[str]:
    return [t for t in re.findall(r"\w+", text.lower()) if len(t) >= 2]
