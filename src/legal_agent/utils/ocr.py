"""Shared OCR utilities for extracting text from document images.

Backends:
- Gemini Vision — per-page image → LLM call. Good general accuracy, weaker on Indic scripts.
- Mistral Pixtral — per-page image → LLM call. Acts as the Gemini fallback (free tier).
- Sarvam Document Intelligence — async job API. 22 Indian languages + English. ≤10 pages/job
  (long PDFs are chunked and processed concurrently). Markdown only.

Output formats:
- "markdown": Structured (headings, bold, tables) — used for translation and drafting.
- "plain":    Flat transcription — used for RAG indexing.
- "html":     Structured HTML (paragraphs, headings, lists, tables) — used by the
              in-place document editor. Sarvam does not support HTML output.

Entry points:
- ocr_pdf(...) / ocr_image(...) — dispatch to the configured backend.
- ocr_pdf_with_{gemini,mistral,sarvam} / ocr_image_with_{gemini,mistral,sarvam} —
  force a specific backend (used by comparison scripts / explicit fallback wiring).
"""

import base64
import hashlib
import logging
import re
import tempfile
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Literal

import fitz  # PyMuPDF

from legal_agent.config import get_settings

logger = logging.getLogger(__name__)

OutputFormat = Literal["markdown", "plain", "html"]
Provider = Literal["gemini", "mistral", "sarvam"]

# Module-level S3 client for the OCR cache. Lazily constructed — avoids paying
# the boto3 init cost when caching is disabled or OCR is never called.
_s3_client_singleton = None


def _get_cache_s3_client():
    """Return a shared S3Client for cache reads/writes, or None if disabled or unconfigured."""
    global _s3_client_singleton
    settings = get_settings()
    if not settings.ocr_cache_enabled:
        return None
    if _s3_client_singleton is None:
        if not settings.s3_access_key or not settings.s3_secret_key:
            logger.debug("[ocr-cache] S3 credentials not set; cache disabled")
            return None
        from legal_agent.clients.s3_client import S3Client
        _s3_client_singleton = S3Client(settings)
    return _s3_client_singleton


_FORMAT_EXT = {"markdown": "md", "plain": "txt", "html": "html"}


def _cache_key(
    content_bytes: bytes,
    provider: str,
    output_format: str,
    language: str | None = None,
) -> tuple[str, str]:
    """Return (sha256_hex, full_s3_key) for a given input + config.

    `language` only varies the key for backends whose output depends on the hint
    (Sarvam); callers pass None for backends that ignore it.
    """
    sha = hashlib.sha256(content_bytes).hexdigest()
    settings = get_settings()
    ext = _FORMAT_EXT.get(output_format, "md")
    lang_seg = f"/{language}" if language else ""
    key = f"{settings.ocr_cache_prefix}/{provider}/{output_format}{lang_seg}/{sha}.{ext}"
    return sha, key


def _cache_lookup(s3_key: str, sha_short: str) -> str | None:
    """Try to fetch cached OCR text from S3. Returns None on miss or transient error."""
    client = _get_cache_s3_client()
    if client is None:
        return None
    try:
        if not client.head_sync(s3_key):
            logger.info(f"[ocr-cache] miss: {sha_short} (will OCR)")
            return None
        text = client.download_text_sync(s3_key)
        logger.info(f"[ocr-cache] hit: {sha_short} → {len(text)} chars")
        return text
    except Exception as exc:
        logger.warning(f"[ocr-cache] lookup failed, will OCR: {exc}")
        return None


def _cache_store(s3_key: str, sha_short: str, text: str) -> None:
    """Upload OCR output to the cache. Failures log a warning but do not raise."""
    client = _get_cache_s3_client()
    if client is None:
        return
    try:
        client.upload_text_sync(s3_key, text)
        logger.info(f"[ocr-cache] stored: {sha_short} ({len(text)} chars)")
    except Exception as exc:
        logger.warning(f"[ocr-cache] upload failed, returning OCR result anyway: {exc}")

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

