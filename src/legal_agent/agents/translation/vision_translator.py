"""Vision-LLM single-pass translation for scanned (image-only) PDFs.

For each page, rasterize to a size-bounded JPEG and send to a vision LLM (Claude Sonnet 4 by
default). Two modes:

- Structured layout (default): model returns JSON blocks with typography hints → HTML with
  `.vt-*` classes for spacing, weight, size, and legal letterhead roles.
- Legacy: model returns semantic HTML inside a ``<section>`` (older prompt).

Per-page output is cached in S3 keyed by SHA256(image + lang + model + prompt version).
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import re
import time
from typing import TYPE_CHECKING

import fitz

from legal_agent.utils.ocr import (
    _cache_lookup,
    _cache_store,
    _strip_code_fence,
)

if TYPE_CHECKING:
    from legal_agent.models.requests import CreateTranslationJobRequest

logger = logging.getLogger(__name__)


PROMPT_VERSION_LEGACY = "vision-translate-v3-simple-hindi-layout"
# v5 splits the pipeline: vision is used for OCR + layout only (output is in
# the source language), and translation runs separately through Sarvam. The
# version bump invalidates the per-page S3 cache from the old v4 single-pass
# prompt cleanly.
PROMPT_VERSION_STRUCTURED = "vision-ocr-v5-then-sarvam"

# Anthropic enforces a 5 MiB limit on the base64 image payload. Keep a margin
# below the hard limit so SDK wrapping never crosses the boundary.
_MAX_ANTHROPIC_IMAGE_BASE64_BYTES = 4_900_000
_RASTER_DPI_STEPS = (200, 180, 160, 140, 120, 100)
_JPEG_QUALITY_STEPS = (88, 80, 72, 64, 56)
_VISION_RETRY_MAX_ATTEMPTS = 4
_VISION_RETRY_BASE_SECONDS = 1.5

_SECTION_RE = re.compile(r"<section\b[^>]*>.*?</section>", re.IGNORECASE | re.DOTALL)

# Inline HTML tags (and a broader pattern for legacy fallback) are tokenized
# to [__VTAG_NNNN__] sentinels before being sent to Sarvam, then restored
# after translation. Uses the same `[__..._NNNN__]` family as the existing
# `[__NNNN__]` (glossary) and `[__SEP_NNNN__]` (batch) sentinels, which
# Sarvam preserves verbatim in formal mode.
_INLINE_TAG_RE = re.compile(r"</?(?:strong|em|u)\s*>|<br\s*/?>", re.IGNORECASE)
_HTML_ANY_TAG_RE = re.compile(r"<[^>]+>")
_VTAG_TEMPLATE = "[__VTAG_{:04d}__]"
_VTAG_RE = re.compile(r"\[__VTAG_(\d{4})__\]")


def _protect_tags(text: str, pattern: re.Pattern[str]) -> tuple[str, list[str]]:
    if not text:
        return text, []
    tags: list[str] = []

    def _repl(m: re.Match[str]) -> str:
        i = len(tags)
        tags.append(m.group(0))
        return _VTAG_TEMPLATE.format(i)

    return pattern.sub(_repl, text), tags


def _restore_tags(text: str, tags: list[str]) -> str:
    if not tags or not text:
        return text

    def _repl(m: re.Match[str]) -> str:
        i = int(m.group(1))
        return tags[i] if i < len(tags) else m.group(0)

    return _VTAG_RE.sub(_repl, text)

_LANG_LABELS = {
    "hindi": "Hindi (Devanagari script)",
    "marathi": "Marathi (Devanagari script)",
    "tamil": "Tamil",
    "telugu": "Telugu",
    "bengali": "Bengali",
    "gujarati": "Gujarati",
    "kannada": "Kannada",
    "malayalam": "Malayalam",
    "punjabi": "Punjabi",
    "urdu": "Urdu",
}


_PROMPT_LEGACY_HTML = """\
OCR this scanned page into HTML. DO NOT TRANSLATE. Transcribe into the same
language as the source — a downstream translator handles language conversion.

Return only one HTML fragment:
<section data-page="{page_no}">...</section>

No markdown fences. No explanation.

Main task:
- Transcribe every readable line on the page — titles, body, footnotes,
  page headers, page numbers, captions, signatures.
- Preserve the visual layout using simple HTML.
- Wrap visibly italicized spans (case names, titles, foreign words) in <em>.

