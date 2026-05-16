"""Translation v2 orchestrator.

Chains the 6 stages and converts stage-specific failures into StagedError so
the JobManager can tag the failure correctly. Optional debug dumps mirror v1's
_dump_debug pattern.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from legal_agent.agents.translation_v2.compose import concat_pdfs, render_pages_to_pdfs
from legal_agent.agents.translation_v2.gemini_client import build_client
from legal_agent.agents.translation_v2.glossary import build_glossary
from legal_agent.agents.translation_v2.html_render import load_font_face_css, render_page_html
from legal_agent.agents.translation_v2.rasterize import rasterize_pdf
from legal_agent.agents.translation_v2.schemas import PageRaster, TranslatedPage, VisionPage
from legal_agent.agents.translation_v2.translate import translate_pages
from legal_agent.agents.translation_v2.vision_extract import extract_pages
from legal_agent.config import get_settings
from legal_agent.services.job_manager import ErrorStage, StagedError

if TYPE_CHECKING:
    from legal_agent.models.requests import CreateTranslationJobRequest

logger = logging.getLogger(__name__)


def _dump(debug_dir: str | None, job_id: str, name: str, content: bytes | str) -> None:
    if not debug_dir:
        return
    try:
        d = Path(debug_dir) / job_id
        d.mkdir(parents=True, exist_ok=True)
        path = d / name
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8")
    except Exception as exc:
        logger.warning("[%s] debug dump failed (%s): %s", job_id, name, exc)


def _dump_pages(
    debug_dir: str | None,
    job_id: str,
    rasters: list[PageRaster] | None = None,
    vision_pages: list[VisionPage] | None = None,
    translated_pages: list[TranslatedPage] | None = None,
    htmls: list[str] | None = None,
    per_page_pdfs: list[bytes] | None = None,
) -> None:
    if not debug_dir:
        return
    if rasters:
        for r in rasters:
            _dump(debug_dir, job_id, f"p{r.page_no:03d}_raster.png", r.png)
    if vision_pages:
        for v in vision_pages:
            _dump(
                debug_dir,
                job_id,
                f"p{v.page_no:03d}_vision.json",
                v.model_dump_json(indent=2),
            )
    if translated_pages:
        for t in translated_pages:
            _dump(
                debug_dir,
                job_id,
                f"p{t.page_no:03d}_translated.json",
                t.model_dump_json(indent=2),
            )
    if htmls:
        for i, h in enumerate(htmls, start=1):
            _dump(debug_dir, job_id, f"p{i:03d}.html", h)
    if per_page_pdfs:
        for i, p in enumerate(per_page_pdfs, start=1):
            _dump(debug_dir, job_id, f"p{i:03d}.pdf", p)


async def translate_pdf_v2(
    source_bytes: bytes,
    filename: str,
    request: "CreateTranslationJobRequest",
    job_id: str,
    debug_dir: str | None = None,
) -> tuple[bytes, dict[str, Any]]:
    """Run the v2 translation pipeline. Returns (pdf_bytes, metadata_dict)."""
    settings = get_settings()
    model = settings.translation_v2_model
    dpi = settings.translation_v2_target_dpi
    meta: dict[str, Any] = {
        "translation_pipeline": "v2",
        "extraction_route": "v2_gemini_html",
        "translation_v2_model": model,
        "source_filename": filename,
    }

    # ── Stage 1: rasterize ───────────────────────────────────────────────
    t0 = time.perf_counter()
    try:
        rasters = await rasterize_pdf(source_bytes, dpi=dpi)
    except Exception as exc:
        raise StagedError(ErrorStage.EXTRACTION, exc) from exc
    if not rasters:
        raise StagedError(
            ErrorStage.EXTRACTION,
            RuntimeError("Source PDF has zero pages"),
        )
    meta["page_count"] = len(rasters)
    meta["rasterize_ms"] = int((time.perf_counter() - t0) * 1000)
    logger.info(
        "[%s] v2 stage 1 rasterize: %d pages in %d ms", job_id, len(rasters), meta["rasterize_ms"]
    )
    _dump_pages(debug_dir, job_id, rasters=rasters)

    client = build_client()

    # ── Stage 2: vision extract ──────────────────────────────────────────
    t0 = time.perf_counter()
    try:
        vision_pages = await extract_pages(
            client,
            rasters,
            model=model,
            concurrency=settings.translation_v2_vision_concurrency,
            job_id=job_id,
        )
    except Exception as exc:
        raise StagedError(ErrorStage.OCR, exc) from exc
    meta["vision_ms"] = int((time.perf_counter() - t0) * 1000)
    meta["vision_block_count"] = sum(len(p.blocks) for p in vision_pages)
    logger.info(
        "[%s] v2 stage 2 vision: %d blocks across %d pages in %d ms",
        job_id,
        meta["vision_block_count"],
        len(vision_pages),
        meta["vision_ms"],
    )
    _dump_pages(debug_dir, job_id, vision_pages=vision_pages)

    # ── Stage 3: glossary (fail-soft) ────────────────────────────────────
    t0 = time.perf_counter()
    glossary = await build_glossary(client, vision_pages, model=model, job_id=job_id)
    meta["glossary_ms"] = int((time.perf_counter() - t0) * 1000)
    meta["glossary_size"] = len(glossary)
    _dump(debug_dir, job_id, "glossary.json", json.dumps(glossary, ensure_ascii=False, indent=2))

    # ── Stage 4: translate ───────────────────────────────────────────────
    t0 = time.perf_counter()
    try:
        translated_pages = await translate_pages(
            client,
            vision_pages,
            glossary,
            model=model,
            concurrency=settings.translation_v2_translate_concurrency,
            job_id=job_id,
        )
    except Exception as exc:
        raise StagedError(ErrorStage.TRANSLATION, exc) from exc
    meta["translate_ms"] = int((time.perf_counter() - t0) * 1000)
    logger.info("[%s] v2 stage 4 translate: %d ms", job_id, meta["translate_ms"])
    _dump_pages(debug_dir, job_id, translated_pages=translated_pages)

    # ── Stage 5: HTML render ─────────────────────────────────────────────
    t0 = time.perf_counter()
    try:
        font_css = load_font_face_css()
        htmls: list[str] = []
        page_sizes_mm: list[tuple[float, float]] = []
        raster_by_page = {r.page_no: r for r in rasters}
        for tp in translated_pages:
            raster = raster_by_page[tp.page_no]
            htmls.append(render_page_html(tp, raster.width_mm, raster.height_mm, font_css))
            page_sizes_mm.append((raster.width_mm, raster.height_mm))
    except Exception as exc:
        raise StagedError(ErrorStage.TRANSLATION, exc) from exc
    meta["html_render_ms"] = int((time.perf_counter() - t0) * 1000)
    _dump_pages(debug_dir, job_id, htmls=htmls)

    # ── Stage 6: per-page PDFs + concat ──────────────────────────────────
    t0 = time.perf_counter()
    try:
        per_page_pdfs = await render_pages_to_pdfs(
            htmls,
            page_sizes_mm,
            concurrency=settings.translation_v2_render_concurrency,
            job_id=job_id,
        )
    except Exception as exc:
        raise StagedError(ErrorStage.PDF_RENDER, exc) from exc
    meta["pdf_render_ms"] = int((time.perf_counter() - t0) * 1000)
    _dump_pages(debug_dir, job_id, per_page_pdfs=per_page_pdfs)

    t0 = time.perf_counter()
    try:
        final_pdf = await concat_pdfs(per_page_pdfs)
    except Exception as exc:
        raise StagedError(ErrorStage.PDF_RENDER, exc) from exc
    meta["concat_ms"] = int((time.perf_counter() - t0) * 1000)
    _dump(debug_dir, job_id, "_final.pdf", final_pdf)

    logger.info(
        "[%s] v2 pipeline complete: %d pages, %d bytes (raster %d / vision %d / glossary %d / translate %d / html %d / pdf %d / concat %d ms)",
        job_id,
        len(per_page_pdfs),
        len(final_pdf),
        meta["rasterize_ms"],
        meta["vision_ms"],
        meta["glossary_ms"],
        meta["translate_ms"],
        meta["html_render_ms"],
        meta["pdf_render_ms"],
        meta["concat_ms"],
    )
    return final_pdf, meta
