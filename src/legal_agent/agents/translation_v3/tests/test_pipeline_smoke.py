"""Smoke tests for the v3 pipeline with all external calls mocked.

Verifies:
- engine dispatch (haiku vs sarvam) routes to the correct translator,
- stage failures map to the right ErrorStage,
- metadata carries pipeline=v3 + translate_engine.
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


def _raster(page_no: int = 1) -> PageRaster:
    return PageRaster(
        page_no=page_no,
        png=b"\x89PNG\r\n\x1a\n",
        width_pt=595,
        height_pt=842,
        width_mm=210.0,
        height_mm=297.0,
    )


def _vision_page(page_no: int = 1) -> VisionPage:
    return VisionPage(
        page_no=page_no,
        width_pt=595,
        height_pt=842,
        blocks=[
            Block(
                id=f"p{page_no}_b0",
                bbox_norm=(0.1, 0.1, 0.9, 0.2),
                text_en="The plaintiff hereby submits.",
                font_size_pt=11.5,
                align=BlockAlign.left,
                reading_order=0,
            )
        ],
    )


async def _fake_refine_identity(pages, *_a: Any, **_kw: Any):
    """Stand-in for `refine_pages` that returns the pages unchanged."""
    return pages


def _translated_page(page_no: int = 1) -> TranslatedPage:
    blocks = list(_vision_page(page_no).blocks)
    blocks[0] = blocks[0].model_copy(update={"text_hi": "वादी एतद्द्वारा प्रस्तुत करता है।"})
    return TranslatedPage(
        page_no=page_no,
        width_pt=595,
        height_pt=842,
        blocks=blocks,
    )


@pytest.mark.asyncio
async def test_v3_pipeline_rasterize_failure_maps_to_extraction():
    from legal_agent.agents.translation_v3 import pipeline as p
    from legal_agent.services.job_manager import ErrorStage, StagedError

    async def boom(*_a: Any, **_kw: Any) -> Any:
        raise RuntimeError("pdf unreadable")

    with patch.object(p, "rasterize_pdf", side_effect=boom):
        with pytest.raises(StagedError) as exc:
            await p.translate_pdf_v3(
                source_bytes=b"%PDF-1.4\n",
                filename="x.pdf",
                request=None,  # type: ignore[arg-type]
                job_id="job-rast",
            )
    assert exc.value.stage == ErrorStage.EXTRACTION


@pytest.mark.asyncio
async def test_v3_pipeline_ocr_failure_maps_to_ocr():
    from legal_agent.agents.translation_v3 import pipeline as p
    from legal_agent.services.job_manager import ErrorStage, StagedError

    async def fake_rast(*_a: Any, **_kw: Any):
        return [_raster()]

    async def boom_ocr(*_a: Any, **_kw: Any):
        raise RuntimeError("azure crashed")

    with (
        patch.object(p, "rasterize_pdf", side_effect=fake_rast),
        patch.object(p, "extract_pages", side_effect=boom_ocr),
    ):
        with pytest.raises(StagedError) as exc:
            await p.translate_pdf_v3(
                source_bytes=b"%PDF-1.4\n",
                filename="x.pdf",
                request=None,  # type: ignore[arg-type]
                job_id="job-ocr",
            )
    assert exc.value.stage == ErrorStage.OCR


@pytest.mark.asyncio
async def test_v3_pipeline_translate_failure_maps_to_translation():
    from legal_agent.agents.translation_v3 import pipeline as p
    from legal_agent.agents.translation_v3 import translate_haiku as th
    from legal_agent.services.job_manager import ErrorStage, StagedError

    async def fake_rast(*_a: Any, **_kw: Any):
        return [_raster()]

    async def fake_ocr(*_a: Any, **_kw: Any):
        return [_vision_page()]

    async def fake_gloss(*_a: Any, **_kw: Any) -> dict[str, str]:
        return {}

    async def boom_translate(*_a: Any, **_kw: Any):
        raise RuntimeError("haiku down")

    with (
        patch.object(p, "rasterize_pdf", side_effect=fake_rast),
        patch.object(p, "extract_pages", side_effect=fake_ocr),
        patch.object(p, "refine_pages", side_effect=_fake_refine_identity),
        patch.object(p, "build_glossary", side_effect=fake_gloss),
        patch.object(th, "translate_pages", side_effect=boom_translate),
    ):
        with pytest.raises(StagedError) as exc:
            await p.translate_pdf_v3(
                source_bytes=b"%PDF-1.4\n",
                filename="x.pdf",
                request=None,  # type: ignore[arg-type]
                job_id="job-tr",
            )
    assert exc.value.stage == ErrorStage.TRANSLATION


@pytest.mark.asyncio
async def test_v3_pipeline_haiku_engine_runs_to_completion():
    """Mock every external dependency; verify metadata carries engine + pipeline tag."""
    from legal_agent.agents.translation_v3 import pipeline as p
    from legal_agent.agents.translation_v3 import translate_haiku as th

    async def fake_rast(*_a: Any, **_kw: Any):
        return [_raster()]

    async def fake_ocr(*_a: Any, **_kw: Any):
        return [_vision_page()]

    async def fake_gloss(*_a: Any, **_kw: Any) -> dict[str, str]:
        return {"plaintiff": "वादी"}

    async def fake_translate(*_a: Any, **_kw: Any):
        return [_translated_page()]

    async def fake_render(*_a: Any, **_kw: Any):
        return [b"%PDF-1.4 fake page\n"]

    async def fake_concat(*_a: Any, **_kw: Any) -> bytes:
        return b"%PDF-1.4 fake final\n"

    async def fake_upload(*_a: Any, **_kw: Any):
        return None  # _upload_document_json returns None when s3 unavailable

    # Use a dummy request — defaults engine to "haiku" via settings.
    class _Req:
        translate_engine = "haiku"
        translation_pipeline = "v3"

    with (
        patch.object(p, "rasterize_pdf", side_effect=fake_rast),
        patch.object(p, "extract_pages", side_effect=fake_ocr),
        patch.object(p, "refine_pages", side_effect=_fake_refine_identity),
        patch.object(p, "build_glossary", side_effect=fake_gloss),
        patch.object(th, "translate_pages", side_effect=fake_translate),
        patch.object(p, "render_pages_to_pdfs", side_effect=fake_render),
        patch.object(p, "concat_pdfs", side_effect=fake_concat),
        patch.object(p, "_upload_document_json", side_effect=fake_upload),
    ):
        pdf, meta = await p.translate_pdf_v3(
            source_bytes=b"%PDF-1.4\n",
            filename="x.pdf",
            request=_Req(),  # type: ignore[arg-type]
            job_id="job-ok",
        )

    assert pdf.startswith(b"%PDF")
    assert meta["translation_pipeline"] == "v3"
    assert meta["translate_engine"] == "haiku"
    assert meta["extraction_route"] == "v3_azure_html"
    assert meta["page_count"] == 1
    assert meta["glossary_size"] == 1


@pytest.mark.asyncio
async def test_v3_pipeline_sarvam_engine_routes_correctly():
    from legal_agent.agents.translation_v3 import pipeline as p
    from legal_agent.agents.translation_v3 import translate_sarvam as ts

    async def fake_rast(*_a: Any, **_kw: Any):
        return [_raster()]

    async def fake_ocr(*_a: Any, **_kw: Any):
        return [_vision_page()]

    async def fake_gloss(*_a: Any, **_kw: Any) -> dict[str, str]:
        return {}

    async def fake_sarvam_translate(*_a: Any, **_kw: Any):
        return [_translated_page()]

    async def fake_render(*_a: Any, **_kw: Any):
        return [b"%PDF-1.4 fake page\n"]

    async def fake_concat(*_a: Any, **_kw: Any) -> bytes:
        return b"%PDF-1.4 fake final\n"

    async def fake_upload(*_a: Any, **_kw: Any):
        return None

    class _Req:
        translate_engine = "sarvam"
        translation_pipeline = "v3"

    with (
        patch.object(p, "rasterize_pdf", side_effect=fake_rast),
        patch.object(p, "extract_pages", side_effect=fake_ocr),
        patch.object(p, "refine_pages", side_effect=_fake_refine_identity),
        patch.object(p, "build_glossary", side_effect=fake_gloss),
        patch.object(ts, "translate_pages", side_effect=fake_sarvam_translate),
        patch.object(p, "render_pages_to_pdfs", side_effect=fake_render),
        patch.object(p, "concat_pdfs", side_effect=fake_concat),
        patch.object(p, "_upload_document_json", side_effect=fake_upload),
    ):
        _pdf, meta = await p.translate_pdf_v3(
            source_bytes=b"%PDF-1.4\n",
            filename="x.pdf",
            request=_Req(),  # type: ignore[arg-type]
            job_id="job-sarvam",
        )

    assert meta["translate_engine"] == "sarvam"