Layout rules:
- Centered title: <h1 class="center"> or <p class="center">
- Author byline: <p class="center"><em>...</em></p>
- Page header (running top-of-page band): <p class="page-header">...</p>
- Page number (standalone numeric foot): <p class="page-number center">...</p>
- Right-aligned items: <p class="right">
- Same-line left/right items:
  <div class="row"><div class="col-left">...</div><div class="col-right">...</div></div>
- Subject line: <p><strong>...:</strong> ...</p>
- Body paragraphs: <p>...</p>
- Block quotes: <blockquote>...</blockquote>
- Footnotes: <p class="footnote">...</p> (one per footnote, preserve the leading marker)
- Use <strong>, <u>, <em> only where visually needed.
- Do not create placeholder boxes for logos, seals, stamps, or signatures —
  omit them rather than writing [Seal], [Logo], or [Signature].

Fidelity:
- Preserve numerals, dates, citations, identifiers, URLs, emails verbatim.
- Do not invent text that is not visible.
"""


_PROMPT_STRUCTURED_JSON = """\
You are an OCR + layout extractor for a single scanned page. DO NOT TRANSLATE.
Output the text you see in the SAME LANGUAGE as the source — a downstream
specialist translator will handle translation in a second pass. Your job is
to faithfully transcribe the page content and capture its visual structure.

Return ONLY valid JSON (no markdown fences, no explanation). Exact keys:

{{
  "page": {page_no},
  "register": "government_legal" | "general",
  "blocks": [
    {{
      "type": "text",
      "role": "title",
      "align": "center",
      "weight": "bold",
      "size": "large",
      "line_spacing": "tight",
      "html": "Source-language text exactly as printed; inline tags only: <strong>, <em>, <u>, <br/>"
    }},
    {{
      "type": "row",
      "role": "meta_row",
      "weight": "normal",
      "size": "normal",
      "left_html": "Left-column fragment, source language",
      "right_html": "Right-column fragment, source language"
    }}
  ]
}}

Register classification (pick exactly one for the document; on page 1 this
sets it for downstream pages):
- "government_legal": Indian administrative / legal / tax / regulatory pages
  (DIN, F.NO, CBIC, GST, Section references, letterhead, formal "To"/"सेवा में"
  salutation, departmental signatures).
- "general": Everything else — academic papers, journal articles, books,
  resumes, business letters, news. When in doubt, choose "general".

Allowed block shapes:
1) Text block:
   - type: "text"
   - role: one of
     title | author | page_header | page_number | body | footnote | block_quote | caption
     | letterhead | meta_row | subject | body_clause | signature_block | footer | general
   - align: left | center | right | justify
   - weight: normal | semibold | bold (match stroke weight visually)
   - size: xs | small | normal | large | xlarge (xs/small for footnotes/fine print, large/xlarge for titles)
   - line_spacing: tight | normal | relaxed
   - html: source-language text exactly as printed; inline tags ONLY <strong>, <em>, <u>, <br/>.
     Use <em> for visibly italicized text (case names, titles, foreign words).

2) Row block (same horizontal line split):
   - type: "row"
   - role: usually meta_row
   - left_html, right_html: source-language fragments

Role guidance:
- title: the document or article title (often top-centered, large).
- author: byline immediately under the title (often italic, centered).
- page_header: running header at top of body pages (e.g. "JOURNAL OF CONSTITUTIONAL LAW [Vol. 1: 2]").
- page_number: standalone numeric page marker (e.g. "325" centered at the foot, or in the header band).
- body: main running paragraphs.
- footnote: numbered footnotes below the body, usually separated by a thin rule and in smaller type.
  Emit one block per footnote; preserve the leading marker (e.g. "1", "12") as part of html.
- block_quote: indented quoted paragraphs offset from the body.
- caption: figure / table captions.
- letterhead / meta_row / subject / body_clause / signature_block / footer:
  for Indian government / legal pages.

OCR fidelity rules:
- Transcribe every printed line into some block — headers, page numbers,
  footnotes, signatures, all of it. Missing content is a defect.
- Preserve numerals, dates, footnote markers, citations, identifiers, URLs,
  emails, and section references EXACTLY as printed.
- For italicized fragments inside otherwise upright text (e.g. case names like
  "Printz v. United States" in body text), wrap only the italic span in <em>.
- Do NOT translate. Do NOT paraphrase. Do NOT summarize.
- Do NOT emit placeholders for seals, stamps, logos, or signatures — omit them.
- Do NOT invent text that is not visible on the page.

