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


def _dump(debug_dir: str | None, job_id: str, name: str, content: str | bytes) -> None:
    if not debug_dir:
        return
    try:
        d = Path(debug_dir) / job_id
        d.mkdir(parents=True, exist_ok=True)
        suffix = ".pdf" if isinstance(content, bytes) else ".html"
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

    Image-only PDFs (every page has < 50 native text chars) are routed to the
    bbox-overlay translator so seals/stamps/letterhead pixels survive. PDFs
    with any native text continue through the PyMuPDF dict → flow-HTML path.
    """
    from legal_agent.agents.translation.layout_extract import extract_document
    from legal_agent.agents.translation.layout_ir import RowBlock, Span, TextBlock
    from legal_agent.agents.translation.layout_render import render_to_html
    from legal_agent.agents.translation.overlay_translator import (
        is_image_only_pdf,
        translate_pdf_via_overlay,
    )
    from legal_agent.agents.translation.sarvam_translate import SARVAM_LANG_CODES
    from legal_agent.agents.translation.translator import Translator

    if is_image_only_pdf(source_bytes):
        logger.info("[%s] image-only PDF → overlay translator", job_id)
        pdf_bytes, meta = await translate_pdf_via_overlay(
            source_bytes, filename, request, job_id, debug_dir
        )
        meta.setdefault("translation_pipeline", "gemini_bbox_overlay_sarvam_pymupdf")
        return pdf_bytes, meta

    lang = request.target_language.value
    target_code = SARVAM_LANG_CODES.get(lang, "hi-IN")
    source_code = (
        SARVAM_LANG_CODES.get(request.source_language.value, "en-IN")
        if request.source_language
        else "en-IN"
    )
    translator = Translator(lang)

    # 1. Extract IR
    ir_doc = await asyncio.to_thread(extract_document, source_bytes, ocr_language=source_code)
    if not ir_doc.pages:
        raise RuntimeError("extract_document returned no pages — PDF may be image-only, encrypted, or invalid")

    # 2. Collect every span-list that needs translation, batch through the translator,
    #    and reassemble. We never redistribute translated text across multiple original
    #    spans — splitting Devanagari conjuncts across element boundaries breaks shaping.
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
            logger.info("[sarvam-translate] splitting %d chars into %d chunks", len(prepared), len(chunks))
        translatable_indices.append(i)
        for c in chunks:
            translatable_chunks.append(c)
            chunk_owner.append(i)

    translated_chunks: list[str] = []
    if translatable_chunks:
        translated_chunks = await translator.translate_batch(
            translatable_chunks, source_code, target_code
        )

    translated_joined: dict[int, str] = {}
    for chunk_text, owner in zip(translated_chunks, chunk_owner):
        translated_joined[owner] = translated_joined.get(owner, "") + chunk_text

    # CBIC/Rajbhasha post-pass for Hindi: transliterate DIN/F.NO. labels and expand
    # address abbreviations (नं. → संख्या) that Sarvam doesn't reliably handle.
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
        "[%s] translated %d/%d blocks, %d pages via Sarvam",
        job_id, blocks_translated, blocks_total, len(ir_doc.pages),
    )

    # 4. Render to HTML
    final_html = render_to_html(translated_ir, lang)
    _dump(debug_dir, job_id, "4_final", final_html)

    # 5. Render to PDF
    pdf_bytes = await asyncio.to_thread(render_html_to_pdf_bytes, final_html)

    return pdf_bytes, {
        "pages": len(ir_doc.pages),
        "blocks_total": blocks_total,
        "blocks_translated": blocks_translated,
        "translation_backend": translator.backend,
    }