_PROMPT_HTML = (
    "You are a legal document layout expert. Output clean, semantic HTML that "
    "preserves the visible structure of this page. Rules:\n"
    "- Use <h1>/<h2>/<h3> for headings (use visual hierarchy and font weight)\n"
    "- Use <p> for body paragraphs (one <p> per visual paragraph)\n"
    "- Use <ol>/<ul> with <li> for numbered/bulleted lists\n"
    "- Use <table>/<thead>/<tbody>/<tr>/<th>/<td> for tables\n"
    "- Use <strong> and <em> for bold and italic text\n"
    "- Use <br> only for hard line breaks inside a paragraph (e.g. addresses, signature blocks)\n"
    "- Preserve Hindi, Devanagari, and other Indic scripts exactly — do not translate or romanise\n"
    "- Preserve clause numbers, dates, case citations, statute references verbatim\n"
    "- Describe stamps and seals inline as <em>[STAMP: ...]</em> or <em>[SEAL: ...]</em>\n"
    "- Include handwritten notes in <em>[HANDWRITTEN: ...]</em>\n"
    "- Do NOT add or omit content. Only add structural tags around the existing text.\n"
    "- Do NOT include <html>, <head>, <body>, or <!DOCTYPE>. Output a fragment only.\n"
    "- Do NOT wrap output in markdown code fences. No commentary, no preamble."
)


def _select_prompt(output_format: OutputFormat) -> str:
    if output_format == "html":
        return _PROMPT_HTML
    if output_format == "plain":
        return _PROMPT_PLAIN
    return _PROMPT_MARKDOWN


def _strip_code_fence(text: str) -> str:
    """LLMs sometimes wrap output in ```html ... ``` despite the prompt; strip it."""
    s = text.strip()
    if s.startswith("```"):
        first_newline = s.find("\n")
        if first_newline != -1:
            s = s[first_newline + 1 :]
        if s.endswith("```"):
            s = s[: -3]
    return s.strip()


_SARVAM_MAX_PAGES_PER_JOB = 10  # API limit


# ──────────────────────────────────────────────────────────────────────────
# Router
# ──────────────────────────────────────────────────────────────────────────

def ocr_pdf(
    pdf_data: bytes,
    output_format: OutputFormat = "markdown",
    provider: Provider | None = None,
    language: str | None = None,
) -> str:
    """OCR a PDF using the configured backend, with a content-hashed S3 cache.

    The cache is keyed by sha256(pdf_data) + provider + output_format (+ language
    for Sarvam), so the same PDF bytes served to a second translation (or RAG
    index, or draft context) skip OCR entirely.

    Args:
        pdf_data: Raw PDF bytes.
        output_format: "markdown", "plain", or "html".
        provider: Force a specific backend. If None, uses settings.ocr_provider.
        language: Sarvam-only BCP-47 language hint (e.g. "hi-IN"). Falls back to
            settings.sarvam_ocr_language.
    """
    backend = provider or get_settings().ocr_provider
    cache_lang = language if backend == "sarvam" else None
    sha, key = _cache_key(pdf_data, backend, output_format, cache_lang)
    sha_short = f"{backend}/{output_format}/{sha[:12]}"

    cached = _cache_lookup(key, sha_short)
    if cached is not None:
        return cached

    if backend == "sarvam":
        text = ocr_pdf_with_sarvam(pdf_data, output_format, language=language)
    elif backend == "mistral":
        text = ocr_pdf_with_mistral(pdf_data, output_format)
    else:
        text = ocr_pdf_with_gemini(pdf_data, output_format)

    _cache_store(key, sha_short, text)
    return text


def ocr_image(
    image_bytes: bytes,
    mime_type: str = "image/png",
    output_format: OutputFormat = "markdown",
    provider: Provider | None = None,
) -> str:
    """OCR a single image using the configured backend, with S3 cache."""
    backend = provider or get_settings().ocr_provider
    sha, key = _cache_key(image_bytes, backend, output_format)
    sha_short = f"{backend}/{output_format}/{sha[:12]}"

    cached = _cache_lookup(key, sha_short)
    if cached is not None:
        return cached

    if backend == "sarvam":
        if output_format == "html":
            md = ocr_image_with_sarvam(image_bytes, mime_type, "markdown")
            text = _markdown_to_html(md)
        else:
            text = ocr_image_with_sarvam(image_bytes, mime_type, output_format)
    elif backend == "mistral":
        text = ocr_image_with_mistral(image_bytes, mime_type, output_format)
    else:
        text = ocr_image_with_gemini(image_bytes, mime_type, output_format)

    _cache_store(key, sha_short, text)
    return text


