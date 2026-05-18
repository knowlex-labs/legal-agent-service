"""Unit tests for glossary_haiku — fail-soft, merge order, mocked Haiku."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from legal_agent.agents.translation_v2.schemas import Block, BlockAlign, BlockRole, VisionPage
from legal_agent.agents.translation_v3 import glossary_haiku as gh


def _page() -> VisionPage:
    return VisionPage(
        page_no=1,
        width_pt=595,
        height_pt=842,
        blocks=[
            Block(
                id="t0",
                role=BlockRole.title,
                align=BlockAlign.center,
                font_size_pt=18.0,
                reading_order=0,
                bbox_norm=(0.2, 0.05, 0.8, 0.1),
                text_en="Rakesh Sharma vs State of MP",
            ),
            Block(
                id="b0",
                role=BlockRole.paragraph,
                align=BlockAlign.left,
                font_size_pt=11.0,
                reading_order=1,
                bbox_norm=(0.1, 0.2, 0.9, 0.3),
                text_en="The Petitioner has filed this Writ Petition under Article 226.",
            ),
        ],
    )


@pytest.mark.asyncio
async def test_build_glossary_fail_soft_returns_baseline():
    """Both Haiku calls fail → merged glossary == baseline YAML."""

    async def boom(*_a: Any, **_kw: Any) -> Any:
        raise RuntimeError("haiku down")

    with patch.object(gh, "call_anthropic_json", side_effect=boom):
        result = await gh.build_glossary(
            [_page()], model="claude-haiku-4-5-20251001", job_id="g-soft"
        )
    # Baseline YAML must still be present.
    assert result.get("plaintiff") == "वादी"
    assert "section" in result  # baseline shipped key


@pytest.mark.asyncio
async def test_build_glossary_merges_legal_and_names():
    """Each call returns its own entries; merge wins over baseline."""

    async def fake(_model: str, schema, **kwargs: Any):  # noqa: ANN001
        tool = kwargs.get("tool_name", "")
        if tool == "submit_glossary":
            return gh._GlossaryResponse(
                glossary=[gh._GlossaryEntry(en="Writ Petition", hi="रिट याचिका")]
            )
        if tool == "submit_entities":
            return gh._EntityResponse(
                entities=[gh._EntityEntry(en="Rakesh Sharma", hi="राकेश शर्मा")]
            )
        raise AssertionError(f"unexpected tool: {tool}")

    with patch.object(gh, "call_anthropic_json", side_effect=fake):
        result = await gh.build_glossary(
            [_page()], model="claude-haiku-4-5-20251001", job_id="g-merge"
        )
    assert result.get("Writ Petition") == "रिट याचिका"
    assert result.get("Rakesh Sharma") == "राकेश शर्मा"
    # Baseline still present
    assert result.get("plaintiff") == "वादी"


@pytest.mark.asyncio
async def test_one_call_failing_still_returns_merged():
    """If only one of the two Haiku calls fails, the other's entries survive."""

    async def fake(_model: str, schema, **kwargs: Any):  # noqa: ANN001
        if kwargs.get("tool_name") == "submit_glossary":
            return gh._GlossaryResponse(
                glossary=[gh._GlossaryEntry(en="Writ Petition", hi="रिट याचिका")]
            )
        raise RuntimeError("name call failed")

    with patch.object(gh, "call_anthropic_json", side_effect=fake):
        result = await gh.build_glossary(
            [_page()], model="claude-haiku-4-5-20251001", job_id="g-partial"
        )
    assert result.get("Writ Petition") == "रिट याचिका"
    # Baseline still there
    assert "plaintiff" in result
