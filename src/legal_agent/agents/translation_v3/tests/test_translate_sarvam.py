"""Unit tests for translate_sarvam — glossary freeze/restore, fallback."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from legal_agent.agents.translation_v2.schemas import Block, BlockAlign, TranslatedPage, VisionPage
from legal_agent.agents.translation_v3 import translate_sarvam as ts


def _block(idx: int, text: str) -> Block:
    return Block(
        id=f"b{idx}",
        align=BlockAlign.left,
        font_size_pt=11.0,
        reading_order=idx,
        bbox_norm=(0.1, 0.1, 0.9, 0.2),
        text_en=text,
    )


def test_freeze_glossary_replaces_headwords():
    text = "The plaintiff filed a section 482 petition."
    frozen, by_idx = ts._freeze_glossary(
        text, {"plaintiff": "वादी", "petition": "याचिका"}
    )
    assert "plaintiff" not in frozen
    assert "petition" not in frozen
    assert "[__0000__]" in frozen
    assert "[__0001__]" in frozen
    # Both Hindi targets registered
    assert "वादी" in by_idx.values()
    assert "याचिका" in by_idx.values()


def test_freeze_glossary_longest_first():
    text = "petitioner counsel argued."
    frozen, by_idx = ts._freeze_glossary(
        text, {"petitioner": "याचिकाकर्ता", "petitioner counsel": "याचिकाकर्ता का अधिवक्ता"}
    )
    # Multi-word term wins over the single word.
    assert "petitioner" not in frozen
    assert "counsel" not in frozen
    assert "याचिकाकर्ता का अधिवक्ता" in by_idx.values()


def test_restore_glossary_substitutes_sentinels():
    by_idx = {0: "वादी", 1: "याचिका"}
    out = ts._restore_glossary("एक [__0000__] ने एक [__0001__] दायर की।", by_idx)
    assert "वादी" in out
    assert "याचिका" in out
    assert "__" not in out


def test_restore_glossary_handles_padded_sentinels():
    """Sarvam sometimes pads/strips the zeroes — fuzzy match should still resolve."""
    by_idx = {7: "वादी"}
    out = ts._restore_glossary("एक [__00007__] ने कहा।", by_idx)
    assert "वादी" in out


def test_restore_glossary_strips_unresolved():
    out = ts._restore_glossary("एक [__0099__] बात।", {0: "वादी"})
    assert "__" not in out


@pytest.mark.asyncio
async def test_translate_pages_uses_sarvam_per_block():
    page = VisionPage(
        page_no=1,
        width_pt=595,
        height_pt=842,
        blocks=[_block(0, "The plaintiff hereby submits."), _block(1, "[STAMP]")],
    )

    async def fake_sarvam(text: str, **_kw: Any) -> str:
        # Sarvam should never see plaintiff in cleartext (glossary freezes it).
        assert "plaintiff" not in text
        return text.replace("hereby submits", "एतद्द्वारा प्रस्तुत करता है").replace("The", "वादी")

    with (
        patch.object(ts, "call_sarvam_translate", side_effect=fake_sarvam),
        patch("legal_agent.config.get_settings") as gs,
    ):
        gs.return_value.sarvam_api_key = "test-key"
        gs.return_value.sarvam_translate_model = "test-model"
        result = await ts.translate_pages(
            [page],
            glossary={"plaintiff": "वादी"},
            model="",
            concurrency=1,
            job_id="t-sarvam",
        )

    assert len(result) == 1
    assert isinstance(result[0], TranslatedPage)
    # Non-translatable [STAMP] passes through
    assert result[0].blocks[1].text_hi == "[STAMP]"
    # Glossary headword restored as Hindi
    assert "वादी" in result[0].blocks[0].text_hi


@pytest.mark.asyncio
async def test_translate_pages_per_block_fallback_on_error():
    page = VisionPage(
        page_no=1,
        width_pt=595,
        height_pt=842,
        blocks=[_block(0, "Some long enough legal text here.")],
    )

    async def boom(*_a: Any, **_kw: Any) -> str:
        raise RuntimeError("sarvam 500")

    with (
        patch.object(ts, "call_sarvam_translate", side_effect=boom),
        patch("legal_agent.config.get_settings") as gs,
    ):
        gs.return_value.sarvam_api_key = "test-key"
        gs.return_value.sarvam_translate_model = "test-model"
        result = await ts.translate_pages(
            [page], glossary={}, model="", concurrency=1, job_id="t-fail"
        )
    # Fallback: text_hi = text_en when Sarvam fails
    assert result[0].blocks[0].text_hi == "Some long enough legal text here."
