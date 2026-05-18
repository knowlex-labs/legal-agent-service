"""Stage 4 (v3): per-page translation via Anthropic Haiku with prompt caching.

The v2 translate prompt (translation_v2/prompts/translate.md) is reused
verbatim — same hard rules, same anti-patterns, same glossary contract. We
split it into:

  - **system** (cached): everything except the page's blocks JSON — template,
    full glossary, style anchors, hard rules. Identical across all pages of
    a document, so Anthropic's ephemeral cache hits on every page after the
    first (~90% discount on cached input).
  - **user** (variable): only the per-page blocks_json at the tail.

One Haiku call per page, fanned out under a shared semaphore. Output blocks
are mapped back into the source `Block`s via id; missing / sanitisation-failed
blocks fall back to source text.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import unicodedata
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from legal_agent.agents.translation_v2.keep_english import restore_english_tokens
from legal_agent.agents.translation_v2.sanitize import sanitize_translation
from legal_agent.agents.translation_v2.schemas import Block, TranslatedPage, VisionPage
from legal_agent.agents.translation_v3._translate_common import (
    build_blocks_payload,
    needs_translation,
    pick_style_anchors,
    render_glossary_table,
)
from legal_agent.agents.translation_v3.anthropic_client import call_anthropic_json

logger = logging.getLogger(__name__)

# Reuse v2's prompt verbatim.
_PROMPT_PATH = (
    Path(__file__).parent.parent / "translation_v2" / "prompts" / "translate.md"
)
_PROMPT_TEMPLATE: str | None = None


def _prompt_template() -> str:
    global _PROMPT_TEMPLATE
    if _PROMPT_TEMPLATE is None:
        _PROMPT_TEMPLATE = _PROMPT_PATH.read_text(encoding="utf-8")
    return _PROMPT_TEMPLATE


class _TranslatedBlock(BaseModel):
    id: str
    text_hi: str


class _TranslateResponse(BaseModel):
    blocks: list[_TranslatedBlock]


def _build_system(glossary: dict[str, str], all_pages: list[VisionPage]) -> str:
    """The cached prefix: full prompt with everything EXCEPT the page blocks.

    We leave `{blocks_json}` literal in the system so the model treats the
    user message (the actual blocks JSON) as a continuation. To make the
    cache work, the system must be byte-identical across pages — which it is
    because glossary + style_anchors are document-level, not page-level.
    """
    template = _prompt_template()
    return (
        template.replace("{glossary_table}", render_glossary_table(glossary))
        .replace("{style_anchors}", pick_style_anchors(all_pages))
        .replace("{blocks_json}", "(see user message)")
    )


def _build_user_message(payload: list[dict[str, str]]) -> str:
    return (
        "Page blocks to translate (JSON):\n\n```\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
        + "\n```"
    )


async def _translate_one(
    page: VisionPage,
    all_pages: list[VisionPage],
    glossary: dict[str, str],
    model: str,
    sem: asyncio.Semaphore,
    job_id: str,
) -> TranslatedPage:
    async with sem:
        payload, keep_maps = build_blocks_payload(page)

        # Short-circuit pages with nothing to translate.
        translatable_ids = {b.id for b in page.blocks if needs_translation(b.text_en)}
        if not translatable_ids:
            return TranslatedPage(
                page_no=page.page_no,
                width_pt=page.width_pt,
                height_pt=page.height_pt,
                blocks=[b.model_copy(update={"text_hi": b.text_en}) for b in page.blocks],
            )

        system_blocks: list[dict[str, Any]] = [
            {
                "type": "text",
                "text": _build_system(glossary, all_pages),
                "cache_control": {"type": "ephemeral"},
            }
        ]
        messages = [{"role": "user", "content": _build_user_message(payload)}]

        t0 = time.perf_counter()
        result = await call_anthropic_json(
            model,
            _TranslateResponse,
            messages=messages,
            system=system_blocks,
            tool_name="submit_translations",
            max_tokens=8192,
            temperature=0.2,
            retries=1,
            context=f"translate page {page.page_no}",
        )
        by_id = {tb.id: tb.text_hi for tb in result.blocks}

        translated_blocks: list[Block] = []
        sanitize_issue_count = 0
        for src in page.blocks:
            if not needs_translation(src.text_en):
                translated_blocks.append(src.model_copy(update={"text_hi": src.text_en}))
                continue

            raw_hi = by_id.get(src.id)
            if raw_hi is None or not raw_hi.strip():
                logger.warning(
                    "[%s] translate page %d: block %s missing in response; using source",
                    job_id,
                    page.page_no,
                    src.id,
                )
                raw_hi = src.text_en

            km = keep_maps.get(src.id, {})
            restored = restore_english_tokens(raw_hi, km)
            repaired, issues = sanitize_translation(src.id, src.text_en, restored, km)
            for iss in issues:
                logger.warning(
                    "[%s] sanitize page %d block %s: %s — %s",
                    job_id,
                    page.page_no,
                    iss.block_id,
                    iss.kind,
                    iss.detail,
                )
            sanitize_issue_count += len(issues)
            if any(i.kind in ("fallback_marker", "empty_translation") for i in issues):
                repaired = src.text_en

            final_hi = unicodedata.normalize("NFC", repaired)
            translated_blocks.append(src.model_copy(update={"text_hi": final_hi}))

        logger.info(
            "[%s] haiku translate page %d took %.2fs (%d/%d blocks, %d masked, %d sanitize)",
            job_id,
            page.page_no,
            time.perf_counter() - t0,
            len(translatable_ids),
            len(page.blocks),
            sum(len(km) for km in keep_maps.values()),
            sanitize_issue_count,
        )
        return TranslatedPage(
            page_no=page.page_no,
            width_pt=page.width_pt,
            height_pt=page.height_pt,
            blocks=translated_blocks,
        )


async def translate_pages(
    pages: list[VisionPage],
    glossary: dict[str, str],
    model: str,
    concurrency: int,
    job_id: str,
) -> list[TranslatedPage]:
    """Fan out per-page Haiku translation, bounded by `concurrency`."""
    if not pages:
        return []
    sem = asyncio.Semaphore(max(1, concurrency))
    results = await asyncio.gather(
        *(_translate_one(p, pages, glossary, model, sem, job_id) for p in pages),
        return_exceptions=False,
    )
    return sorted(results, key=lambda p: p.page_no)