# Top-level bullet glyphs Sarvam (and other OCR engines) commonly emit instead
# of standard markdown `-`. All map to `- `.
_TOP_BULLETS = ("• ", "● ", "◦ ", "▪ ", "▫ ", "‣ ", "⦁ ", "· ", "• ", "·  ")
# Sub-bullet glyphs → indented `- ` so the parser nests them.
_SUB_BULLETS = ("– ", "— ", "‒ ", "⁃ ", "○ ")

# LaTeX math-mode bullet macros Sarvam emits when the source PDF was compiled
# from LaTeX (resumes / academic CVs). Each maps to a top-level `- ` marker.
_LATEX_BULLET_RE = re.compile(
    r"^(\s*)\$\\(circ|bullet|diamond|square|star|cdot|ast|triangleleft|triangleright)\$\s*",
    re.MULTILINE,
)
# `\text{X}` is LaTeX's text-in-math wrapper. Unwrap to bare X.
_LATEX_TEXT_RE = re.compile(r"\\text\{([^}]*)\}")
# `$ ... $` math delimiters that survived. Strip the dollar signs and keep
# the inner content. We strip them all (not just those with backslash
# commands) because Sarvam wraps numeric expressions like `$40K+$` too;
# legitimate dollar amounts in legal docs are normally written "Rs. 40,000"
# or "$40,000" without a closing `$` so collisions are rare.
_LATEX_DOLLAR_RE = re.compile(r"\$([^$\n]*)\$")
# Drop any orphan `\macro` artefacts left behind.
_LATEX_BARE_MACRO_RE = re.compile(r"\\([a-zA-Z]+)(?![a-zA-Z])")


def _strip_latex_artifacts(text: str) -> str:
    """Convert Sarvam's LaTeX residue to plain markdown.

    Order matters: line-start `$\\circ$` etc must be converted to `- ` BEFORE
    we unwrap `$...$`, otherwise they'd just become `\\circ` strays.
    """
    text = _LATEX_BULLET_RE.sub(r"\1- ", text)
    text = _LATEX_TEXT_RE.sub(r"\1", text)
    text = _LATEX_DOLLAR_RE.sub(r"\1", text)
    text = _LATEX_BARE_MACRO_RE.sub("", text)
    return text


def _normalize_ocr_markdown(md_text: str) -> str:
    """Convert OCR-emitted Unicode bullet glyphs and LaTeX residue to standard
    markdown markers.

    Without this, `markdown.markdown` treats lines like `• Foo` and
    `$\\circ$ Foo` as plain paragraphs because they don't match the list-marker
    grammar. Same for `–` / `—` sub-bullets. We also normalise leading
    whitespace (NBSP, tabs) so indentation is detected consistently.
    """
    md_text = _strip_latex_artifacts(md_text)

    out: list[str] = []
    for raw in md_text.splitlines():
        # Replace NBSP with regular space, tabs with two spaces, in leading whitespace.
        line = raw.replace(" ", " ").replace("\t", "  ")
        stripped = line.lstrip(" ")
        leading = line[: len(line) - len(stripped)]

        matched = False
        for glyph in _TOP_BULLETS:
            if stripped.startswith(glyph):
                out.append(f"{leading}- {stripped[len(glyph):]}")
                matched = True
                break
        if matched:
            continue
        for glyph in _SUB_BULLETS:
            if stripped.startswith(glyph):
                out.append(f"{leading}  - {stripped[len(glyph):]}")
                matched = True
                break
        if matched:
            continue
        out.append(line)
    return "\n".join(out)


def _markdown_to_html(md_text: str) -> str:
    """Render OCR-produced markdown to HTML for the in-place Tiptap editor.

    Sarvam (and other markdown-emitting OCR backends) commonly use Unicode
    bullet glyphs and unusual whitespace that the stock `markdown` parser does
    not recognise. We normalise via `_normalize_ocr_markdown` first.

    We deliberately do NOT enable `nl2br` — it injects `<br>` between every
    line of a list, which prevents the parser from merging consecutive `- `
    items into a single `<ul>`.
    """
    import markdown

    md_clean = _normalize_ocr_markdown(md_text)
    # DEBUG only: legal documents contain PII (party names, case numbers,
    # addresses), so the normalised content sample must not land in INFO logs.
    logger.debug("[ocr-md] sample after normalize: %r", md_clean[:400])
    return markdown.markdown(
        md_clean,
        extensions=["tables", "fenced_code"],
    )


