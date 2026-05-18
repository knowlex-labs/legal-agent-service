"""Unit tests for translate_haiku — prompt structure, cache_control, fallback."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from legal_agent.agents.translation_v2.schemas import Block, BlockAlign, TranslatedPage, VisionPage
from legal_agent.agents.translation_v3 import translate_haiku as th


def _block(idx: int, text: str) -> Block:
    return Block(
        id=f"b{idx}",
        align=BlockAlign.left,
        font_size_pt=11.0,
        reading_order=idx,
        bbox_norm=(0.1, 0.1, 0.9, 0.2),
        text_en=text,
    )


@pytest.mark.asyncio
async def test_translate_one_populates_text_hi_from_mock():
    page = VisionPage(
        page_no=1,
        width_pt=595,
        height_pt=842,
        blocks=[_block(0, "The plaintiff hereby submits.")],
    )

    async def fake_call(model: str, schema, **kwargs: Any):  # noqa: ANN001
        # System prefix must carry the cache_control marker for prompt caching.
        system = kwargs.get("system")
        assert isinstance(system, list)
        assert system[0]["cache_control"] == {"type": "ephemeral"}
        # Glossary must be in the cached prefix.
        assert "वादी" in system[0]["text"]
        # User message carries the per-page blocks JSON tail.
        assert "Page blocks to translate" in kwargs["messages"][0]["content"]
        return th._TranslateResponse(
            blocks=[th._TranslatedBlock(id="b0", text_hi="वादी एतद्द्वारा प्रस्तुत करता है।")]
        )

    with patch.object(th, "call_anthropic_json", side_effect=fake_call):
        result = await th.translate_pages(
            [page],
            glossary={"plaintiff": "वादी"},
            model="claude-haiku-4-5-20251001",
            concurrency=1,
            job_id="t-haiku",
        )

    assert len(result) == 1
    assert isinstance(result[0], TranslatedPage)
    assert "वादी" in result[0].blocks[0].text_hi


@pytest.mark.asyncio
async def test_translate_one_missing_block_falls_back_to_source():
    page = VisionPage(
        page_no=1,
        width_pt=595,
        height_pt=842,
        blocks=[
            _block(0, "The plaintiff hereby submits."),
            _block(1, "Second sentence here."),
        ],
    )

    async def fake_call(_model: str, _schema, **_kw: Any):
        # Only returns b0 — b1 is "missing"
        return th._TranslateResponse(
            blocks=[th._TranslatedBlock(id="b0", text_hi="वादी प्रस्तुत करता है।")]
        )

    with patch.object(th, "call_anthropic_json", side_effect=fake_call):
        result = await th.translate_pages(
            [page], glossary={}, model="claude-haiku-4-5-20251001", concurrency=1, job_id="t-miss"
        )

    blocks = result[0].blocks
    # b0 got Hindi from mock
    assert "वादी" in blocks[0].text_hi
    # b1 missing → falls back to source text
    assert blocks[1].text_hi == "Second sentence here."


@pytest.mark.asyncio
async def test_translate_one_short_circuits_when_no_translatable_blocks():
    page = VisionPage(
        page_no=1,
        width_pt=595,
        height_pt=842,
        blocks=[_block(0, "[STAMP]"), _block(1, "123")],
    )

    # If short-circuit works correctly, call_anthropic_json should never run.
    async def boom(*_a: Any, **_kw: Any):
        raise AssertionError("Anthropic should not be called for non-translatable pages")

    with patch.object(th, "call_anthropic_json", side_effect=boom):
        result = await th.translate_pages(
            [page], glossary={}, model="claude-haiku-4-5-20251001", concurrency=1, job_id="t-skip"
        )
    assert result[0].blocks[0].text_hi == "[STAMP]"
    assert result[0].blocks[1].text_hi == "123"
