"""Unit tests for azure_extract — Azure response → VisionPage mapping.

The Azure SDK isn't installed in test environments; we mock the response
objects with simple namespaces. Only the pure-Python mapping logic is tested.
"""

from __future__ import annotations

from types import SimpleNamespace

from legal_agent.agents.translation_v2.schemas import BlockRole, PageRaster
from legal_agent.agents.translation_v3.azure_extract import (
    _bbox_inside,
    _build_block,
    _detect_styles,
    _map_role,
    _polygon_to_bbox_norm,
    _result_to_vision_page,
    _span_overlap_length,
)


def _raster(page_no: int = 1) -> PageRaster:
    return PageRaster(
        page_no=page_no,
        png=b"\x89PNG\r\n\x1a\n",
        width_pt=595.0,
        height_pt=842.0,
        width_mm=210.0,
        height_mm=297.0,
    )


def _region(polygon: list[float], page_number: int = 1) -> SimpleNamespace:
    return SimpleNamespace(polygon=polygon, page_number=page_number)


def _paragraph(
    content: str,
    polygon: list[float],
    role: str | None = None,
    spans: list[SimpleNamespace] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        content=content,
        role=role,
        bounding_regions=[_region(polygon)],
        spans=spans or [],
    )


def _cell(
    content: str,
    polygon: list[float],
    *,
    kind: str | None = None,
    row_index: int = 0,
    column_index: int = 0,
    row_span: int = 1,
    column_span: int = 1,
    spans: list[SimpleNamespace] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        content=content,
        bounding_regions=[_region(polygon)],
        kind=kind,
        row_index=row_index,
        column_index=column_index,
        row_span=row_span,
        column_span=column_span,
        spans=spans or [],
    )


def _table(polygon: list[float], cells: list[SimpleNamespace]) -> SimpleNamespace:
    return SimpleNamespace(bounding_regions=[_region(polygon)], cells=cells)


def _span(offset: int, length: int) -> SimpleNamespace:
    return SimpleNamespace(offset=offset, length=length)


