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
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from legal_agent.models.requests import CreateTranslationJobRequest

logger = logging.getLogger(__name__)

_SKIP_RE = __import__("re").compile(r'^[\d\s.,/%$€₹+\-–—:;()\[\]@#&*|•·∙○◦■□▪▫]+$')
_EMAIL_RE = __import__("re").compile(r'^\S+@\S+\.\S+$')
_URL_RE = __import__("re").compile(r'^https?://', __import__("re").IGNORECASE)


def _needs_translation(text: str) -> bool:
    t = text.strip()
    if not t or len(t) <= 2:
        return False
    if _EMAIL_RE.match(t) or _URL_RE.match(t) or _SKIP_RE.match(t):
        return False
    return True


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
    """Render HTML to PDF via Playwright Chromium."""
    import asyncio as _asyncio

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

    loop = _asyncio.ProactorEventLoop() if hasattr(_asyncio, "ProactorEventLoop") else _asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_render())
    finally:
        loop.close()


async def translate_pdf_via_html(
    source_bytes: bytes,
    filename: str,
    request: "CreateTranslationJobRequest",
    job_id: str,
    debug_dir: str | None = None,
) -> tuple[bytes, dict]:
    """Translate a PDF using the IR pipeline.

    Returns (pdf_bytes, metadata_dict).
    """
    from legal_agent.agents.translation.glossary import (
        DocState,
        Glossary,
        freeze,
        localize_units,
        restore,
        strip_pua,
    )
    from legal_agent.agents.translation.layout_extract import extract_document
    from legal_agent.agents.translation.layout_ir import RowBlock, Span, TextBlock
    from legal_agent.agents.translation.layout_render import render_to_html
    from legal_agent.agents.translation.sarvam_translate import (
        SARVAM_LANG_CODES,
        call_sarvam_translate,
        clean_sarvam_translate_output,
    )
    from legal_agent.config import get_settings

    settings = get_settings()
    lang = request.target_language.value
    target_code = SARVAM_LANG_CODES.get(lang, "hi-IN")
    source_code = (
        SARVAM_LANG_CODES.get(request.source_language.value, "en-IN")
        if request.source_language
        else "en-IN"
    )
    api_key = settings.sarvam_api_key
    if not api_key:
        raise RuntimeError("SARVAM_API_KEY not configured")

    tm = settings.sarvam_translate_model
    sem = asyncio.Semaphore(max(1, settings.sarvam_translate_max_concurrency))
    glossary = Glossary.load()
    doc_state = DocState()
    state_lock = asyncio.Lock()
    is_hindi_target = target_code.startswith("hi")

    async def _translate_text(text: str) -> str:
        if not text.strip():
            return text
        prepared = strip_pua(text.strip())
        if is_hindi_target:
            prepared = localize_units(prepared)
        async with state_lock:
            frozen, sentinels = freeze(prepared, doc_state, glossary)
        async with sem:
            raw = await call_sarvam_translate(frozen, source_code, target_code, api_key, tm)
        cleaned = clean_sarvam_translate_output(raw) or frozen
        return restore(cleaned, sentinels)

    async def _translate_spans(spans: list[Span]) -> list[Span]:
        """Translate spans as one unit; return a single Span with dominant formatting.

        Never redistributes translated text back across multiple spans — doing so splits
        Devanagari conjunct clusters across element boundaries, which breaks shaping in
        Chromium (conjuncts like फ्ट appear as "फ्ट" visually spaced apart).
        """
        if not spans:
            return spans
        joined = "".join(s.text for s in spans)
        if not _needs_translation(joined):
            return spans
        translated = await _translate_text(joined)
        total = len(joined) or 1
        bold = sum(len(s.text) for s in spans if s.bold) / total > 0.5
        italic = sum(len(s.text) for s in spans if s.italic) / total > 0.5
        return [Span(text=translated, bold=bold, italic=italic)]

    # 1. Extract IR
    ir_doc = await asyncio.to_thread(extract_document, source_bytes)
    if not ir_doc.pages:
        raise RuntimeError("extract_document returned no pages — PDF may be image-only, encrypted, or invalid")

    # 2. Translate all spans concurrently (one coroutine per block)
    tasks: list[asyncio.Task] = []
    block_refs: list[tuple[int, int, str]] = []  # (page_idx, block_idx, side)

    for pi, page in enumerate(ir_doc.pages):
        for bi, block in enumerate(page.blocks):
            if isinstance(block, RowBlock):
                tasks.append(asyncio.create_task(_translate_spans(block.left)))
                block_refs.append((pi, bi, "left"))
                tasks.append(asyncio.create_task(_translate_spans(block.right)))
                block_refs.append((pi, bi, "right"))
            elif isinstance(block, TextBlock):
                tasks.append(asyncio.create_task(_translate_spans(block.spans)))
                block_refs.append((pi, bi, "spans"))

    results = await asyncio.gather(*tasks)

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
    _dump(debug_dir, job_id, "5_rendered", pdf_bytes)

    return pdf_bytes, {
        "pages": len(ir_doc.pages),
        "blocks_total": blocks_total,
        "blocks_translated": blocks_translated,
    }
