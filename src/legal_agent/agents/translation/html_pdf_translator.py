"""PDF translation via IR pipeline.

Pipeline: PDF bytes → layout_extract (PyMuPDF dict) → translate spans via Sarvam
→ layout_render (flow HTML with flexbox) → Playwright PDF.

Flow HTML survives Hindi expansion (~30% wider) without absolute-position collisions.
Split rows (company|location, role|date) are expressed as RowBlock and rendered with
CSS flexbox so left/right alignment is preserved regardless of text length changes.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from legal_agent.models.requests import CreateTranslationJobRequest

logger = logging.getLogger(__name__)

_SKIP_RE = re.compile(r'^[\d\s.,/%$€₹+\-–—:;()\[\]@#&*|•·∙○◦■□▪▫]+$')
_EMAIL_RE = re.compile(r'^\S+@\S+\.\S+$')
_URL_RE = re.compile(r'^https?://', re.IGNORECASE)

# Sarvam REST translate caps input at 2000 chars/request. Margin for glossary
# sentinel expansion / NFC differences.
_SARVAM_MAX_CHARS = 1800

_SARVAM_SPLIT_RE = re.compile(r"(\n+|(?<=[.!?।])\s+)")

# Form-field dotted lines, signature underscores, and other OCR-extracted noise
# tend to produce runs of the same non-alphanum char hundreds of chars long
# (e.g. "Date: ......................"). Sarvam Translate rejects these with
# "Input contains excessively repeated characters." Cap any run at 5 occurrences.
_REPEAT_RUN_RE = re.compile(r"([^\w\s])\1{5,}")


def _collapse_repeated_chars(text: str) -> str:
    return _REPEAT_RUN_RE.sub(lambda m: m.group(1) * 5, text)


def _is_image_only_pdf(data: bytes, min_chars_per_page: int = 50) -> bool:
    """True iff every page has < min_chars_per_page native text characters."""
    import fitz

    doc = fitz.open(stream=data, filetype="pdf")
    try:
        for page in doc:
            d = page.get_text("dict", sort=True)
            n = sum(
                len(s.get("text", ""))
                for b in d.get("blocks", [])
                if b.get("type") == 0
                for ln in b.get("lines", [])
                for s in ln.get("spans", [])
            )
            if n >= min_chars_per_page:
                return False
        return True
    finally:
        doc.close()


def _needs_translation(text: str) -> bool:
    # Evaluate after collapsing repeated noise (dotted underlines, signature
    # bars) so "Date: ..............." still translates "Date:" and a pure-dots
    # row gets filtered.
    t = _collapse_repeated_chars(text.strip())
    if not t or len(t) <= 2:
        return False
    if _EMAIL_RE.match(t) or _URL_RE.match(t) or _SKIP_RE.match(t):
        return False
    alnum = sum(1 for c in t if c.isalnum())
    if alnum < 3:
        return False
    return True


def _chunk_for_sarvam(text: str, limit: int = _SARVAM_MAX_CHARS) -> list[str]:
    """Split text into ≤limit chunks on paragraph/sentence boundaries, lossless concat."""
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    buf = ""
    for part in _SARVAM_SPLIT_RE.split(text):
        if not part:
            continue
        if len(buf) + len(part) <= limit:
            buf += part
            continue
        if buf:
            chunks.append(buf)
            buf = ""
        if len(part) <= limit:
            buf = part
        else:
            for i in range(0, len(part), limit):
                chunks.append(part[i:i + limit])
    if buf:
        chunks.append(buf)
    return chunks


def _dump(
    debug_dir: str | None,
    job_id: str,
    name: str,
    content: str | bytes,
    *,
    ext: str | None = None,
) -> None:
    if not debug_dir:
        return
    try:
        d = Path(debug_dir) / job_id
        d.mkdir(parents=True, exist_ok=True)
        if ext:
            suffix = ext if ext.startswith(".") else f".{ext}"
        elif isinstance(content, bytes):
            suffix = ".pdf"
        else:
            suffix = ".html"
        path = d / f"{name}{suffix}"
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8")
        logger.info("[%s] debug -> %s", job_id, path)
    except Exception as exc:
        logger.warning("[%s] debug dump failed (%s): %s", job_id, name, exc)


def render_html_to_pdf_bytes(html_document: str, pdf_options: dict | None = None) -> bytes:
    """Render HTML to PDF via Playwright Chromium. Invoked from a worker thread."""

    async def _render() -> bytes:
        from playwright.async_api import async_playwright

        defaults: dict = {
            "format": "A4",
            "print_background": True,
            "margin": {"top": "0", "bottom": "0", "left": "0", "right": "0"},
        }
        opts = {**defaults, **(pdf_options or {})}

        async with async_playwright() as p:
            browser = await p.chromium.launch()
            try:
                page = await browser.new_page()
                await page.set_content(html_document, wait_until="networkidle")
                return await page.pdf(**opts)
            finally:
                await browser.close()

    return asyncio.run(_render())


async def translate_pdf_via_html(
    source_bytes: bytes,
    filename: str,
    request: "CreateTranslationJobRequest",
    job_id: str,
    debug_dir: str | None = None,
) -> tuple[bytes, dict]:
    """Translate a PDF using the IR pipeline.

    Returns (pdf_bytes, metadata_dict).

    Image-only PDFs (every page has < 50 native text chars) are translated via
    the vision-LLM reconstruction path (Claude vision → translated HTML → PDF).
    PDFs with native text use PyMuPDF IR → flow-HTML path.
    """
    from legal_agent.agents.translation.layout_extract import extract_document
    from legal_agent.agents.translation.layout_render import render_to_html
    from legal_agent.agents.translation.sarvam_translate import SARVAM_LANG_CODES

    if _is_image_only_pdf(source_bytes):
        from legal_agent.agents.translation.vision_translator import (
            translate_scanned_pdf_via_vision,
        )
        pdf_v, meta_v = await translate_scanned_pdf_via_vision(
            source_bytes, request, job_id, debug_dir
        )
        meta_v["scanned_translation_mode"] = "vision_reconstruct"
        return pdf_v, meta_v

    lang = request.target_language.value
    target_code = SARVAM_LANG_CODES.get(lang, "hi-IN")
    source_code = (
        SARVAM_LANG_CODES.get(request.source_language.value, "en-IN")
        if request.source_language
        else "en-IN"
    )
    t_extract = time.perf_counter()

    # 1. Extract IR
    ir_doc = await asyncio.to_thread(extract_document, source_bytes, ocr_language=source_code)
    if not ir_doc.pages:
        raise RuntimeError("extract_document returned no pages — PDF may be image-only, encrypted, or invalid")
    logger.info("[%s] native extract took %.2fs", job_id, time.perf_counter() - t_extract)

    translated_ir, blocks_total, blocks_translated, backend, pipeline_metrics = (
        await _translate_ir_document(ir_doc, lang, source_code, target_code, job_id)
    )

    # 4. Render to HTML
    t_render_html = time.perf_counter()
    final_html = render_to_html(translated_ir, lang)
    _dump(debug_dir, job_id, "4_final", final_html)
    logger.info("[%s] HTML render took %.2fs", job_id, time.perf_counter() - t_render_html)

    # 5. Render to PDF
    t_pdf = time.perf_counter()
    pdf_bytes = await asyncio.to_thread(render_html_to_pdf_bytes, final_html)
    logger.info("[%s] PDF render took %.2fs", job_id, time.perf_counter() - t_pdf)

    return pdf_bytes, {
        "pages": len(ir_doc.pages),
        "blocks_total": blocks_total,
        "blocks_translated": blocks_translated,
        "translation_backend": backend,
        "translation_pipeline": "pymupdf_ir_three_stage_playwright",
        **pipeline_metrics,
    }


_LANG_LABEL_FROM_CODE: dict[str, str] = {
    "en": "English", "hi": "Hindi", "mr": "Marathi", "bn": "Bengali",
    "ta": "Tamil", "te": "Telugu", "gu": "Gujarati", "kn": "Kannada",
    "ml": "Malayalam", "or": "Odia", "pa": "Punjabi", "as": "Assamese",
    "ur": "Urdu", "ne": "Nepali", "sa": "Sanskrit", "mai": "Maithili",
    "kok": "Konkani", "doi": "Dogri", "sd": "Sindhi", "ks": "Kashmiri",
    "brx": "Bodo", "mni": "Manipuri", "sat": "Santali",
}


def _label_from_lang_code(code: str) -> str:
    base = code.split("-", 1)[0].lower() if code else ""
    return _LANG_LABEL_FROM_CODE.get(base, base.title() or "Unknown")


async def _translate_ir_document(
    ir_doc,
    lang: str,
    source_code: str,
    target_code: str,
    job_id: str,
):
    from legal_agent.agents.translation.layout_ir import RowBlock, Span, TextBlock
    from legal_agent.agents.translation.translator import DocumentContext, Translator
    from legal_agent.config import get_settings

    settings = get_settings()
    # Stage B backend: honour translation_llm_model. When it's "sarvam"
    # (the configured default), Sarvam REST does the per-chunk translation —
    # fast, Indic-specialist, no system prompt. Any other value names an LLM
    # (claude-*, gemini-*, gpt-*) and engages the context-windowed prompt path.
    stage_b_model = settings.translation_llm_model
    translator = Translator(lang, model=stage_b_model)
    # Stage A (glossary) + Stage C (reviewer) run for both Sarvam and LLM
    # backends. On Sarvam, Stage A terms protect via freeze()/restore() sentinels
    # (Sarvam has no system prompt), and Stage C catches drift post-translation.
    # Only Stage B's context-windowed prompt remains LLM-only — gated inside
    # Translator via uses_context_pipeline.
    run_stage_ac = True

    block_refs: list[tuple[int, int, str]] = []
    original_span_lists: list[list[Span]] = []

    def _collect(spans: list[Span], pi: int, bi: int, side: str) -> None:
        block_refs.append((pi, bi, side))
        original_span_lists.append(spans)

    for pi, page in enumerate(ir_doc.pages):
        for bi, block in enumerate(page.blocks):
            if isinstance(block, RowBlock):
                _collect(block.left, pi, bi, "left")
                _collect(block.right, pi, bi, "right")
            elif isinstance(block, TextBlock):
                _collect(block.spans, pi, bi, "spans")

    joined_per_block = ["".join(s.text for s in spans) for spans in original_span_lists]
    translatable_indices: list[int] = []
    translatable_chunks: list[str] = []
    chunk_owner: list[int] = []
    for i, joined in enumerate(joined_per_block):
        if not _needs_translation(joined):
            continue
        prepared = _collapse_repeated_chars(joined.strip())
        chunks = _chunk_for_sarvam(prepared)
        if len(chunks) > 1:
            logger.info("[translate] splitting %d chars into %d chunks", len(prepared), len(chunks))
        translatable_indices.append(i)
        for c in chunks:
            translatable_chunks.append(c)
            chunk_owner.append(i)

    source_label = _label_from_lang_code(source_code)
    target_label = _label_from_lang_code(target_code) or lang.title()

    pipeline_metrics: dict[str, object] = {
        "translation_pipeline_mode": "three_stage" if run_stage_ac else "legacy_single_pass",
        "translation_primary_model": translator.backend,
    }

    # Stage A — per-document glossary extractor (Haiku); runs for any backend.
    doc_glossary = None
    if run_stage_ac and translatable_indices:
        from legal_agent.agents.translation.document_glossary import (
            extract_document_glossary,
        )
        t_gloss = time.perf_counter()
        joined_source_text = "\n\n".join(
            joined_per_block[i] for i in translatable_indices
        )
        doc_glossary = await extract_document_glossary(
            joined_source_text, source_label, target_label,
        )
        pipeline_metrics["glossary_terms"] = len(doc_glossary.terms)
        pipeline_metrics["document_subject"] = doc_glossary.subject[:200]
        pipeline_metrics["document_register"] = doc_glossary.doc_register
        logger.info(
            "[%s] glossary extraction took %.2fs (%d terms, register=%s)",
            job_id, time.perf_counter() - t_gloss, len(doc_glossary.terms),
            doc_glossary.doc_register,
        )
        translator.set_document_context(
            DocumentContext(
                subject=doc_glossary.subject,
                source_language=source_label,
                target_language=target_label,
                register=doc_glossary.doc_register,
                glossary={t.source: t.target for t in doc_glossary.terms},
            ),
            dynamic_entries=doc_glossary.to_glossary_entries(),
        )

    translated_chunks: list[str] = []
    if translatable_chunks:
        t_translate = time.perf_counter()
        translated_chunks = await translator.translate_batch(
            translatable_chunks, source_code, target_code
        )
        logger.info("[%s] translation calls took %.2fs", job_id, time.perf_counter() - t_translate)

    translated_joined: dict[int, str] = {}
    for chunk_text, owner in zip(translated_chunks, chunk_owner):
        translated_joined[owner] = translated_joined.get(owner, "") + chunk_text

    # Stage C — source-grounded reviewer pass; runs for any backend.
    if run_stage_ac and translated_joined and settings.translation_reviewer_enabled:
        from legal_agent.agents.translation.reviewer import (
            Reviewer,
            review_in_batches,
        )
        reviewer = Reviewer(
            source_label,
            target_label,
            register=(doc_glossary.doc_register if doc_glossary else "government_legal"),
        )
        if reviewer.enabled:
            owner_order = sorted(translated_joined.keys())
            source_regions = [joined_per_block[i] for i in owner_order]
            candidate_regions = [translated_joined[i] for i in owner_order]
            t_review = time.perf_counter()
            review = await review_in_batches(
                reviewer,
                source_regions=source_regions,
                candidate_regions=candidate_regions,
                subject=(doc_glossary.subject if doc_glossary else ""),
                glossary=(
                    {t.source: t.target for t in doc_glossary.terms}
                    if doc_glossary
                    else None
                ),
                batch_size=20,
            )
            logger.info(
                "[%s] reviewer pass took %.2fs (%d/%d corrected)",
                job_id, time.perf_counter() - t_review,
                review.fixed_count, len(owner_order),
            )
            for owner, item in zip(owner_order, review.items):
                translated_joined[owner] = item.corrected
            pipeline_metrics["reviewer_regions"] = len(owner_order)
            pipeline_metrics["reviewer_fixed_count"] = review.fixed_count
            pipeline_metrics["reviewer_model"] = settings.translation_reviewer_model

    # CBIC/Rajbhasha post-pass for Hindi: transliterate DIN/F.NO. labels and expand
    # address abbreviations (नं. → संख्या) that the translator doesn't reliably handle.
    if target_code.startswith("hi"):
        from legal_agent.agents.translation.glossary import normalize_govt_hindi
        translated_joined = {k: normalize_govt_hindi(v) for k, v in translated_joined.items()}

    results: list[list[Span]] = []
    for i, spans in enumerate(original_span_lists):
        if i not in translated_joined:
            results.append(spans)
            continue
        joined = joined_per_block[i]
        total = len(joined) or 1
        bold = sum(len(s.text) for s in spans if s.bold) / total > 0.5
        italic = sum(len(s.text) for s in spans if s.italic) / total > 0.5
        results.append([Span(text=translated_joined[i], bold=bold, italic=italic)])

    # 3. Apply translated spans back into the IR (build new immutable objects)
    from copy import deepcopy
    translated_ir = deepcopy(ir_doc)

    updates: dict[tuple[int, int, str], list[Span]] = {}
    for (pi, bi, side), translated_spans in zip(block_refs, results):
        updates[(pi, bi, side)] = translated_spans

    for (pi, bi, side), translated_spans in updates.items():
        block = translated_ir.pages[pi].blocks[bi]
        if side == "spans" and isinstance(block, TextBlock):
            translated_ir.pages[pi].blocks[bi] = TextBlock(
                type=block.type, level=block.level, align=block.align,
                spans=translated_spans,
            )
        elif side == "left" and isinstance(block, RowBlock):
            translated_ir.pages[pi].blocks[bi] = RowBlock(
                left=translated_spans,
                right=block.right,
            )
        elif side == "right" and isinstance(block, RowBlock):
            existing = translated_ir.pages[pi].blocks[bi]
            translated_ir.pages[pi].blocks[bi] = RowBlock(
                left=existing.left,
                right=translated_spans,
            )

    blocks_total = len(block_refs)
    blocks_translated = sum(
        1 for (pi, bi, side) in block_refs
        if _needs_translation("".join(
            s.text for s in (
                ir_doc.pages[pi].blocks[bi].left
                if side == "left" else
                ir_doc.pages[pi].blocks[bi].right
                if side == "right" else
                ir_doc.pages[pi].blocks[bi].spans  # type: ignore[union-attr]
            )
        ))
    )

    logger.info(
        "[%s] translated %d/%d blocks, %d pages via %s",
        job_id, blocks_translated, blocks_total, len(ir_doc.pages), translator.backend,
    )
    return translated_ir, blocks_total, blocks_translated, translator.backend, pipeline_metrics


