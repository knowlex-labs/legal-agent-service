"""Stage 2 (v3): Azure Document Intelligence → VisionPage.

Replaces v3's previous self-hosted PaddleOCR PP-StructureV3 with Azure's
`prebuilt-layout` model. Azure returns per-block bounding boxes, reading
order, role metadata (title / sectionHeading / pageHeader / etc.), and
structured tables — across 309 languages including Hindi, Tamil, Kannada,
Bengali, English. ~$1.50 / 1000 pages flat.

The output shape matches v2's `VisionPage` exactly so every downstream stage
(glossary, translate, reflow, html_render, compose) is reused unchanged.

Per-page results are content-hashed and cached in S3 (same
`OCR_CACHE_ENABLED` / `OCR_CACHE_PREFIX` env vars as `utils/ocr.py`), so
job retries against the same PDF are free.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from legal_agent.agents.translation_v2.schemas import (
    Block,
    BlockRole,
    PageRaster,
    VisionPage,
)
from legal_agent.agents.translation_v3._layout_geometry import (
    estimate_font_size,
    infer_align,
    is_page_number,
)
from legal_agent.config import get_settings
from legal_agent.utils.ocr import _cache_key, _cache_lookup, _cache_store

logger = logging.getLogger(__name__)

_CACHE_PROVIDER = "azure_doc_intel"

# Azure paragraph.role string → our BlockRole. Anything unmapped → paragraph.
_ROLE_MAP: dict[str, BlockRole] = {
    "title": BlockRole.title,
    "sectionHeading": BlockRole.heading,
    "pageHeader": BlockRole.header,
    "pageFooter": BlockRole.footer,
    "pageNumber": BlockRole.page_number,
    "footnote": BlockRole.paragraph,
}

_client: Any | None = None
_client_lock = asyncio.Lock()


async def _get_client() -> Any:
    """Lazy process-singleton `AsyncDocumentIntelligenceClient`."""
    global _client
    if _client is not None:
        return _client
    async with _client_lock:
        if _client is not None:
            return _client
        settings = get_settings()
        endpoint = settings.azure_document_intelligence_endpoint
        key = settings.azure_document_intelligence_key
        if not endpoint or not key:
            raise RuntimeError(
                "AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT and "
                "AZURE_DOCUMENT_INTELLIGENCE_KEY must be set for translation_v3 OCR."
            )
        from azure.ai.documentintelligence.aio import DocumentIntelligenceClient
        from azure.core.credentials import AzureKeyCredential

        _client = DocumentIntelligenceClient(
            endpoint=endpoint,
            credential=AzureKeyCredential(key),
        )
        logger.info("Azure Document Intelligence client ready: %s", endpoint)
        return _client


# ── Response → VisionPage mapping ───────────────────────────────────────────


def _polygon_to_bbox_norm(
    polygon: list[float], page_w: float, page_h: float
) -> tuple[float, float, float, float]:
    """Convert Azure's 4-point polygon `[x0,y0,x1,y1,x2,y2,x3,y3]` to an
    axis-aligned bbox normalised to `[0, 1]`."""
    if not polygon or page_w <= 0 or page_h <= 0:
        return (0.0, 0.0, 1.0, 1.0)
    xs = polygon[0::2]
    ys = polygon[1::2]
    if not xs or not ys:
        return (0.0, 0.0, 1.0, 1.0)
    return (
        max(0.0, min(1.0, min(xs) / page_w)),
        max(0.0, min(1.0, min(ys) / page_h)),
        max(0.0, min(1.0, max(xs) / page_w)),
        max(0.0, min(1.0, max(ys) / page_h)),
    )


def _map_role(azure_role: str | None) -> BlockRole:
    if azure_role is None:
        return BlockRole.paragraph
    return _ROLE_MAP.get(azure_role, BlockRole.paragraph)


def _first_region_polygon(item: Any) -> list[float]:
    regions = getattr(item, "bounding_regions", None) or []
    if not regions:
        return []
    polygon = getattr(regions[0], "polygon", None) or []
    return list(polygon)


def _build_block(
    *,
    content: str,
    polygon: list[float],
    page_w: float,
    page_h: float,
    page_height_pt: float,
    block_id: str,
    reading_order: int,
    role: BlockRole,
    line_count: int = 1,
) -> Block | None:
    text = (content or "").strip()
    if not text:
        return None
    bbox_norm = _polygon_to_bbox_norm(polygon, page_w, page_h)
    # Azure occasionally fails to mark obvious page numbers; promote when our
    # heuristic catches them.
    if role == BlockRole.paragraph and is_page_number(text, bbox_norm):
        role = BlockRole.page_number
    return Block(
        id=block_id,
        role=role,
        align=infer_align(bbox_norm),
        font_size_pt=estimate_font_size(bbox_norm, page_height_pt, line_count),
        reading_order=reading_order,
        bbox_norm=bbox_norm,
        text_en=text,
    )


def _bbox_inside(
    inner: tuple[float, float, float, float],
    outer: tuple[float, float, float, float],
) -> bool:
    return (
        inner[0] >= outer[0]
        and inner[1] >= outer[1]
        and inner[2] <= outer[2]
        and inner[3] <= outer[3]
    )


def _tight_polygon_from_lines(
    outer_polygon: list[float],
    page_lines: list[Any],
    page_w: float,
    page_h: float,
) -> tuple[list[float], int]:
    """Tighten a paragraph/cell polygon to the envelope of the text lines it
    contains. Returns `(tight_polygon, line_count)`.

    Azure paragraph/cell bboxes include vertical line-leading whitespace above
    and below the actual glyphs (~30% padding). Rendering with absolute
    positioning at the padded `top` makes every block drift downward, and
    cumulative drift across a page pushes footer content off-page. Using the
    envelope of the inner `lines[]` removes that padding without losing
    information: the tight polygon still covers every character, and the
    line_count is accurate for font-size estimation.

    Falls back to the input polygon if no lines fall inside it (e.g., when
    the SDK returned a paragraph but no matching line entries).
    """
    if not outer_polygon or not page_lines or page_w <= 0 or page_h <= 0:
        return outer_polygon, 1
    outer = _polygon_to_bbox_norm(outer_polygon, page_w, page_h)
    matched: list[list[float]] = []
    for line in page_lines:
        poly = list(getattr(line, "polygon", None) or [])
        if not poly:
            continue
        xs_n = [x / page_w for x in poly[0::2]]
        ys_n = [y / page_h for y in poly[1::2]]
        if not xs_n or not ys_n:
            continue
        cx = (min(xs_n) + max(xs_n)) / 2.0
        cy = (min(ys_n) + max(ys_n)) / 2.0
        if outer[0] <= cx <= outer[2] and outer[1] <= cy <= outer[3]:
            matched.append(poly)
    if not matched:
        return outer_polygon, 1
    all_xs: list[float] = []
    all_ys: list[float] = []
    for p in matched:
        all_xs.extend(p[0::2])
        all_ys.extend(p[1::2])
    x0, x1 = min(all_xs), max(all_xs)
    y0, y1 = min(all_ys), max(all_ys)
    tight = [x0, y0, x1, y0, x1, y1, x0, y1]
    return tight, len(matched)


def _result_to_vision_page(result: Any, raster: PageRaster) -> VisionPage:
    """Map a one-page `AnalyzeResult` to a `VisionPage`.

    Azure emits a flat paragraph list in reading order plus a separate tables
    list. Paragraph blocks fully contained within a table's bbox are skipped
    so we don't double-emit; their content arrives via `table.cells` instead.
    """
    pages = getattr(result, "pages", None) or []
    page = pages[0] if pages else None
    if page is None:
        return VisionPage(
            page_no=raster.page_no,
            width_pt=raster.width_pt,
            height_pt=raster.height_pt,
            blocks=[],
        )

    page_w = float(getattr(page, "width", 0.0) or 0.0)
    page_h = float(getattr(page, "height", 0.0) or 0.0)
    page_height_pt = raster.height_pt
    block_id_prefix = f"p{raster.page_no}"
    blocks: list[Block] = []
    page_lines = getattr(page, "lines", None) or []

    tables = getattr(result, "tables", None) or []
    table_bboxes = [
        _polygon_to_bbox_norm(_first_region_polygon(t), page_w, page_h) for t in tables
    ]

    for paragraph in getattr(result, "paragraphs", None) or []:
        polygon = _first_region_polygon(paragraph)
        tight_polygon, line_count = _tight_polygon_from_lines(
            polygon, page_lines, page_w, page_h
        )
        block = _build_block(
            content=getattr(paragraph, "content", ""),
            polygon=tight_polygon,
            page_w=page_w,
            page_h=page_h,
            page_height_pt=page_height_pt,
            block_id=f"{block_id_prefix}_b{len(blocks)}",
            reading_order=len(blocks),
            role=_map_role(getattr(paragraph, "role", None)),
            line_count=line_count,
        )
        if block is None:
            continue
        if any(_bbox_inside(block.bbox_norm, tb) for tb in table_bboxes):
            continue
        blocks.append(block)

    for table in tables:
        for cell in getattr(table, "cells", None) or []:
            cell_polygon = _first_region_polygon(cell)
            tight_polygon, line_count = _tight_polygon_from_lines(
                cell_polygon, page_lines, page_w, page_h
            )
            block = _build_block(
                content=getattr(cell, "content", ""),
                polygon=tight_polygon,
                page_w=page_w,
                page_h=page_h,
                page_height_pt=page_height_pt,
                block_id=f"{block_id_prefix}_b{len(blocks)}",
                reading_order=len(blocks),
                role=BlockRole.table_cell,
                line_count=line_count,
            )
            if block is not None:
                blocks.append(block)

    return VisionPage(
        page_no=raster.page_no,
        width_pt=raster.width_pt,
        height_pt=raster.height_pt,
        blocks=blocks,
    )


# ── Per-page extraction (with cache) ────────────────────────────────────────


async def _analyze_png(client: Any, png: bytes) -> Any:
    """Call `prebuilt-layout` on a single page PNG, returning the resolved
    `AnalyzeResult`."""
    from azure.ai.documentintelligence.models import AnalyzeDocumentRequest

    poller = await client.begin_analyze_document(
        model_id="prebuilt-layout",
        body=AnalyzeDocumentRequest(bytes_source=png),
    )
    return await poller.result()


def _load_cached_page(cache_text: str) -> VisionPage | None:
    try:
        return VisionPage.model_validate_json(cache_text)
    except Exception:
        return None


async def _extract_one(
    client: Any,
    raster: PageRaster,
    sem: asyncio.Semaphore,
    job_id: str,
) -> VisionPage:
    async with sem:
        t0 = time.perf_counter()
        sha, cache_key = _cache_key(raster.png, _CACHE_PROVIDER, "json")
        sha_short = f"{_CACHE_PROVIDER}/json/{sha[:12]}"
        cached = _cache_lookup(cache_key, sha_short)
        if cached is not None:
            page = _load_cached_page(cached)
            if page is not None:
                logger.info(
                    "[%s] azure page %d cache hit (%d blocks)",
                    job_id,
                    raster.page_no,
                    len(page.blocks),
                )
                return page
            logger.warning(
                "[%s] azure page %d cache parse failed; re-OCRing",
                job_id,
                raster.page_no,
            )
        try:
            result = await _analyze_png(client, raster.png)
        except Exception as exc:
            logger.warning(
                "[%s] azure page %d failed (%s: %s); emitting empty page",
                job_id,
                raster.page_no,
                type(exc).__name__,
                exc,
            )
            return VisionPage(
                page_no=raster.page_no,
                width_pt=raster.width_pt,
                height_pt=raster.height_pt,
                blocks=[],
            )
        vision = _result_to_vision_page(result, raster)
        _cache_store(cache_key, sha_short, vision.model_dump_json())
        logger.info(
            "[%s] azure page %d took %.2fs (%d blocks)",
            job_id,
            raster.page_no,
            time.perf_counter() - t0,
            len(vision.blocks),
        )
        return vision


async def extract_pages(
    rasters: list[PageRaster],
    lang: str,  # noqa: ARG001 — accepted for parity; Azure auto-detects language.
    concurrency: int,
    job_id: str,
) -> list[VisionPage]:
    """Fan out Azure layout extraction across pages, bounded by `concurrency`."""
    if not rasters:
        return []
    client = await _get_client()
    sem = asyncio.Semaphore(max(1, concurrency))
    results = await asyncio.gather(
        *(_extract_one(client, r, sem, job_id) for r in rasters),
        return_exceptions=False,
    )
    return sorted(results, key=lambda v: v.page_no)
