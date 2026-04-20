"""Shared OCR utilities for extracting text from document images.

Backends:
- Gemini Vision — per-page image → LLM call. Good general accuracy, weaker on Indic scripts.
- Sarvam Document Intelligence — async job API. 22 Indian languages + English. ≤10 pages/job
  (long PDFs are chunked and processed concurrently).

Output formats:
- "markdown": Structured (headings, bold, tables) — used for translation and drafting.
- "plain": Flat transcription — used for RAG indexing.

Entry points:
- ocr_pdf(...) / ocr_image(...) — dispatch to the configured backend.
- ocr_pdf_with_gemini / ocr_image_with_gemini — force Gemini (comparison scripts, fallback).
- ocr_pdf_with_sarvam / ocr_image_with_sarvam — force Sarvam.
"""

import hashlib
import logging
import tempfile
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Literal

import fitz  # PyMuPDF

from legal_agent.config import get_settings

logger = logging.getLogger(__name__)

OutputFormat = Literal["markdown", "plain"]
Provider = Literal["gemini", "sarvam"]

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


def _cache_key(content_bytes: bytes, provider: str, output_format: str) -> tuple[str, str]:
    """Return (sha256_hex, full_s3_key) for a given input + config."""
    sha = hashlib.sha256(content_bytes).hexdigest()
    settings = get_settings()
    key = f"{settings.ocr_cache_prefix}/{provider}/{output_format}/{sha}.md"
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

_SARVAM_MAX_PAGES_PER_JOB = 10  # API limit


# ──────────────────────────────────────────────────────────────────────────
# Router
# ──────────────────────────────────────────────────────────────────────────

def ocr_pdf(
    pdf_data: bytes,
    output_format: OutputFormat = "markdown",
    provider: Provider | None = None,
) -> str:
    """OCR a PDF using the configured backend, with a content-hashed S3 cache.

    The cache is keyed by sha256(pdf_data) + provider + output_format, so the
    same PDF bytes served to a second translation (or RAG index, or draft
    context) skip OCR entirely.

    Args:
        pdf_data: Raw PDF bytes.
        output_format: "markdown" or "plain".
        provider: Force a specific backend. If None, uses settings.ocr_provider.
    """
    backend = provider or get_settings().ocr_provider
    sha, key = _cache_key(pdf_data, backend, output_format)
    sha_short = f"{backend}/{output_format}/{sha[:12]}"

    cached = _cache_lookup(key, sha_short)
    if cached is not None:
        return cached

    if backend == "sarvam":
        text = ocr_pdf_with_sarvam(pdf_data, output_format)
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
        text = ocr_image_with_sarvam(image_bytes, mime_type, output_format)
    else:
        text = ocr_image_with_gemini(image_bytes, mime_type, output_format)

    _cache_store(key, sha_short, text)
    return text


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
    prompt = _PROMPT_MARKDOWN if output_format == "markdown" else _PROMPT_PLAIN

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
        page_text = (response.text or "").strip()
        if not page_text:
            raise RuntimeError(
                f"Gemini OCR returned empty text for page {idx + 1}/{total}. "
                "Possible causes: content filter, blank page rendered as empty, "
                "or max_output_tokens truncation."
            )
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
        # pool.map re-raises exceptions as the iterator advances; wrapping
        # the worker with a page-indexed RuntimeError above guarantees the
        # lawyer-facing error names the exact page that broke.
        for idx, text in pool.map(_ocr_page, enumerate(page_images)):
            results[idx] = text

    return "\n\n".join(results)


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


# ──────────────────────────────────────────────────────────────────────────
# Sarvam backend
# ──────────────────────────────────────────────────────────────────────────

def ocr_pdf_with_sarvam(
    pdf_data: bytes,
    output_format: OutputFormat = "markdown",
) -> str:
    """OCR a PDF using Sarvam Document Intelligence.

    The API caps input at 10 pages per job, so longer PDFs are split into
    chunks and processed concurrently (bounded by `sarvam_ocr_concurrency`).

    `output_format` controls the return shape but Sarvam only emits markdown;
    for "plain" we return the markdown as-is (downstream RAG chunking tolerates
    markdown structure fine).
    """
    chunks = _chunk_pdf_by_pages(pdf_data, _SARVAM_MAX_PAGES_PER_JOB)
    total_pages = sum(c["page_count"] for c in chunks)
    logger.info(
        f"[sarvam-ocr] {total_pages} pages → {len(chunks)} chunks "
        f"(concurrency={get_settings().sarvam_ocr_concurrency})"
    )

    settings = get_settings()

    def _run_indexed(idx_and_chunk: tuple[int, dict]) -> tuple[int, str]:
        idx, c = idx_and_chunk
        try:
            text = _run_sarvam_pdf_job(c["bytes"], c["page_count"])
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


def _run_sarvam_pdf_job(pdf_bytes: bytes, page_count: int) -> str:
    return _run_sarvam_job(pdf_bytes, ".pdf", page_hint=page_count)


def _run_sarvam_job(file_bytes: bytes, file_ext: str, page_hint: int) -> str:
    """Run a single Sarvam Document Intelligence job and return extracted markdown.

    Writes the input to a temp file (SDK accepts a file path), creates a job,
    uploads, waits for completion, downloads the output ZIP, and extracts the
    markdown contents.
    """
    from sarvamai import SarvamAI  # type: ignore

    settings = get_settings()
    if not settings.sarvam_api_key:
        raise RuntimeError(
            "SARVAM_API_KEY is not configured but ocr_provider='sarvam'. "
            "Set SARVAM_API_KEY in .env or switch OCR_PROVIDER=gemini."
        )

    client = SarvamAI(api_subscription_key=settings.sarvam_api_key)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        in_path = tmp / f"input{file_ext}"
        out_path = tmp / "output.zip"
        in_path.write_bytes(file_bytes)

        job = client.document_intelligence.create_job(
            language=settings.sarvam_ocr_language,
            output_format="md",
        )
        job.upload_file(str(in_path))
        job.start()
        job.wait_until_complete()
        job.download_output(str(out_path))

        text = _extract_markdown_from_zip(out_path)
        logger.info(f"[sarvam-ocr] Job complete: {page_hint} pages → {len(text)} chars")
        return text


def _extract_markdown_from_zip(zip_path: Path) -> str:
    """Read all .md files from a Sarvam output ZIP and concatenate them in sorted order."""
    parts: list[str] = []
    with zipfile.ZipFile(zip_path) as zf:
        md_names = sorted(n for n in zf.namelist() if n.lower().endswith(".md"))
        if not md_names:
            raise RuntimeError(
                f"Sarvam output ZIP contained no .md files (entries: {zf.namelist()})"
            )
        for name in md_names:
            with zf.open(name) as f:
                parts.append(f.read().decode("utf-8", errors="replace"))
    return "\n\n".join(parts).strip()
