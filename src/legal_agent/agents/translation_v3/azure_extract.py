"""Stage 2 (v3): Azure Document Intelligence → VisionPage.

Treats Azure as a SEMANTIC extractor: every block carries a role (title /
heading / paragraph / header / footer / page_number / table_cell), reading
order, text, bold/italic flags (from styleFont), and — for table cells —
the table_id / row_index / column_index needed for the renderer to
reconstruct `<table><tr><td>`.

bbox_norm is still populated (unchanged Block schema, UI editor reads it),
but is NOT used by the flow-layout renderer. Bbox-tightening, row-snap,
separator-rule synthesis, and adjacent-table grouping have all been removed —
they existed only to patch the previous absolute-positioning renderer.

Per-page results are content-hashed and cached in S3 (`OCR_CACHE_ENABLED` /
`OCR_CACHE_PREFIX`).
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any

from legal_agent.agents.translation_v2.schemas import (
    Block,
    BlockRole,
    BlockWeight,
    PageRaster,
    VisionPage,
)
from legal_agent.agents.translation_v3._layout_geometry import (
    infer_align,
    is_page_number,
)
from legal_agent.config import get_settings
from legal_agent.utils.ocr import _cache_key, _cache_lookup, _cache_store

logger = logging.getLogger(__name__)

_CACHE_PROVIDER = "azure_doc_intel_v5"

# Azure prebuilt-layout JSON mode emits these placeholders inline for
# checkbox-style selection marks. They have no glyph in the source; strip them.
_SELECTION_MARK_RE = re.compile(r":(?:selected|unselected):")

# A bold/italic style entry is applied only when it covers at least this
# fraction of the block's total span length. Prevents a tiny bold fragment
# from flipping a 200-character paragraph to bold.
_STYLE_APPLY_THRESHOLD = 0.5

# Azure paragraph.role string → our BlockRole.
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
    """Axis-aligned [0,1]-normalised bbox from Azure's 4-point polygon."""
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


def _span_overlap_length(item_spans: Any, style_spans: Any) -> int:
    """Total length of the intersection between `item_spans` and `style_spans`."""
    total = 0
    for a in item_spans or []:
        a0 = getattr(a, "offset", 0) or 0
        a1 = a0 + (getattr(a, "length", 0) or 0)
        for b in style_spans or []:
            b0 = getattr(b, "offset", 0) or 0
            b1 = b0 + (getattr(b, "length", 0) or 0)
            lo, hi = max(a0, b0), min(a1, b1)
            if hi > lo:
                total += hi - lo
    return total


def _detect_styles(item: Any, doc_styles: list[Any]) -> tuple[bool, bool]:
    """Return `(is_bold, is_italic)` for a paragraph or cell.

    Only flips the flag when matching styles cover ≥ `_STYLE_APPLY_THRESHOLD`
    of the block's span length, so a tiny bold fragment doesn't flip the whole
    paragraph.
    """
    spans = getattr(item, "spans", None) or []
    if not spans or not doc_styles:
        return False, False
    total = sum(getattr(s, "length", 0) or 0 for s in spans)
    if total <= 0:
        return False, False
    bold_overlap = 0
    italic_overlap = 0
    for style in doc_styles:
        style_spans = getattr(style, "spans", None) or []
        if not style_spans:
            continue
        overlap = _span_overlap_length(spans, style_spans)
        if overlap <= 0:
            continue
        if getattr(style, "font_weight", None) == "bold":
            bold_overlap += overlap
        if getattr(style, "font_style", None) == "italic":
            italic_overlap += overlap
    return (
        (bold_overlap / total) >= _STYLE_APPLY_THRESHOLD,
        (italic_overlap / total) >= _STYLE_APPLY_THRESHOLD,
    )