# ──────────────────────────────────────────────────────────────────────────
# Gemini backend
# ──────────────────────────────────────────────────────────────────────────

def ocr_pdf_with_gemini(
    pdf_data: bytes,
    output_format: OutputFormat = "markdown",
) -> str:
    """OCR a PDF using Gemini Vision.

    Pages are rasterized sequentially (fast, CPU-bound) then sent to Gemini
    concurrently (bounded by gemini_ocr_concurrency). Output is assembled in
    original page order regardless of completion order.
    """
    from google import genai
    from google.genai import types

    settings = get_settings()
    client = genai.Client(api_key=settings.gemini_api_key or "")
    prompt = _select_prompt(output_format)

    doc = fitz.open(stream=pdf_data, filetype="pdf")
    total = doc.page_count

    page_images: list[bytes] = []
    for page_num in range(total):
        page = doc[page_num]
        pixmap = page.get_pixmap(matrix=fitz.Matrix(2, 2))
        page_images.append(pixmap.tobytes("png"))
    doc.close()

    def _ocr_page(idx_and_image: tuple[int, bytes]) -> tuple[int, str]:
        idx, image_bytes = idx_and_image
        try:
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
        except Exception as exc:
            # Re-raise with page index so the caller knows exactly which
            # page broke instead of seeing a generic Gemini error.
            raise RuntimeError(
                f"Gemini OCR failed on page {idx + 1}/{total}: {type(exc).__name__}: {exc}"
            ) from exc
        page_text = _strip_code_fence(response.text or "")
        if not page_text:
            raise RuntimeError(
                f"Gemini OCR returned empty text for page {idx + 1}/{total}. "
                "Possible causes: content filter, blank page rendered as empty, "
                "or max_output_tokens truncation."
            )
        if output_format == "html":
            page_text = f'<section data-page="{idx + 1}">\n{page_text}\n</section>'
        logger.info(
            f"[gemini-ocr] Page {idx + 1}/{total}: {len(page_text)} chars extracted"
        )
        return idx, page_text

    logger.info(
        f"[gemini-ocr] Processing {total} pages with concurrency="
        f"{settings.gemini_ocr_concurrency}"
    )
    results: list[str] = [""] * total
    with ThreadPoolExecutor(max_workers=settings.gemini_ocr_concurrency) as pool:
        for idx, text in pool.map(_ocr_page, enumerate(page_images)):
            results[idx] = text

    separator = "\n" if output_format == "html" else "\n\n"
    return separator.join(results)


def ocr_image_with_gemini(
    image_bytes: bytes,
    mime_type: str = "image/png",
    output_format: OutputFormat = "markdown",
) -> str:
    """OCR a single image using Gemini Vision."""
    from google import genai
    from google.genai import types

    settings = get_settings()
    client = genai.Client(api_key=settings.gemini_api_key or "")
    prompt = _select_prompt(output_format)

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
    return _strip_code_fence(response.text or "")


# ──────────────────────────────────────────────────────────────────────────
# Mistral backend (Pixtral; secondary path / Gemini fallback)
# ──────────────────────────────────────────────────────────────────────────

