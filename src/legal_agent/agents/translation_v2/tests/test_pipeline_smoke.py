"""Smoke tests for the v2 pipeline with all Gemini calls mocked.

Verifies:
- stage ordering / mapping of failures to ErrorStage,
- glossary fail-soft (errors → empty dict, pipeline proceeds),
- per-page translations populate text_hi from the mocked response.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from legal_agent.agents.translation_v2.schemas import (
    Block,
    BlockAlign,
    PageRaster,
    TranslatedPage,
    VisionPage,
)


def _vision_page(page_no: int = 1) -> VisionPage:
    return VisionPage(
        page_no=page_no,
        width_pt=595,
        height_pt=842,
        blocks=[
            Block(
                id=f"p{page_no}-b01",
                bbox_norm=(0.1, 0.1, 0.9, 0.2),
                text_en="The plaintiff hereby submits.",
                font_size_pt=11.5,
                align=BlockAlign.left,
                reading_order=0,
            )
        ],
    )


def _raster(page_no: int = 1) -> PageRaster:
    return PageRaster(
        page_no=page_no,
        png=b"\x89PNG\r\n\x1a\n",  # PNG magic — placeholder
        width_pt=595,
        height_pt=842,
        width_mm=210.0,
        height_mm=297.0,
    )


@pytest.mark.asyncio
async def test_glossary_fail_soft_returns_empty_dict():
    from legal_agent.agents.translation_v2 import glossary as glossary_mod

    async def boom(*_a: Any, **_kw: Any) -> Any:
        raise RuntimeError("gemini down")

    with patch.object(glossary_mod, "call_gemini_json", side_effect=boom):
        result = await glossary_mod.build_glossary(
            client=object(),
            pages=[_vision_page()],
            model="gemini-2.5-pro",
            job_id="job-soft",
        )
    assert result == {}


@pytest.mark.asyncio
async def test_translate_page_uses_mocked_response():
    from legal_agent.agents.translation_v2 import translate as translate_mod
    from legal_agent.agents.translation_v2.translate import _TranslatedBlock, _TranslateResponse

    async def fake_call(*_a: Any, **_kw: Any) -> _TranslateResponse:
        return _TranslateResponse(
            blocks=[_TranslatedBlock(id="p1-b01", text_hi="वादी एतद्द्वारा प्रस्तुत करता है।")]
        )

    with patch.object(translate_mod, "call_gemini_json", side_effect=fake_call):
        translated = await translate_mod.translate_pages(
            client=object(),
            pages=[_vision_page()],
            glossary={"plaintiff": "वादी"},
            model="gemini-2.5-pro",
            concurrency=2,
            job_id="job-tr",
        )

    assert len(translated) == 1
    assert isinstance(translated[0], TranslatedPage)
    assert translated[0].blocks[0].text_hi == "वादी एतद्द्वारा प्रस्तुत करता है।"


@pytest.mark.asyncio
async def test_pipeline_raster_failure_maps_to_extraction_stage():
    from legal_agent.agents.translation_v2 import pipeline as pipeline_mod
    from legal_agent.services.job_manager import ErrorStage, StagedError

    async def boom(*_a: Any, **_kw: Any) -> Any:
        raise RuntimeError("PDF unreadable")

    with patch.object(pipeline_mod, "rasterize_pdf", side_effect=boom):
        with pytest.raises(StagedError) as exc_info:
            await pipeline_mod.translate_pdf_v2(
                source_bytes=b"%PDF-1.4\n",
                filename="x.pdf",
                request=None,  # type: ignore[arg-type]
                job_id="job-rast",
                debug_dir=None,
            )
    assert exc_info.value.stage == ErrorStage.EXTRACTION


@pytest.mark.asyncio
async def test_pipeline_vision_failure_maps_to_ocr_stage():
    from legal_agent.agents.translation_v2 import pipeline as pipeline_mod
    from legal_agent.services.job_manager import ErrorStage, StagedError

    async def fake_rast(*_a: Any, **_kw: Any) -> Any:
        return [_raster(1)]

    async def boom_vision(*_a: Any, **_kw: Any) -> Any:
        raise RuntimeError("gemini vision blew up")

    with (
        patch.object(pipeline_mod, "rasterize_pdf", side_effect=fake_rast),
        patch.object(pipeline_mod, "build_client", return_value=object()),
        patch.object(pipeline_mod, "extract_pages", side_effect=boom_vision),
    ):
        with pytest.raises(StagedError) as exc_info:
            await pipeline_mod.translate_pdf_v2(
                source_bytes=b"%PDF-1.4\n",
                filename="x.pdf",
                request=None,  # type: ignore[arg-type]
                job_id="job-vis",
                debug_dir=None,
            )
    assert exc_info.value.stage == ErrorStage.OCR


@pytest.mark.asyncio
async def test_pipeline_translate_failure_maps_to_translation_stage():
    from legal_agent.agents.translation_v2 import pipeline as pipeline_mod
    from legal_agent.services.job_manager import ErrorStage, StagedError

    async def fake_rast(*_a: Any, **_kw: Any) -> Any:
        return [_raster(1)]

    async def fake_vision(*_a: Any, **_kw: Any) -> Any:
        return [_vision_page(1)]

    async def fake_gloss(*_a: Any, **_kw: Any) -> dict[str, str]:
        return {}

    async def boom_translate(*_a: Any, **_kw: Any) -> Any:
        raise RuntimeError("translation API down")

    with (
        patch.object(pipeline_mod, "rasterize_pdf", side_effect=fake_rast),
        patch.object(pipeline_mod, "build_client", return_value=object()),
        patch.object(pipeline_mod, "extract_pages", side_effect=fake_vision),
        patch.object(pipeline_mod, "build_glossary", side_effect=fake_gloss),
        patch.object(pipeline_mod, "translate_pages", side_effect=boom_translate),
    ):
        with pytest.raises(StagedError) as exc_info:
            await pipeline_mod.translate_pdf_v2(
                source_bytes=b"%PDF-1.4\n",
                filename="x.pdf",
                request=None,  # type: ignore[arg-type]
                job_id="job-tr-fail",
                debug_dir=None,
            )
    assert exc_info.value.stage == ErrorStage.TRANSLATION