def _style(
    spans: list[SimpleNamespace],
    *,
    weight: str | None = None,
    style: str | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(spans=spans, font_weight=weight, font_style=style)


def _result(
    *,
    page_w: float = 8.5,
    page_h: float = 11.0,
    paragraphs: list[SimpleNamespace] | None = None,
    tables: list[SimpleNamespace] | None = None,
    styles: list[SimpleNamespace] | None = None,
) -> SimpleNamespace:
    page = SimpleNamespace(width=page_w, height=page_h, page_number=1)
    return SimpleNamespace(
        pages=[page],
        paragraphs=paragraphs or [],
        tables=tables or [],
        styles=styles or [],
    )


# ── _polygon_to_bbox_norm ────────────────────────────────────────────────────


def test_polygon_to_bbox_norm_axis_aligned():
    polygon = [1.0, 1.0, 4.0, 1.0, 4.0, 3.0, 1.0, 3.0]
    out = _polygon_to_bbox_norm(polygon, page_w=8.0, page_h=11.0)
    assert out == (0.125, 1 / 11, 0.5, 3 / 11)


def test_polygon_to_bbox_norm_rotated_polygon():
    polygon = [1.0, 2.0, 5.0, 1.0, 6.0, 4.0, 2.0, 5.0]
    out = _polygon_to_bbox_norm(polygon, page_w=10.0, page_h=10.0)
    assert out == (0.1, 0.1, 0.6, 0.5)


def test_polygon_to_bbox_norm_clamps_to_unit_square():
    polygon = [-1.0, -1.0, 100.0, 100.0, 100.0, -1.0, -1.0, 100.0]
    out = _polygon_to_bbox_norm(polygon, page_w=10.0, page_h=10.0)
    assert all(0.0 <= v <= 1.0 for v in out)


def test_polygon_to_bbox_norm_empty_returns_full_page():
    assert _polygon_to_bbox_norm([], 10.0, 10.0) == (0.0, 0.0, 1.0, 1.0)


def test_polygon_to_bbox_norm_zero_page_returns_full_page():
    assert _polygon_to_bbox_norm([1, 2, 3, 4], 0.0, 10.0) == (0.0, 0.0, 1.0, 1.0)


# ── _map_role ───────────────────────────────────────────────────────────────


def test_map_role_all_azure_values():
    assert _map_role("title") == BlockRole.title
    assert _map_role("sectionHeading") == BlockRole.heading
    assert _map_role("pageHeader") == BlockRole.header
    assert _map_role("pageFooter") == BlockRole.footer
    assert _map_role("pageNumber") == BlockRole.page_number
    assert _map_role("footnote") == BlockRole.paragraph


def test_map_role_none_and_unknown_default_to_paragraph():
    assert _map_role(None) == BlockRole.paragraph
    assert _map_role("someNewRoleAzureAdded") == BlockRole.paragraph


# ── _build_block ────────────────────────────────────────────────────────────


def test_build_block_skips_empty_content():
    block = _build_block(
        content="   ",
        polygon=[0, 0, 1, 0, 1, 1, 0, 1],
        page_w=10.0, page_h=10.0,
        block_id="p1_b0", reading_order=0, role=BlockRole.paragraph,
    )
    assert block is None


def test_build_block_strips_selected_sentinel():
    block = _build_block(
        content=":selected: 2 Some content",
        polygon=[1, 1, 7, 1, 7, 2, 1, 2],
        page_w=10.0, page_h=10.0,
        block_id="p1_b0", reading_order=0, role=BlockRole.paragraph,
    )
    assert block is not None
    assert block.text_en == "2 Some content"


def test_build_block_drops_block_when_only_sentinel():
    block = _build_block(
        content=":selected:  :unselected:",
        polygon=[1, 1, 7, 1, 7, 2, 1, 2],
        page_w=10.0, page_h=10.0,
        block_id="p1_b0", reading_order=0, role=BlockRole.paragraph,
    )
    assert block is None


def test_build_block_promotes_page_number_heuristic():
    block = _build_block(
        content="12",
        polygon=[4.5, 9.5, 5.0, 9.5, 5.0, 9.9, 4.5, 9.9],
        page_w=10.0, page_h=10.0,
        block_id="p1_b0", reading_order=0, role=BlockRole.paragraph,
    )
    assert block is not None
    assert block.role == BlockRole.page_number


def test_build_block_underlines_uppercase_titles():
    block = _build_block(
        content="IN THE HIGH COURT OF MADHYA PRADESH",
        polygon=[1, 1, 7, 1, 7, 2, 1, 2],
        page_w=10.0, page_h=10.0,
        block_id="p1_b0", reading_order=0, role=BlockRole.title,
    )
    assert block is not None
    assert block.underline is True


def test_build_block_table_cell_metadata_round_trips():
    block = _build_block(
        content="S.No.",
        polygon=[0, 0, 1, 0, 1, 1, 0, 1],
        page_w=10.0, page_h=10.0,
        block_id="p1_b3", reading_order=3, role=BlockRole.table_cell,
        table_id=2, row_index=0, column_index=1,
        row_span=1, column_span=2, is_header_cell=True,
    )
    assert block is not None
    assert block.table_id == 2
    assert block.row_index == 0
    assert block.column_index == 1
    assert block.column_span == 2
    assert block.is_header_cell is True


# ── _bbox_inside ────────────────────────────────────────────────────────────


def test_bbox_inside_true():
    assert _bbox_inside((0.2, 0.2, 0.3, 0.3), (0.1, 0.1, 0.5, 0.5)) is True


def test_bbox_inside_partial_overlap_false():
    assert _bbox_inside((0.4, 0.4, 0.6, 0.6), (0.1, 0.1, 0.5, 0.5)) is False


# ── _span_overlap_length / _detect_styles ──────────────────────────────────


def test_span_overlap_length_basic():
    assert _span_overlap_length([_span(0, 10)], [_span(5, 3)]) == 3
    assert _span_overlap_length([_span(0, 5)], [_span(5, 5)]) == 0  # touching, not overlapping
    assert _span_overlap_length([_span(0, 5)], [_span(10, 5)]) == 0


def test_detect_styles_bold():
    paragraph = SimpleNamespace(spans=[_span(0, 30)])
    styles = [_style([_span(0, 30)], weight="bold")]
    assert _detect_styles(paragraph, styles) == (True, False)


def test_detect_styles_italic():
    paragraph = SimpleNamespace(spans=[_span(50, 6)])
    styles = [_style([_span(50, 6)], style="italic")]
    assert _detect_styles(paragraph, styles) == (False, True)


def test_detect_styles_below_threshold_not_bold():
    paragraph = SimpleNamespace(spans=[_span(0, 100)])
    styles = [_style([_span(0, 5)], weight="bold")]
    assert _detect_styles(paragraph, styles) == (False, False)


def test_detect_styles_above_threshold_is_bold():
    paragraph = SimpleNamespace(spans=[_span(0, 100)])
    styles = [_style([_span(0, 60)], weight="bold")]
    assert _detect_styles(paragraph, styles) == (True, False)


def test_detect_styles_no_spans_safe():
    paragraph = SimpleNamespace(spans=None)
    styles = [_style([_span(0, 10)], weight="bold")]
    assert _detect_styles(paragraph, styles) == (False, False)


# ── _result_to_vision_page ──────────────────────────────────────────────────


def test_result_to_vision_page_emits_paragraphs_in_order():
    result = _result(
        paragraphs=[
            _paragraph("Title One", [1, 1, 7, 1, 7, 2, 1, 2], role="title"),
            _paragraph("Some body text.", [1, 3, 7, 3, 7, 4, 1, 4]),
            _paragraph("Another paragraph.", [1, 5, 7, 5, 7, 6, 1, 6]),
        ],
    )
    page = _result_to_vision_page(result, _raster())
    assert len(page.blocks) == 3
    assert [b.role for b in page.blocks] == [
        BlockRole.title,
        BlockRole.paragraph,
        BlockRole.paragraph,
    ]
    assert [b.reading_order for b in page.blocks] == [0, 1, 2]


def test_result_to_vision_page_skips_empty_paragraphs():
    result = _result(
        paragraphs=[
            _paragraph("Real content", [1, 1, 7, 1, 7, 2, 1, 2]),
            _paragraph("   ", [1, 3, 7, 3, 7, 4, 1, 4]),
            _paragraph("", [1, 5, 7, 5, 7, 6, 1, 6]),
        ],
    )
    page = _result_to_vision_page(result, _raster())
    assert len(page.blocks) == 1
    assert page.blocks[0].text_en == "Real content"


def test_result_to_vision_page_table_cells_carry_table_metadata():
    # No more synthetic separator blocks — just one block per cell, tagged
    # with table_id / row_index / column_index for the flow renderer.
    table = _table(
        polygon=[0, 5, 8, 5, 8, 9, 0, 9],
        cells=[
            _cell("A1", [0, 5, 4, 5, 4, 7, 0, 7], row_index=0, column_index=0),
            _cell("B1", [4, 5, 8, 5, 8, 7, 4, 7], row_index=0, column_index=1),
            _cell("A2", [0, 7, 4, 7, 4, 9, 0, 9], row_index=1, column_index=0),
            _cell("B2", [4, 7, 8, 7, 8, 9, 4, 9], row_index=1, column_index=1),
        ],
    )
    result = _result(tables=[table])
    page = _result_to_vision_page(result, _raster())
    assert [b.role for b in page.blocks] == [BlockRole.table_cell] * 4
    assert [b.table_id for b in page.blocks] == [0, 0, 0, 0]
    assert [(b.row_index, b.column_index) for b in page.blocks] == [
        (0, 0), (0, 1), (1, 0), (1, 1),
    ]


def test_result_to_vision_page_marks_header_cells():
    table = _table(
        polygon=[0, 5, 8, 5, 8, 9, 0, 9],
        cells=[
            _cell("S.No.", [0, 5, 4, 5, 4, 6, 0, 6], kind="columnHeader",
                  row_index=0, column_index=0),
            _cell("Page", [4, 5, 8, 5, 8, 6, 4, 6], kind="columnHeader",
                  row_index=0, column_index=1),
            _cell("1.", [0, 7, 4, 7, 4, 8, 0, 8], row_index=1, column_index=0),
            _cell("12-166", [4, 7, 8, 7, 8, 8, 4, 8], row_index=1, column_index=1),
        ],
    )
    result = _result(tables=[table])
    page = _result_to_vision_page(result, _raster())
    flags = [b.is_header_cell for b in page.blocks]
    assert flags == [True, True, False, False]


def test_result_to_vision_page_dedupes_paragraphs_inside_tables():
    # Azure can emit the same content both as a paragraph AND as cells inside
    # a table; the paragraph fully inside the table region should be dropped.
    table = _table(
        polygon=[0, 5, 8, 5, 8, 9, 0, 9],
        cells=[
            _cell("A1", [0, 5, 4, 5, 4, 7, 0, 7], row_index=0, column_index=0),
            _cell("B1", [4, 5, 8, 5, 8, 7, 4, 7], row_index=0, column_index=1),
        ],
    )
    result = _result(
        paragraphs=[
            _paragraph("Outside heading", [1, 1, 7, 1, 7, 2, 1, 2], role="sectionHeading"),
            _paragraph("A1 dup", [1, 5.5, 3, 5.5, 3, 6.5, 1, 6.5]),
        ],
        tables=[table],
    )
    page = _result_to_vision_page(result, _raster())
    assert [b.text_en for b in page.blocks] == ["Outside heading", "A1", "B1"]


def test_result_to_vision_page_empty_pages_returns_empty_blocks():
    result = SimpleNamespace(pages=[], paragraphs=[], tables=[], styles=[])
    page = _result_to_vision_page(result, _raster())
    assert page.blocks == []
    assert page.page_no == 1


# ── Cache provider ─────────────────────────────────────────────────────────


def test_cache_provider_versioned_for_flow_layout_rollout():
    """Bumping the provider key invalidates pre-flow-layout cached JSONs so the
    next request re-OCRs with the new block metadata (table_id, row_index, etc.)."""
    from legal_agent.agents.translation_v3 import azure_extract
    assert azure_extract._CACHE_PROVIDER == "azure_doc_intel_v5"
