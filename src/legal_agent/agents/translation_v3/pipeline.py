"""Translation v3 orchestrator.

Chains the v3 stages and converts stage-specific failures into StagedError so
the JobManager tags the failure correctly. Reuses v2's rasterize, reflow,
html_render, and compose stages unchanged — only OCR (stage 2, Azure
Document Intelligence) and translate (stage 4) are swapped, and stage 7
(persist editable document.json) is new.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

from legal_agent.agents.translation_v2.compose import concat_pdfs, render_pages_to_pdfs
from legal_agent.agents.translation_v2.html_render import load_font_face_css, render_page_html
from legal_agent.agents.translation_v2.rasterize import rasterize_pdf
from legal_agent.agents.translation_v2.schemas import Document
from legal_agent.agents.translation_v3.azure_extract import extract_pages
from legal_agent.agents.translation_v3.block_refine_haiku import refine_pages
from legal_agent.agents.translation_v3.glossary_haiku import build_glossary
from legal_agent.config import get_settings
from legal_agent.services.job_manager import ErrorStage, StagedError

if TYPE_CHECKING:
    from legal_agent.models.requests import CreateTranslationJobRequest

logger = logging.getLogger(__name__)

# Lazy S3 client for stage 7 (document.json upload). Same pattern as utils/ocr.py.
_s3_client_singleton: Any = None


def _get_s3_client():
    global _s3_client_singleton
    settings = get_settings()
    if _s3_client_singleton is None:
        if not settings.s3_access_key or not settings.s3_secret_key:
            return None
        from legal_agent.clients.s3_client import S3Client

        _s3_client_singleton = S3Client(settings)
    return _s3_client_singleton


def _resolve_translate_engine(request: "CreateTranslationJobRequest") -> str:
    """Per-request override wins over the settings default."""
    settings = get_settings()
    engine = getattr(request, "translate_engine", None) or settings.translation_v3_translate_engine
    if engine not in ("haiku", "sarvam"):
        raise ValueError(f"translation_v3 unknown translate_engine: {engine}")
    return engine


async def translate_pdf_v3(
    source_bytes: bytes,
    filename: str,
    request: "CreateTranslationJobRequest",
    job_id: str,
    debug_dir: str | None = None,  # noqa: ARG001 — signature parity with v1/v2
) -> tuple[bytes, dict[str, Any]]:
    """Run the v3 translation pipeline. Returns (pdf_bytes, metadata_dict).

    Metadata includes `document_json_key` when stage 7 successfully uploads the
    editable structured document to S3. The UI uses that key to fetch + edit
    blocks, then POSTs them back to `/api/v1/jobs/{job_id}/render`.
    """
    settings = get_settings()
    engine = _resolve_translate_engine(request)
    dpi = settings.translation_v3_target_dpi
    glossary_model = settings.translation_v3_glossary_model
    translate_model = settings.translation_v3_translate_model

    meta: dict[str, Any] = {
        "translation_pipeline": "v3",
        "extraction_route": "v3_azure_html",
        "translate_engine": engine,
        "glossary_engine": "haiku",
        "translation_v3_glossary_model": glossary_model,
        "translation_v3_translate_model": translate_model if engine == "haiku" else None,
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
        "[%s] v3 stage 1 rasterize: %d pages in %d ms",
        job_id,
        len(rasters),
        meta["rasterize_ms"],
    )

    # ── Stage 2: azure_extract (Azure Document Intelligence) ─────────────
    t0 = time.perf_counter()
    try:
        vision_pages = await extract_pages(
            rasters,
            lang=settings.translation_v3_ocr_lang_hint,
            concurrency=settings.translation_v3_azure_concurrency,
            job_id=job_id,
        )
    except Exception as exc:
        raise StagedError(ErrorStage.OCR, exc) from exc
    meta["ocr_ms"] = int((time.perf_counter() - t0) * 1000)
    meta["block_count"] = sum(len(p.blocks) for p in vision_pages)
    logger.info(
        "[%s] v3 stage 2 azure: %d blocks across %d pages in %d ms",
        job_id,
        meta["block_count"],
        len(vision_pages),
        meta["ocr_ms"],
    )

    # ── Stage 2.5: Haiku multimodal block refinement (fail-soft) ─────────
    # Corrects misclassified roles (heading vs paragraph) and false bold/italic
    # using the page raster as visual evidence. Role is load-bearing for the
    # flow-layout renderer (each role → CSS class → typography). Disabled via
    # `translation_v3_refine_enabled=false`.
    if settings.translation_v3_refine_enabled:
        t0 = time.perf_counter()
        try:
            vision_pages = await refine_pages(
                vision_pages,
                rasters,
                model=settings.translation_v3_refine_model,
                concurrency=settings.translation_v3_refine_concurrency,
                job_id=job_id,
            )
        except Exception as exc:  # noqa: BLE001 — fail-soft, fall through to glossary
            logger.warning(
                "[%s] v3 stage 2.5 refine failed (%s: %s); using Azure output as-is",
                job_id, type(exc).__name__, exc,
            )
        meta["refine_ms"] = int((time.perf_counter() - t0) * 1000)
        logger.info("[%s] v3 stage 2.5 refine: %d ms", job_id, meta["refine_ms"])
    else:
        meta["refine_ms"] = 0

    # ── Stage 3: glossary (fail-soft) ────────────────────────────────────
    t0 = time.perf_counter()
    glossary = await build_glossary(vision_pages, model=glossary_model, job_id=job_id)
    meta["glossary_ms"] = int((time.perf_counter() - t0) * 1000)
    meta["glossary_size"] = len(glossary)

    # ── Stage 4: translate (haiku | sarvam) ──────────────────────────────
    t0 = time.perf_counter()
    try:
        if engine == "haiku":
            from legal_agent.agents.translation_v3.translate_haiku import (
                translate_pages as _translate,
            )

            translated_pages = await _translate(
                vision_pages,
                glossary,
                model=translate_model,
                concurrency=settings.translation_v3_translate_concurrency,
                job_id=job_id,
            )
        else:  # sarvam
            from legal_agent.agents.translation_v3.translate_sarvam import (
                translate_pages as _translate,
            )

            translated_pages = await _translate(
                vision_pages,
                glossary,
                model="",  # unused for sarvam; settings drive it
                concurrency=settings.translation_v3_translate_concurrency,
                job_id=job_id,
            )
    except Exception as exc:
        raise StagedError(ErrorStage.TRANSLATION, exc) from exc
    meta["translate_ms"] = int((time.perf_counter() - t0) * 1000)
    logger.info("[%s] v3 stage 4 translate (%s): %d ms", job_id, engine, meta["translate_ms"])

    # ── Stage 5: HTML render (semantic flow) ─────────────────────────────
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

    # ── Stage 6: per-page PDFs + concat (reused from v2) ─────────────────
    t0 = time.perf_counter()
    try:
        per_page_pdfs = await render_pages_to_pdfs(
            htmls,
            page_sizes_mm,
            concurrency=settings.translation_v3_render_concurrency,
            job_id=job_id,
        )
    except Exception as exc:
        raise StagedError(ErrorStage.PDF_RENDER, exc) from exc
    meta["pdf_render_ms"] = int((time.perf_counter() - t0) * 1000)

    t0 = time.perf_counter()
    try:
        final_pdf = await concat_pdfs(per_page_pdfs)
    except Exception as exc:
        raise StagedError(ErrorStage.PDF_RENDER, exc) from exc
    meta["concat_ms"] = int((time.perf_counter() - t0) * 1000)

    # ── Stage 7: persist editable structured document (fail-soft) ────────
    # The UI calls GET /api/v1/jobs/{id}/document to fetch this JSON for
    # editing, then POST /api/v1/jobs/{id}/render to regenerate the PDF.
    t0 = time.perf_counter()
    document_key = await _upload_document_json(translated_pages, glossary, filename, job_id)
    if document_key:
        meta["document_json_key"] = document_key
    meta["document_json_ms"] = int((time.perf_counter() - t0) * 1000)

    logger.info(
        "[%s] v3 pipeline complete: %d pages, %d bytes "
        "(raster %d / azure %d / refine %d / glossary %d / translate %d / html %d / pdf %d / concat %d / doc %d ms)",
        job_id,
        len(per_page_pdfs),
        len(final_pdf),
        meta["rasterize_ms"],
        meta["ocr_ms"],
        meta["refine_ms"],
        meta["glossary_ms"],
        meta["translate_ms"],
        meta["html_render_ms"],
        meta["pdf_render_ms"],
        meta["concat_ms"],
        meta["document_json_ms"],
    )
    return final_pdf, meta


async def _upload_document_json(
    translated_pages: list,
    glossary: dict[str, str],
    filename: str,
    job_id: str,
) -> str | None:
    """Serialise and upload the editable Document JSON. Fail-soft."""
    s3 = _get_s3_client()
    if s3 is None:
        logger.info("[%s] v3 stage 7: S3 not configured; skipping document.json upload", job_id)
        return None
    try:
        settings = get_settings()
        doc = Document(
            source_filename=filename,
            pages=translated_pages,
            glossary=glossary,
        )
        body = doc.model_dump_json(indent=2).encode("utf-8")
        key = f"{settings.translation_v3_document_json_prefix}/{job_id}/document.json"
        await s3.upload_bytes(key, body, content_type="application/json; charset=utf-8")
        logger.info("[%s] v3 stage 7: uploaded document.json → %s (%d bytes)", job_id, key, len(body))
        return key
    except Exception as exc:  # noqa: BLE001 — fail-soft, PDF still returned
        logger.warning(
            "[%s] v3 stage 7: document.json upload failed (%s: %s); UI editing disabled for this job",
            job_id,
            type(exc).__name__,
            exc,
        )
        return None


__all__ = ["translate_pdf_v3"]