def ocr_pdf_with_mistral(
    pdf_data: bytes,
    output_format: OutputFormat = "markdown",
) -> str:
    """OCR a PDF using Mistral Pixtral.

    Mirrors `ocr_pdf_with_gemini`: rasterise each page (PyMuPDF), send images
    concurrently to Mistral, reassemble in original page order.
    """
    from mistralai.client import Mistral

    settings = get_settings()
    if not settings.mistral_api_key:
        raise RuntimeError(
            "MISTRAL_API_KEY is not configured but ocr_provider='mistral'. "
            "Set MISTRAL_API_KEY in .env or switch OCR_PROVIDER=gemini."
        )

    client = Mistral(api_key=settings.mistral_api_key)
    prompt = _select_prompt(output_format)

    doc = fitz.open(stream=pdf_data, filetype="pdf")
    total = doc.page_count
    page_images: list[bytes] = []
    for page_num in range(total):
        pixmap = doc[page_num].get_pixmap(matrix=fitz.Matrix(2, 2))
        page_images.append(pixmap.tobytes("png"))
    doc.close()

    def _ocr_page(idx_and_image: tuple[int, bytes]) -> tuple[int, str]:
        idx, image_bytes = idx_and_image
        b64 = base64.b64encode(image_bytes).decode("ascii")
        try:
            response = client.chat.complete(
                model=settings.mistral_vision_model,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {
                                "type": "image_url",
                                "image_url": f"data:image/png;base64,{b64}",
                            },
                        ],
                    }
                ],
                temperature=0.1,
            )
        except Exception as exc:
            raise RuntimeError(
                f"Mistral OCR failed on page {idx + 1}/{total}: {type(exc).__name__}: {exc}"
            ) from exc
        page_text = _strip_code_fence(response.choices[0].message.content or "")
        if not page_text:
            raise RuntimeError(
                f"Mistral OCR returned empty text for page {idx + 1}/{total}."
            )
        if output_format == "html":
            page_text = f'<section data-page="{idx + 1}">\n{page_text}\n</section>'
        logger.info(
            f"[mistral-ocr] Page {idx + 1}/{total}: {len(page_text)} chars extracted"
        )
        return idx, page_text

    logger.info(
        f"[mistral-ocr] Processing {total} pages with concurrency="
        f"{settings.mistral_ocr_concurrency}"
    )
    results: list[str] = [""] * total
    with ThreadPoolExecutor(max_workers=settings.mistral_ocr_concurrency) as pool:
        for idx, text in pool.map(_ocr_page, enumerate(page_images)):
            results[idx] = text

    separator = "\n" if output_format == "html" else "\n\n"
    return separator.join(results)


def ocr_image_with_mistral(
    image_bytes: bytes,
    mime_type: str = "image/png",
    output_format: OutputFormat = "markdown",
) -> str:
    """OCR a single image using Mistral Pixtral."""
    from mistralai.client import Mistral

    settings = get_settings()
    if not settings.mistral_api_key:
        raise RuntimeError(
            "MISTRAL_API_KEY is not configured but ocr_provider='mistral'."
        )

    client = Mistral(api_key=settings.mistral_api_key)
    prompt = _select_prompt(output_format)
    b64 = base64.b64encode(image_bytes).decode("ascii")

    response = client.chat.complete(
        model=settings.mistral_vision_model,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": f"data:{mime_type};base64,{b64}"},
                ],
            }
        ],
        temperature=0.1,
    )
    return _strip_code_fence(response.choices[0].message.content or "")


# ──────────────────────────────────────────────────────────────────────────
# Sarvam backend
# ──────────────────────────────────────────────────────────────────────────

def ocr_pdf_with_sarvam(
    pdf_data: bytes,
    output_format: OutputFormat = "markdown",
    language: str | None = None,
) -> str:
    """OCR a PDF using Sarvam Document Intelligence (Sarvam Vision VLM).

    The API caps input at 10 pages per job, so longer PDFs are split into
    chunks and processed concurrently (bounded by `sarvam_ocr_concurrency`).

    `output_format` is honored natively: "html" requests HTML from Sarvam,
    "markdown"/"plain" requests markdown. `language` overrides the configured
    `sarvam_ocr_language` env default.
    """
    chunks = _chunk_pdf_by_pages(pdf_data, _SARVAM_MAX_PAGES_PER_JOB)
    total_pages = sum(c["page_count"] for c in chunks)
    logger.info(
        f"[sarvam-ocr] {total_pages} pages → {len(chunks)} chunks "
        f"(concurrency={get_settings().sarvam_ocr_concurrency}, "
        f"format={output_format}, language={language or get_settings().sarvam_ocr_language})"
    )

    settings = get_settings()

    def _run_indexed(idx_and_chunk: tuple[int, dict]) -> tuple[int, str]:
        idx, c = idx_and_chunk
        try:
            text = _run_sarvam_pdf_job(
                c["bytes"], c["page_count"], output_format=output_format, language=language,
            )
        except Exception as exc:
            raise RuntimeError(
                f"Sarvam OCR failed on chunk {idx + 1}/{len(chunks)} "
                f"({c['page_count']} pages): {type(exc).__name__}: {exc}"
            ) from exc
        if not text.strip():
            raise RuntimeError(
                f"Sarvam OCR returned empty output for chunk {idx + 1}/{len(chunks)} "
                f"({c['page_count']} pages)."
            )
        return idx, text

    results_ordered: list[str] = [""] * len(chunks)
    with ThreadPoolExecutor(max_workers=settings.sarvam_ocr_concurrency) as pool:
        for idx, text in pool.map(_run_indexed, enumerate(chunks)):
            results_ordered[idx] = text

    return "\n\n".join(results_ordered)