def _build_block(
    *,
    content: str,
    polygon: list[float],
    page_w: float,
    page_h: float,
    block_id: str,
    reading_order: int,
    role: BlockRole,
    is_bold: bool = False,
    is_italic: bool = False,
    is_underline: bool = False,
    table_id: int | None = None,
    row_index: int = 0,
    column_index: int = 0,
    row_span: int = 1,
    column_span: int = 1,
    is_header_cell: bool = False,
) -> Block | None:
    text = (content or "").strip()
    text = _SELECTION_MARK_RE.sub("", text).strip()
    if not text:
        return None
    bbox_norm = _polygon_to_bbox_norm(polygon, page_w, page_h)
    if role == BlockRole.paragraph and is_page_number(text, bbox_norm):
        role = BlockRole.page_number
    # Underline heuristic for ALL-CAPS titles (Azure prebuilt-layout doesn't
    # expose text-decoration). The renderer's CSS underlines title/heading
    # by role anyway; this flag is mostly preserved for downstream consumers.
    if not is_underline and role == BlockRole.title and text == text.upper():
        is_underline = True
    return Block(
        id=block_id,
        role=role,
        align=infer_align(bbox_norm),
        weight=BlockWeight.bold if is_bold else BlockWeight.normal,
        italic=is_italic,
        underline=is_underline,
        # font_size_pt is now decided by CSS-per-role in html_render.py. Keep
        # a sane default in the schema for any tooling that reads it.
        font_size_pt=11.0,
        reading_order=reading_order,
        bbox_norm=bbox_norm,
        text_en=text,
        table_id=table_id,
        row_index=row_index,
        column_index=column_index,
        row_span=row_span,
        column_span=column_span,
        is_header_cell=is_header_cell,
    )


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
    block_id_prefix = f"p{raster.page_no}"
    blocks: list[Block] = []
    doc_styles = getattr(result, "styles", None) or []

    tables = getattr(result, "tables", None) or []
    table_bboxes = [
        _polygon_to_bbox_norm(_first_region_polygon(t), page_w, page_h)
        for t in tables
    ]

    # Paragraphs first — skip those that sit inside any table region.
    for paragraph in getattr(result, "paragraphs", None) or []:
        polygon = _first_region_polygon(paragraph)
        is_bold, is_italic = _detect_styles(paragraph, doc_styles)
        block = _build_block(
            content=getattr(paragraph, "content", ""),
            polygon=polygon,
            page_w=page_w,
            page_h=page_h,
            block_id=f"{block_id_prefix}_b{len(blocks)}",
            reading_order=len(blocks),
            role=_map_role(getattr(paragraph, "role", None)),
            is_bold=is_bold,
            is_italic=is_italic,
        )
        if block is None:
            continue
        if any(_bbox_inside(block.bbox_norm, tb) for tb in table_bboxes):
            continue
        blocks.append(block)

    # Tables: emit one block per cell with table_id + row/col indices so the
    # renderer can reconstruct an HTML <table>.
    for table_idx, table in enumerate(tables):
        for cell in getattr(table, "cells", None) or []:
            cell_polygon = _first_region_polygon(cell)
            is_bold, is_italic = _detect_styles(cell, doc_styles)
            kind = getattr(cell, "kind", None)
            block = _build_block(
                content=getattr(cell, "content", ""),
                polygon=cell_polygon,
                page_w=page_w,
                page_h=page_h,
                block_id=f"{block_id_prefix}_b{len(blocks)}",
                reading_order=len(blocks),
                role=BlockRole.table_cell,
                is_bold=is_bold,
                is_italic=is_italic,
                table_id=table_idx,
                row_index=int(getattr(cell, "row_index", 0) or 0),
                column_index=int(getattr(cell, "column_index", 0) or 0),
                row_span=int(getattr(cell, "row_span", 1) or 1),
                column_span=int(getattr(cell, "column_span", 1) or 1),
                is_header_cell=(kind == "columnHeader"),
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
    """Call `prebuilt-layout` on a single page PNG with `styleFont` so the
    response carries real font_weight / font_style entries."""
    from azure.ai.documentintelligence.models import (
        AnalyzeDocumentRequest,
        DocumentAnalysisFeature,
    )

    poller = await client.begin_analyze_document(
        model_id="prebuilt-layout",
        body=AnalyzeDocumentRequest(bytes_source=png),
        features=[DocumentAnalysisFeature.STYLE_FONT],
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
