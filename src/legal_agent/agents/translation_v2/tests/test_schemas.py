"""Schema validation: bbox clamping, NFC normalization, role/align/weight defaults."""

from __future__ import annotations

import unicodedata

import pytest

from legal_agent.agents.translation_v2.schemas import (
    Block,
    BlockAlign,
    BlockRole,
    BlockWeight,
    TranslatedPage,
    VisionPage,
)


def _block(**overrides: object) -> Block:
    base: dict[str, object] = {
        "id": "p1-b01",
        "bbox_norm": (0.1, 0.1, 0.5, 0.2),
        "text_en": "Hello world",
        "font_size_pt": 11.0,
    }
    base.update(overrides)
    return Block(**base)  # type: ignore[arg-type]


def test_block_defaults():
    b = _block()
    assert b.role == BlockRole.paragraph
    assert b.align == BlockAlign.left
    assert b.weight == BlockWeight.normal
    assert b.italic is False
    assert b.underline is False
    assert b.text_hi is None


def test_bbox_clamped_to_unit_square():
    b = _block(bbox_norm=(-0.1, 0.2, 1.5, 0.9))
    assert b.bbox_norm == (0.0, 0.2, 1.0, 0.9)


def test_bbox_degenerate_expands():
    b = _block(bbox_norm=(0.5, 0.5, 0.5, 0.5))
    x0, y0, x1, y1 = b.bbox_norm
    assert x1 > x0
    assert y1 > y0


def test_text_hi_nfc_normalized():
    # NFD form of "नमस्ते" (decomposed) → must come back as NFC
    nfd = unicodedata.normalize("NFD", "नमस्ते")
    b = _block(text_hi=nfd)
    assert b.text_hi == unicodedata.normalize("NFC", "नमस्ते")


def test_vision_page_requires_positive_dims():
    with pytest.raises(Exception):
        VisionPage(page_no=1, width_pt=0, height_pt=842, blocks=[])


def test_translated_page_inherits_shape():
    page = TranslatedPage(
        page_no=1,
        width_pt=595,
        height_pt=842,
        blocks=[_block(text_hi="नमस्ते")],
    )
    assert page.blocks[0].text_hi == "नमस्ते"


def test_font_size_must_be_positive():
    with pytest.raises(Exception):
        _block(font_size_pt=0.0)
