"""Stage 2: per-page Gemini vision extraction → VisionPage.

One LLM call per page, fanned out with a shared semaphore. Returns results
in page_no order regardless of completion order.
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any

from legal_agent.agents.translation_v2.gemini_client import call_gemini_json
from legal_agent.agents.translation_v2.schemas import PageRaster, VisionPage

logger = logging.getLogger(__name__)

_PROMPT_PATH = Path(__file__).parent / "prompts" / "vision_extract.md"
_PROMPT_TEMPLATE: str | None = None


def _prompt() -> str:
    global _PROMPT_TEMPLATE
    if _PROMPT_TEMPLATE is None:
        _PROMPT_TEMPLATE = _PROMPT_PATH.read_text(encoding="utf-8")
    return _PROMPT_TEMPLATE


def _build_prompt(page: PageRaster, total_pages: int) -> str:
    return (
        _prompt()
        .replace("{page_no}", str(page.page_no))
        .replace("{total_pages}", str(total_pages))
        .replace("{width_pt}", f"{page.width_pt:.1f}")
        .replace("{height_pt}", f"{page.height_pt:.1f}")
    )


async def _extract_one(
    client: Any,
    page: PageRaster,
    total_pages: int,
    model: str,
    sem: asyncio.Semaphore,
    job_id: str,
) -> VisionPage:
    from google.genai import types

    async with sem:
        prompt = _build_prompt(page, total_pages)
        contents = [
            types.Part.from_bytes(data=page.png, mime_type="image/png"),
            prompt,
        ]
        t0 = time.perf_counter()
        result = await call_gemini_json(
            client,
            model,
            contents,
            schema=VisionPage,
            temperature=0.1,
            max_output_tokens=32768,
            retries=1,
            context=f"vision page {page.page_no}",
            thinking_budget=0,
        )
        # The model can drift on page_no / dimensions — force these to match source.
        result = result.model_copy(
            update={
                "page_no": page.page_no,
                "width_pt": page.width_pt,
                "height_pt": page.height_pt,
            }
        )
        logger.info(
            "[%s] vision page %d took %.2fs (%d blocks)",
            job_id,
            page.page_no,
            time.perf_counter() - t0,
            len(result.blocks),
        )
        return result


async def extract_pages(
    client: Any,
    pages: list[PageRaster],
    model: str,
    concurrency: int,
    job_id: str,
) -> list[VisionPage]:
    """Fan out vision extraction across pages with bounded concurrency."""
    if not pages:
        return []
    sem = asyncio.Semaphore(max(1, concurrency))
    total = len(pages)
    results = await asyncio.gather(
        *(_extract_one(client, p, total, model, sem, job_id) for p in pages),
        return_exceptions=False,
    )
    return sorted(results, key=lambda v: v.page_no)
