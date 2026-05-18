"""Unit tests for block_refine_haiku — mapping refinements onto blocks.

The Anthropic SDK isn't installed in test environments; we mock `call_anthropic_json`
and the S3 cache. Only the pure-Python mapping + payload logic is exercised.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from legal_agent.agents.translation_v2.schemas import (
    Block,
    BlockAlign,
    BlockRole,
    BlockWeight,
    PageRaster,
    VisionPage,
)
from legal_agent.agents.translation_v3.block_refine_haiku import (
    _BlockCorrection,
    _RefineResponse,
    _apply_corrections,
    _compact_block_for_prompt,
    refine_pages,
)


def _block(
    id_: str,
    role: BlockRole = BlockRole.paragraph,
    *,
    font_size_pt: float = 11.0,
    bold: bool = False,
    italic: bool = False,
    underline: bool = False,
    text_en: str = "sample text",
    y0: float = 0.10,
    y1: float = 0.15,
) -> Block:
    return Block(
        id=id_,
        role=role,
        align=BlockAlign.left,
        weight=BlockWeight.bold if bold else BlockWeight.normal,
        italic=italic,
        underline=underline,
        font_size_pt=font_size_pt,
        reading_order=0,
        bbox_norm=(0.10, y0, 0.90, y1),
        text_en=text_en,
    )


def _page(blocks: list[Block], page_no: int = 1) -> VisionPage:
    return VisionPage(page_no=page_no, width_pt=595.0, height_pt=842.0, blocks=blocks)


def _raster(page_no: int = 1) -> PageRaster:
    return PageRaster(
        page_no=page_no,
        png=b"\x89PNG\r\n\x1a\nfake-raster",
        width_pt=595.0,
        height_pt=842.0,
        width_mm=210.0,
        height_mm=297.0,
    )


# ── _compact_block_for_prompt ───────────────────────────────────────────────


def test_compact_block_payload_shape():
    b = _block("p1_b0", BlockRole.heading, font_size_pt=12.0, bold=True, text_en="FACTS OF THE CASE")
    payload = _compact_block_for_prompt(b)
    assert payload["id"] == "p1_b0"
    assert payload["role"] == "heading"
    assert payload["font_size_pt"] == 12.0
    assert payload["bold"] is True
    assert payload["italic"] is False
    assert payload["text"] == "FACTS OF THE CASE"


def test_compact_block_truncates_long_text():
    b = _block("p1_b0", text_en="x" * 500)
    payload = _compact_block_for_prompt(b)
    assert len(payload["text"]) == 240


# ── _apply_corrections ──────────────────────────────────────────────────────


def test_apply_corrections_changes_role_to_heading():
    page = _page([_block("p1_b0", BlockRole.paragraph, text_en="FACTS OF THE CASE")])
    corrections = [_BlockCorrection(id="p1_b0", role="heading")]
    out = _apply_corrections(page, corrections)
    assert out.blocks[0].role == BlockRole.heading


def test_apply_corrections_unknown_role_ignored():
    page = _page([_block("p1_b0", BlockRole.paragraph)])
    corrections = [_BlockCorrection(id="p1_b0", role="madeup_role")]
    out = _apply_corrections(page, corrections)
    assert out.blocks[0].role == BlockRole.paragraph


def test_apply_corrections_font_size_category_maps_to_pt():
    page = _page([_block("p1_b0", font_size_pt=20.0)])
    corrections = [_BlockCorrection(id="p1_b0", font_size_category="s")]
    out = _apply_corrections(page, corrections)
    assert out.blocks[0].font_size_pt == 10.5


def test_apply_corrections_unknown_category_ignored():
    page = _page([_block("p1_b0", font_size_pt=20.0)])
    corrections = [_BlockCorrection(id="p1_b0", font_size_category="huge")]
    out = _apply_corrections(page, corrections)
    assert out.blocks[0].font_size_pt == 20.0


def test_apply_corrections_bold_flip():
    page = _page([_block("p1_b0", bold=True)])
    corrections = [_BlockCorrection(id="p1_b0", bold=False)]
    out = _apply_corrections(page, corrections)
    assert out.blocks[0].weight == BlockWeight.normal


def test_apply_corrections_preserves_bbox_and_reading_order():
    page = _page([_block("p1_b0", y0=0.20, y1=0.30)])
    corrections = [_BlockCorrection(id="p1_b0", role="heading", bold=True)]
    out = _apply_corrections(page, corrections)
    assert out.blocks[0].bbox_norm == (0.10, 0.20, 0.90, 0.30)
    assert out.blocks[0].reading_order == 0


def test_apply_corrections_table_cells_locked():
    # Even if the LLM suggests a role change for a table_cell, ignore it —
    # table cells are positional.
    page = _page([_block("p1_b0", BlockRole.table_cell, text_en="A1")])
    corrections = [_BlockCorrection(id="p1_b0", role="paragraph", bold=True)]
    out = _apply_corrections(page, corrections)
    assert out.blocks[0].role == BlockRole.table_cell
    assert out.blocks[0].weight == BlockWeight.normal  # not flipped


def test_apply_corrections_separators_locked():
    page = _page([_block("p1_b0", BlockRole.separator)])
    corrections = [_BlockCorrection(id="p1_b0", role="paragraph")]
    out = _apply_corrections(page, corrections)
    assert out.blocks[0].role == BlockRole.separator


def test_apply_corrections_missing_id_passthrough():
    page = _page([
        _block("p1_b0", BlockRole.paragraph),
        _block("p1_b1", BlockRole.paragraph),
    ])
    corrections = [_BlockCorrection(id="p1_b0", role="heading")]
    out = _apply_corrections(page, corrections)
    assert out.blocks[0].role == BlockRole.heading
    assert out.blocks[1].role == BlockRole.paragraph  # untouched


def test_apply_corrections_null_fields_no_change():
    page = _page([_block("p1_b0", BlockRole.paragraph, bold=True, font_size_pt=11.0)])
    corrections = [_BlockCorrection(id="p1_b0")]  # all None
    out = _apply_corrections(page, corrections)
    assert out.blocks[0].weight == BlockWeight.bold
    assert out.blocks[0].font_size_pt == 11.0


# ── refine_pages: integration via mocked call_anthropic_json ────────────────


@pytest.mark.asyncio
async def test_refine_pages_calls_llm_and_applies_corrections():
    page = _page([
        _block("p1_b0", BlockRole.paragraph, text_en="FACTS OF THE CASE"),
        _block("p1_b1", BlockRole.paragraph, text_en="Body content here"),
    ])
    mock_response = _RefineResponse(
        blocks=[
            _BlockCorrection(id="p1_b0", role="heading", font_size_category="m", bold=True, underline=True),
        ]
    )
    with patch(
        "legal_agent.agents.translation_v3.block_refine_haiku.call_anthropic_json",
        return_value=mock_response,
    ), patch(
        "legal_agent.agents.translation_v3.block_refine_haiku._cache_lookup",
        return_value=None,
    ), patch(
        "legal_agent.agents.translation_v3.block_refine_haiku._cache_store",
        return_value=None,
    ):
        out = await refine_pages(
            [page], [_raster()], model="claude-haiku-4-5", concurrency=1, job_id="t1",
        )
    assert out[0].blocks[0].role == BlockRole.heading
    assert out[0].blocks[0].font_size_pt == 12.0
    assert out[0].blocks[0].weight == BlockWeight.bold
    assert out[0].blocks[0].underline is True
    # Second block had no correction → unchanged.
    assert out[0].blocks[1].role == BlockRole.paragraph


@pytest.mark.asyncio
async def test_refine_pages_uses_cache_when_present():
    page = _page([_block("p1_b0", BlockRole.paragraph, text_en="text")])
    cached_json = _RefineResponse(
        blocks=[_BlockCorrection(id="p1_b0", role="heading")]
    ).model_dump_json()
    with patch(
        "legal_agent.agents.translation_v3.block_refine_haiku.call_anthropic_json",
    ) as mock_call, patch(
        "legal_agent.agents.translation_v3.block_refine_haiku._cache_lookup",
        return_value=cached_json,
    ):
        out = await refine_pages(
            [page], [_raster()], model="haiku", concurrency=1, job_id="t1",
        )
    mock_call.assert_not_called()
    assert out[0].blocks[0].role == BlockRole.heading


@pytest.mark.asyncio
async def test_refine_pages_falls_back_to_azure_on_llm_failure():
    page = _page([_block("p1_b0", BlockRole.paragraph, text_en="text")])
    with patch(
        "legal_agent.agents.translation_v3.block_refine_haiku.call_anthropic_json",
        side_effect=RuntimeError("anthropic down"),
    ), patch(
        "legal_agent.agents.translation_v3.block_refine_haiku._cache_lookup",
        return_value=None,
    ):
        out = await refine_pages(
            [page], [_raster()], model="haiku", concurrency=1, job_id="t1",
        )
    # Original page passes through unchanged on failure.
    assert out[0].blocks[0].role == BlockRole.paragraph


@pytest.mark.asyncio
async def test_refine_pages_skips_pages_with_only_separators():
    page = _page([_block("p1_b0", BlockRole.separator, text_en="")])
    with patch(
        "legal_agent.agents.translation_v3.block_refine_haiku.call_anthropic_json",
    ) as mock_call:
        out = await refine_pages(
            [page], [_raster()], model="haiku", concurrency=1, job_id="t1",
        )
    mock_call.assert_not_called()
    assert out[0].blocks[0].role == BlockRole.separator


@pytest.mark.asyncio
async def test_refine_pages_passes_through_page_with_no_matching_raster():
    page = _page([_block("p1_b0", text_en="orphan")])
    with patch(
        "legal_agent.agents.translation_v3.block_refine_haiku.call_anthropic_json",
    ) as mock_call:
        out = await refine_pages(
            [page], rasters=[], model="haiku", concurrency=1, job_id="t1",
        )
    mock_call.assert_not_called()
    assert out[0].blocks[0].text_en == "orphan"
