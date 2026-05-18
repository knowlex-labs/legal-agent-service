"""Unit tests for azure_extract — response-to-Block mapping.

The Azure SDK isn't installed in test environments; we mock its response
objects with simple namespaces. Only the pure-Python mapping logic is
exercised here — live API calls are exercised by the pipeline smoke test
with a mocked `extract_pages`.
"""

from __future__ import annotations

from types import SimpleNamespace

from legal_agent.agents.translation_v2.schemas import BlockRole, PageRaster
from legal_agent.agents.translation_v3.azure_extract import (
    _bbox_inside,
    _build_block,
    _map_role,
    _polygon_to_bbox_norm,
    _result_to_vision_page,
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


def _paragraph(content: str, polygon: list[float], role: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(content=content, role=role, bounding_regions=[_region(polygon)])


def _cell(content: str, polygon: list[float]) -> SimpleNamespace:
    return SimpleNamespace(content=content, bounding_regions=[_region(polygon)])


def _table(polygon: list[float], cells: list[SimpleNamespace]) -> SimpleNamespace:
    return SimpleNamespace(bounding_regions=[_region(polygon)], cells=cells)


def _result(
    *,
    page_w: float = 8.5,
    page_h: float = 11.0,
    paragraphs: list[SimpleNamespace] | None = None,
    tables: list[SimpleNamespace] | None = None,
) -> SimpleNamespace:
    page = SimpleNamespace(width=page_w, height=page_h, page_number=1)
    return SimpleNamespace(
        pages=[page],
        paragraphs=paragraphs or [],
        tables=tables or [],
    )


# ── _polygon_to_bbox_norm ────────────────────────────────────────────────────


def test_polygon_to_bbox_norm_axis_aligned():
    # 4-point polygon for an axis-aligned rect (1,1) → (4,3) on an 8x11 page.
    polygon = [1.0, 1.0, 4.0, 1.0, 4.0, 3.0, 1.0, 3.0]
    out = _polygon_to_bbox_norm(polygon, page_w=8.0, page_h=11.0)
    assert out == (0.125, 1 / 11, 0.5, 3 / 11)


def test_polygon_to_bbox_norm_rotated_polygon():
    # A skewed quad — bbox should be the min/max envelope.
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
        page_w=10.0,
        page_h=10.0,
        page_height_pt=842.0,
        block_id="p1_b0",
        reading_order=0,
        role=BlockRole.paragraph,
    )
    assert block is None


def test_build_block_assigns_role_and_normalises_bbox():
    block = _build_block(
        content="AGREEMENT",
        polygon=[1.0, 1.0, 4.0, 1.0, 4.0, 2.0, 1.0, 2.0],
        page_w=10.0,
        page_h=10.0,
        page_height_pt=842.0,
        block_id="p1_b3",
        reading_order=3,
        role=BlockRole.title,
    )
    assert block is not None
    assert block.role == BlockRole.title
    assert block.id == "p1_b3"
    assert block.text_en == "AGREEMENT"
    assert block.bbox_norm == (0.1, 0.1, 0.4, 0.2)


def test_build_block_promotes_page_number_heuristic():
    # Short numeric content with bbox in the bottom 10% → page_number.
    block = _build_block(
        content="12",
        polygon=[4.5, 9.5, 5.0, 9.5, 5.0, 9.9, 4.5, 9.9],
        page_w=10.0,
        page_h=10.0,
        page_height_pt=842.0,
        block_id="p1_b0",
        reading_order=0,
        role=BlockRole.paragraph,
    )
    assert block is not None
    assert block.role == BlockRole.page_number


def test_build_block_does_not_promote_when_role_already_set():
    # Even if it looks like a page number, an explicit role (e.g. footer) wins.
    block = _build_block(
        content="12",
        polygon=[4.5, 9.5, 5.0, 9.5, 5.0, 9.9, 4.5, 9.9],
        page_w=10.0,
        page_h=10.0,
        page_height_pt=842.0,
        block_id="p1_b0",
        reading_order=0,
        role=BlockRole.footer,
    )
    assert block is not None
    assert block.role == BlockRole.footer


# ── _bbox_inside ────────────────────────────────────────────────────────────


def test_bbox_inside_true():
    inner = (0.2, 0.2, 0.3, 0.3)
    outer = (0.1, 0.1, 0.5, 0.5)
    assert _bbox_inside(inner, outer) is True


def test_bbox_inside_partial_overlap_false():
    inner = (0.4, 0.4, 0.6, 0.6)
    outer = (0.1, 0.1, 0.5, 0.5)
    assert _bbox_inside(inner, outer) is False


def test_bbox_inside_identical_true():
    box = (0.2, 0.2, 0.5, 0.5)
    assert _bbox_inside(box, box) is True


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
    assert [b.id for b in page.blocks] == ["p1_b0", "p1_b1", "p1_b2"]


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


def test_result_to_vision_page_expands_table_cells():
    table = _table(
        polygon=[0, 5, 8, 5, 8, 9, 0, 9],
        cells=[
            _cell("A1", [0, 5, 4, 5, 4, 7, 0, 7]),
            _cell("B1", [4, 5, 8, 5, 8, 7, 4, 7]),
            _cell("A2", [0, 7, 4, 7, 4, 9, 0, 9]),
            _cell("B2", [4, 7, 8, 7, 8, 9, 4, 9]),
        ],
    )
    result = _result(
        paragraphs=[_paragraph("Heading", [1, 1, 7, 1, 7, 2, 1, 2], role="sectionHeading")],
        tables=[table],
    )
    page = _result_to_vision_page(result, _raster())
    # 1 heading + 4 table cells
    assert len(page.blocks) == 5
    assert page.blocks[0].role == BlockRole.heading
    assert all(b.role == BlockRole.table_cell for b in page.blocks[1:])
    assert [b.text_en for b in page.blocks[1:]] == ["A1", "B1", "A2", "B2"]


def test_result_to_vision_page_dedupes_paragraphs_inside_tables():
    # Azure sometimes emits the same content both as a paragraph AND as cells
    # inside a table. Paragraphs fully inside the table's bbox should be dropped.
    table = _table(
        polygon=[0, 5, 8, 5, 8, 9, 0, 9],
        cells=[
            _cell("A1", [0, 5, 4, 5, 4, 7, 0, 7]),
            _cell("B1", [4, 5, 8, 5, 8, 7, 4, 7]),
        ],
    )
    result = _result(
        paragraphs=[
            _paragraph("Outside heading", [1, 1, 7, 1, 7, 2, 1, 2], role="sectionHeading"),
            # This paragraph sits entirely inside the table region → should be skipped.
            _paragraph("A1 dup", [1, 5.5, 3, 5.5, 3, 6.5, 1, 6.5]),
        ],
        tables=[table],
    )
    page = _result_to_vision_page(result, _raster())
    texts = [b.text_en for b in page.blocks]
    assert texts == ["Outside heading", "A1", "B1"]


def test_result_to_vision_page_empty_pages_returns_empty_blocks():
    result = SimpleNamespace(pages=[], paragraphs=[], tables=[])
    page = _result_to_vision_page(result, _raster())
    assert page.blocks == []
    assert page.page_no == 1
