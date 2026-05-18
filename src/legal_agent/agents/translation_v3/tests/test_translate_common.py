"""Unit tests for _translate_common helpers."""

from __future__ import annotations

import pytest

from legal_agent.agents.translation_v2.schemas import Block, BlockAlign, BlockRole, VisionPage
from legal_agent.agents.translation_v3._translate_common import (
    build_blocks_payload,
    needs_translation,
    pick_style_anchors,
    render_glossary_table,
)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("hello world", True),
        ("", False),
        ("   ", False),
        ("1", False),
        ("[STAMP: blah]", False),
        ("[LOGO]", False),
        ("[Stamp]", False),
        ("ok", True),
        ("123", False),
    ],
)
def test_needs_translation(text: str, expected: bool):
    assert needs_translation(text) is expected


def _block(idx: int, text: str, role: BlockRole = BlockRole.paragraph) -> Block:
    return Block(
        id=f"b{idx}",
        role=role,
        align=BlockAlign.left,
        font_size_pt=11.0,
        reading_order=idx,
        bbox_norm=(0.1, 0.1, 0.9, 0.2),
        text_en=text,
    )


def test_build_blocks_payload_skips_non_translatable():
    page = VisionPage(
        page_no=1,
        width_pt=595,
        height_pt=842,
        blocks=[
            _block(0, "[STAMP]"),
            _block(1, "The plaintiff shall file."),
        ],
    )
    payload, _km = build_blocks_payload(page)
    assert len(payload) == 2
    # Non-translatable passes through unmasked
    assert payload[0]["text_en"] == "[STAMP]"
    # Translatable block gets text_en preserved (may be masked but should contain key word)
    assert "plaintiff" in payload[1]["text_en"] or "__KEEP" in payload[1]["text_en"]


def test_render_glossary_table_empty():
    assert "empty" in render_glossary_table({}).lower()


def test_render_glossary_table_has_rows():
    out = render_glossary_table({"plaintiff": "वादी", "section": "धारा"})
    assert "plaintiff" in out
    assert "वादी" in out
    assert out.count("\n") >= 3  # header + sep + 2 rows


def test_pick_style_anchors_paragraph_first():
    page = VisionPage(
        page_no=1,
        width_pt=595,
        height_pt=842,
        blocks=[
            _block(0, "Title here", role=BlockRole.title),
            _block(1, "A long enough paragraph block that demonstrates the document register.", role=BlockRole.paragraph),
        ],
    )
    out = pick_style_anchors([page])
    assert "long enough paragraph" in out


def test_pick_style_anchors_no_pages():
    assert "no anchors" in pick_style_anchors([])