def ocr_image_with_sarvam(
    image_bytes: bytes,
    mime_type: str = "image/png",
    output_format: OutputFormat = "markdown",
) -> str:
    """OCR a single image using Sarvam Document Intelligence."""
    ext_map = {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/tiff": ".tiff",
        "image/webp": ".webp",
    }
    ext = ext_map.get(mime_type, ".png")
    return _run_sarvam_job(image_bytes, ext, page_hint=1)


# ──────────────────────────────────────────────────────────────────────────
# Sarvam helpers
# ──────────────────────────────────────────────────────────────────────────

def _chunk_pdf_by_pages(pdf_data: bytes, max_pages: int) -> list[dict]:
    """Split a PDF into sub-PDFs of at most `max_pages` pages each.

    Returns a list of {"bytes": ..., "page_count": ...} dicts.
    """
    doc = fitz.open(stream=pdf_data, filetype="pdf")
    chunks: list[dict] = []
    try:
        for start in range(0, doc.page_count, max_pages):
            end = min(start + max_pages, doc.page_count) - 1
            sub = fitz.open()
            try:
                sub.insert_pdf(doc, from_page=start, to_page=end)
                chunks.append({"bytes": sub.tobytes(), "page_count": end - start + 1})
            finally:
                sub.close()
    finally:
        doc.close()
    return chunks


def _run_sarvam_pdf_job(
    pdf_bytes: bytes,
    page_count: int,
    output_format: OutputFormat = "markdown",
    language: str | None = None,
) -> str:
    return _run_sarvam_job(
        pdf_bytes, ".pdf", page_hint=page_count,
        output_format=output_format, language=language,
    )


# Sarvam Document Intelligence expects "md" or "html"; our internal vocabulary is
# "markdown"/"plain"/"html". Map to what the API accepts ("plain" → md, then
# strip structure on the consumer side if needed).
_SARVAM_DI_FORMAT = {"markdown": "md", "plain": "md", "html": "html"}
_SARVAM_DI_EXT = {"md": ".md", "html": ".html"}


def _run_sarvam_job(
    file_bytes: bytes,
    file_ext: str,
    page_hint: int,
    output_format: OutputFormat = "markdown",
    language: str | None = None,
) -> str:
    """Run a single Sarvam Document Intelligence job and return extracted text.

    Writes the input to a temp file (SDK accepts a file path), creates a job,
    uploads, waits for completion, downloads the output ZIP, and extracts the
    contents matching the requested output format.
    """
    from sarvamai import SarvamAI  # type: ignore

    settings = get_settings()
    if not settings.sarvam_api_key:
        raise RuntimeError(
            "SARVAM_API_KEY is not configured but ocr_provider='sarvam'. "
            "Set SARVAM_API_KEY in .env or switch OCR_PROVIDER=gemini."
        )

    client = SarvamAI(api_subscription_key=settings.sarvam_api_key)
    di_format = _SARVAM_DI_FORMAT.get(output_format, "md")
    di_ext = _SARVAM_DI_EXT[di_format]
    lang = language or settings.sarvam_ocr_language

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        in_path = tmp / f"input{file_ext}"
        out_path = tmp / "output.zip"
        in_path.write_bytes(file_bytes)

        job = client.document_intelligence.create_job(
            language=lang,
            output_format=di_format,
        )
        job.upload_file(str(in_path))
        job.start()
        job.wait_until_complete()
        job.download_output(str(out_path))

        text = _extract_text_from_zip(out_path, di_ext)
        logger.info(f"[sarvam-ocr] Job complete: {page_hint} pages → {len(text)} chars")
        return text


def _extract_text_from_zip(zip_path: Path, ext: str) -> str:
    """Read all files with `ext` from a Sarvam output ZIP and concatenate them."""
    parts: list[str] = []
    with zipfile.ZipFile(zip_path) as zf:
        names = sorted(n for n in zf.namelist() if n.lower().endswith(ext))
        if not names:
            raise RuntimeError(
                f"Sarvam output ZIP contained no {ext} files (entries: {zf.namelist()})"
            )
        for name in names:
            with zf.open(name) as f:
                parts.append(f.read().decode("utf-8", errors="replace"))
    return "\n\n".join(parts).strip()