"""


async def translate_scanned_pdf_via_vision(
    source_bytes: bytes,
    request: "CreateTranslationJobRequest",
    job_id: str,
    debug_dir: str | None = None,
) -> tuple[bytes, dict]:
    """Translate an image-only PDF via Claude vision.

    Returns (pdf_bytes, metadata_dict).
    """
    from legal_agent.agents.translation.html_pdf_translator import (
        _dump,
        render_html_to_pdf_bytes,
    )
    from legal_agent.agents.translation.layout_render import wrap_pages_html
    from legal_agent.config import get_settings

    settings = get_settings()
    lang = request.target_language.value
    target_language = _target_language_label(lang)
    model = settings.vision_translation_model
    concurrency = max(1, settings.vision_translation_concurrency)
    structured = settings.vision_translation_structured_layout
    prompt_ver = PROMPT_VERSION_STRUCTURED if structured else PROMPT_VERSION_LEGACY

    logger.info(
        "[%s] image-only PDF → vision LLM (model=%s, concurrency=%d, structured=%s)",
        job_id, model, concurrency, structured,
    )

    t_raster = time.perf_counter()
    page_images = await asyncio.to_thread(_rasterize_pages, source_bytes)
    logger.info(
        "[%s] rasterized %d page(s) for vision in %.2fs",
        job_id, len(page_images), time.perf_counter() - t_raster,
    )

    t_vision = time.perf_counter()
    sem = asyncio.Semaphore(concurrency)
    tasks = [
        _translate_page(
            sem=sem,
            page_png=page_images[i],
            page_no=i + 1,
            total_pages=len(page_images),
            lang=lang,
            target_language=target_language,
            model=model,
            structured_layout=structured,
            prompt_version=prompt_ver,
            debug_dir=debug_dir,
            job_id=job_id,
        )
        for i in range(len(page_images))
    ]
    results = await asyncio.gather(*tasks)
    cache_hits = sum(1 for _, hit, _ in results if hit)

    fidelity_agg = _aggregate_fidelity([f for _, _, f in results])

    logger.info(
        "[%s] vision translation took %.2fs (%d cache hits / %d pages)",
        job_id, time.perf_counter() - t_vision, cache_hits, len(results),
    )

    final_html = wrap_pages_html([html for html, _, _ in results], lang)
    _dump(debug_dir, job_id, "4_final", final_html)
    if structured and debug_dir and settings.vision_translation_debug_fidelity_files:
        _dump(
            debug_dir,
            job_id,
            "vision_fidelity_summary",
            json.dumps(fidelity_agg, indent=2),
            ext=".json",
        )

    t_pdf = time.perf_counter()
    pdf_bytes = await asyncio.to_thread(render_html_to_pdf_bytes, final_html)
    logger.info("[%s] PDF render took %.2fs", job_id, time.perf_counter() - t_pdf)

    meta = {
        "pages": len(page_images),
        "translation_backend": "sarvam",
        "ocr_backend": f"claude_vision_ocr_only ({model})",
        "vision_cache_hits": cache_hits,
        "translation_pipeline": "vision_ocr_then_sarvam",
        "scanned_translation_mode": "vision_reconstruct",
        "vision_structured_layout": structured,
        "vision_translation_prompt_version": prompt_ver,
        "vision_fidelity_aggregate": fidelity_agg,
    }
    return pdf_bytes, meta


def _aggregate_fidelity(per_page: list[dict | None]) -> dict:
    roles: dict[str, int] = {}
    total_blocks = 0
    emphasis = 0
    centered = 0
    pages_with_data = 0
    checklist_merge = {
        "has_letterhead_role": False,
        "has_meta_or_row": False,
        "has_subject_role": False,
        "has_body_clause_role": False,
    }
    for fp in per_page:
        if not fp:
            continue
        pages_with_data += 1
        total_blocks += int(fp.get("vision_fidelity_block_count") or 0)
        emphasis += int(fp.get("vision_fidelity_emphasis_blocks") or 0)
        centered += int(fp.get("vision_fidelity_centered_text_blocks") or 0)
        for k, v in (fp.get("vision_fidelity_roles") or {}).items():
            roles[k] = roles.get(k, 0) + int(v)
        ch = fp.get("vision_fidelity_checklist") or {}
        for key in checklist_merge:
            checklist_merge[key] = checklist_merge[key] or bool(ch.get(key))
    return {
        "pages_with_fidelity_metrics": pages_with_data,
        "vision_fidelity_block_count_total": total_blocks,
        "vision_fidelity_roles": roles,
        "vision_fidelity_emphasis_blocks_total": emphasis,
        "vision_fidelity_centered_text_blocks_total": centered,
        "vision_fidelity_checklist": checklist_merge,
        "qa_note": "Use checklist + role histogram for A/B vs competitor output.",
    }


def _target_language_label(lang: str) -> str:
    return _LANG_LABELS.get(lang.lower(), lang)


def _rasterize_pages(pdf_bytes: bytes) -> list[bytes]:
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    images: list[bytes] = []
    try:
        for page in doc:
            images.append(_render_page_under_anthropic_limit(page))
    finally:
        doc.close()
    return images


def _render_page_under_anthropic_limit(page) -> bytes:
    best: bytes | None = None
    best_encoded_size = 0
    for dpi in _RASTER_DPI_STEPS:
        scale = dpi / 72.0
        pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
        for quality in _JPEG_QUALITY_STEPS:
            data = pix.tobytes("jpeg", jpg_quality=quality)
            best = data
            best_encoded_size = _base64_size(len(data))
            if best_encoded_size <= _MAX_ANTHROPIC_IMAGE_BASE64_BYTES:
                return data
        logger.info(
            "[vision-raster] page image at %sdpi exceeded limit after compression "
            "(%d base64 bytes)",
            dpi,
            best_encoded_size,
        )
    if best is None:
        raise RuntimeError("Failed to rasterize page for vision translation")
    raise ValueError(
        "Rendered page exceeds Anthropic's 5 MiB base64 image limit after "
        f"compression ({best_encoded_size} base64 bytes)"
    )


def _base64_size(byte_count: int) -> int:
    return ((byte_count + 2) // 3) * 4


async def _translate_structured_page_via_sarvam(
    page,
    target_language: str,
    job_id: str | None,
    page_no: int,
):
    """Translate every block's source-language text through Sarvam.

    Inline HTML tags (<strong>, <em>, <u>, <br/>) are tokenized to
    [__VTAG_NNNN__] sentinels before translation and restored after, so
    italicized case names and bold spans survive intact.
    """
    from legal_agent.agents.translation.layout_ir import VisionStyledRowBlock
    from legal_agent.agents.translation.sarvam_translate import SARVAM_LANG_CODES
    from legal_agent.agents.translation.source_cleanup import clean_source_text
    from legal_agent.agents.translation.translator import Translator

    blocks = list(page.blocks)
    items: list[tuple[int, str, list[str]]] = []  # (block_idx, side, tags)
    inputs: list[str] = []

    def _prepare(raw: str) -> tuple[str, list[str]]:
        # Inline-tag protection first so cleanup never sees HTML; THEN run
        # deterministic source cleanup on the residual text (NFC, mid-word
        # repair, ngram dedup, whitespace collapse).
        protected, tags = _protect_tags(raw, _INLINE_TAG_RE)
        cleaned, _report = clean_source_text(protected)
        return cleaned, tags

    for i, b in enumerate(blocks):
        if isinstance(b, VisionStyledRowBlock):
            for side, raw in (("left", b.left_html), ("right", b.right_html)):
                if raw and raw.strip():
                    cleaned, tags = _prepare(raw)
                    items.append((i, side, tags))
                    inputs.append(cleaned)
        else:
            raw = b.html or ""
            if raw.strip():
                cleaned, tags = _prepare(raw)
                items.append((i, "html", tags))
                inputs.append(cleaned)

    if not inputs:
        return page

    target_code = SARVAM_LANG_CODES.get(target_language.lower(), "hi-IN")
    source_code = SARVAM_LANG_CODES.get("english", "en-IN")
    translator = Translator(target_language, model="sarvam")
    translated = await translator.translate_batch(inputs, source_code, target_code)
    if len(translated) != len(inputs):
        logger.warning(
            "[%s] page %s: sarvam returned %d outputs for %d inputs — keeping source",
            job_id or "?", page_no, len(translated), len(inputs),
        )
        return page

    for (i, side, tags), tr in zip(items, translated):
        restored = _restore_tags(tr or "", tags)
        b = blocks[i]
        if isinstance(b, VisionStyledRowBlock):
            if side == "left":
                blocks[i] = b.model_copy(update={"left_html": restored})
            else:
                blocks[i] = b.model_copy(update={"right_html": restored})
        else:
            blocks[i] = b.model_copy(update={"html": restored})

    return page.model_copy(update={"blocks": blocks})


async def _translate_legacy_section_html_via_sarvam(
    section_html: str,
    target_language: str,
    job_id: str | None,
    page_no: int,
) -> str:
    """Fallback path: translate a whole section's text by tokenizing every
    HTML tag (not just inline) into sentinels, sending the residue through
    Sarvam, and restoring tags after.
    """
    from legal_agent.agents.translation.sarvam_translate import SARVAM_LANG_CODES
    from legal_agent.agents.translation.translator import Translator

    if not section_html or not section_html.strip():
        return section_html

    protected, tags = _protect_tags(section_html, _HTML_ANY_TAG_RE)
    target_code = SARVAM_LANG_CODES.get(target_language.lower(), "hi-IN")
    source_code = SARVAM_LANG_CODES.get("english", "en-IN")
    translator = Translator(target_language, model="sarvam")
    out = await translator.translate_batch([protected], source_code, target_code)
    if not out:
        logger.warning(
            "[%s] page %s: sarvam returned empty on legacy HTML — keeping source",
            job_id or "?", page_no,
        )
        return section_html
    return _restore_tags(out[0] or "", tags)


async def _translate_page(
    *,
    sem: asyncio.Semaphore,
    page_png: bytes,
    page_no: int,
    total_pages: int,
    lang: str,
    target_language: str,
    model: str,
    structured_layout: bool,
    prompt_version: str,
    debug_dir: str | None,
    job_id: str | None,
) -> tuple[str, bool, dict | None]:
    sha_short, s3_key = _cache_key_for_page(page_png, lang, model, prompt_version)
    cached = await asyncio.to_thread(_cache_lookup, s3_key, sha_short)
    if cached is not None:
        return cached, True, None

    async with sem:
        # OCR pass — vision returns source-language transcript + layout.
        raw = await _call_vision_model(
            page_png=page_png,
            page_no=page_no,
            total_pages=total_pages,
            target_language=target_language,
            model=model,
            structured_layout=structured_layout,
        )
        html_out, fidelity = await _materialize_ocr_and_translate(
            raw=raw,
            page_no=page_no,
            target_language=lang,
            structured_layout=structured_layout,
            job_id=job_id,
        )
        # One retry with legacy HTML if structured JSON was unusable (avoid blank/garbled PDF).
        if structured_layout and fidelity is None:
            stripped = _strip_code_fence(raw).strip()
            if not _SECTION_RE.search(stripped):
                logger.warning(
                    "[%s] page %s: structured vision parse failed — retrying legacy HTML prompt",
                    job_id or "?",
                    page_no,
                )
                raw = await _call_vision_model(
                    page_png=page_png,
                    page_no=page_no,
                    total_pages=total_pages,
                    target_language=target_language,
                    model=model,
                    structured_layout=False,
                )
                html_out, fidelity = await _materialize_ocr_and_translate(
                    raw=raw,
                    page_no=page_no,
                    target_language=lang,
                    structured_layout=False,
                    job_id=job_id,
                )
    from legal_agent.config import get_settings

    _dbg = get_settings()
    if (
        structured_layout
        and fidelity
        and debug_dir
        and job_id
        and _dbg.vision_translation_debug_fidelity_files
    ):
        from legal_agent.agents.translation.html_pdf_translator import _dump

        _dump(
            debug_dir,
            job_id,
            f"vision_page_{page_no:03d}_fidelity",
            json.dumps(fidelity, indent=2),
            ext=".json",
        )

    stripped_html = html_out.strip()
    cleaned = (
        stripped_html
        if stripped_html.startswith("<section")
        else _normalize_section_html(stripped_html, page_no)
    )
    await asyncio.to_thread(_cache_store, s3_key, sha_short, cleaned)
    return cleaned, False, fidelity


async def _materialize_ocr_and_translate(
    *,
    raw: str,
    page_no: int,
    target_language: str,
    structured_layout: bool,
    job_id: str | None,
) -> tuple[str, dict | None]:
    """Parse vision OCR response, translate via Sarvam, emit section HTML.

    Structured path: JSON → VisionStructuredPage → Sarvam-translate each block
    → normalize headers → emit HTML + fidelity metrics.
    Legacy fallback: extract <section> HTML in source language → Sarvam-translate
    its text (tags tokenized) → return.
    """
    from legal_agent.agents.translation.vision_header_normalize import (
        normalize_government_header_blocks,
    )
    from legal_agent.agents.translation.vision_structured_layout import (
        parse_vision_structured_response,
        vision_fidelity_summary,
        vision_structured_page_to_section_html,
    )

    if structured_layout:
        page = parse_vision_structured_response(raw)
        if page is not None:
            translated_page = await _translate_structured_page_via_sarvam(
                page, target_language, job_id, page_no,
            )
            norm_blocks = normalize_government_header_blocks(list(translated_page.blocks))
            translated_page = translated_page.model_copy(update={"blocks": norm_blocks})
            html = vision_structured_page_to_section_html(translated_page, page_no)
            return html, vision_fidelity_summary(norm_blocks)
        logger.warning(
            "vision structured JSON parse failed for page %s — falling back to legacy HTML",
            page_no,
        )
    stripped = _strip_code_fence(raw).strip()
    match = _SECTION_RE.search(stripped)
    section_html = match.group(0) if match else _normalize_section_html(stripped, page_no)
    translated_html = await _translate_legacy_section_html_via_sarvam(
        section_html, target_language, job_id, page_no,
    )
    return translated_html, None


def _cache_key_for_page(image_bytes: bytes, lang: str, model: str, prompt_version: str) -> tuple[str, str]:
    from legal_agent.config import get_settings

    settings = get_settings()
    h = hashlib.sha256()
    h.update(image_bytes)
    h.update(b"|")
    h.update(lang.encode())
    h.update(b"|")
    h.update(model.encode())
    h.update(b"|")
    h.update(prompt_version.encode())
    sha = h.hexdigest()
    model_slug = re.sub(r"[^a-z0-9]+", "-", model.lower()).strip("-")
    pv_slug = re.sub(r"[^a-z0-9]+", "-", prompt_version.lower()).strip("-")
    key = (
        f"{settings.ocr_cache_prefix}/vision_translate/"
        f"{lang.lower()}/{model_slug}/{pv_slug}/{sha}.html"
    )
    sha_short = f"vision/{lang}/{sha[:12]}"
    return sha_short, key


async def _call_vision_model(
    *,
    page_png: bytes,
    page_no: int,
    total_pages: int,
    target_language: str,
    model: str,
    structured_layout: bool,
) -> str:
    """Invoke the vision LLM via LangChain with transient-error retries."""
    from langchain.chat_models import init_chat_model
    from langchain_core.messages import HumanMessage

    b64 = base64.b64encode(page_png).decode("ascii")
    if structured_layout:
        prompt = _PROMPT_STRUCTURED_JSON.format(
            page_no=page_no,
            target_language=target_language,
        )
    else:
        prompt = _PROMPT_LEGACY_HTML.format(
            page_no=page_no,
            total_pages=total_pages,
            target_language=target_language,
        )
    llm = init_chat_model(
        model,
        model_provider="anthropic",
        max_tokens=8192,
        temperature=0.0,
    )
    for attempt in range(1, _VISION_RETRY_MAX_ATTEMPTS + 1):
        try:
            response = await llm.ainvoke([
                HumanMessage(content=[
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
                    },
                ])
            ])
            break
        except Exception as exc:
            retryable = _is_retryable_vision_error(exc)
            if (not retryable) or attempt >= _VISION_RETRY_MAX_ATTEMPTS:
                raise
            delay = _VISION_RETRY_BASE_SECONDS * (2 ** (attempt - 1))
            logger.warning(
                "[vision] transient provider error on attempt %d/%d (%s); retrying in %.1fs",
                attempt,
                _VISION_RETRY_MAX_ATTEMPTS,
                type(exc).__name__,
                delay,
            )
            await asyncio.sleep(delay)

    content = response.content
    if isinstance(content, list):
        return "".join(
            part.get("text", "")
            for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        )
    return str(content)


def _is_retryable_vision_error(exc: Exception) -> bool:
    """Best-effort transient classification across Anthropic/LangChain wrappers."""
    name = type(exc).__name__.lower()
    msg = str(exc).lower()
    return any(token in name or token in msg for token in (
        "internalservererror",
        "api_error",
        "internal server error",
        "rate",
        "timeout",
        "connection",
        "overloaded",
        "temporar",
        "503",
        "502",
        "500",
        "529",
    ))


def _normalize_section_html(raw: str, page_no: int) -> str:
    text = _strip_code_fence(raw).strip()
    match = _SECTION_RE.search(text)
    if match:
        return match.group(0)
    return f'<section data-page="{page_no}">\n{text}\n</section>'
