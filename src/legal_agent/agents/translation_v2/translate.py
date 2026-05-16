"""Stage 4: per-page translation of vision blocks.

One Gemini call per page, fanned out with a shared semaphore. Per-page glossary
is filtered to terms actually present on the page (smaller prompt = faster +
cheaper). Style anchors come from the first 1-2 blocks of page 1 to keep the
register consistent across the whole document.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import unicodedata
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from legal_agent.agents.translation_v2.gemini_client import call_gemini_json
from legal_agent.agents.translation_v2.keep_english import (
    mask_english_tokens,
    restore_english_tokens,
)
from legal_agent.agents.translation_v2.sanitize import sanitize_translation
from legal_agent.agents.translation_v2.schemas import Block, TranslatedPage, VisionPage

logger = logging.getLogger(__name__)

_PROMPT_PATH = Path(__file__).parent / "prompts" / "translate.md"
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


def _filter_glossary_for_page(glossary: dict[str, str], page: VisionPage) -> dict[str, str]:
    if not glossary:
        return {}
    joined = " ".join(b.text_en for b in page.blocks).lower()
    return {en: hi for en, hi in glossary.items() if en.lower() in joined}


def _glossary_table(filtered: dict[str, str]) -> str:
    if not filtered:
        return "(no glossary entries for this page)"
    lines = ["| English | Hindi |", "|---|---|"]
    for en, hi in filtered.items():
        lines.append(f"| {en} | {hi} |")
    return "\n".join(lines)


def _style_anchors(all_pages: list[VisionPage]) -> str:
    """Pick 1-2 representative paragraph blocks from page 1 as register anchors."""
    if not all_pages:
        return "(no anchors available)"
    p1 = all_pages[0]
    paragraph_blocks = [
        b for b in p1.blocks if b.role.value in ("paragraph", "clause") and len(b.text_en) > 30
    ]
    chosen = paragraph_blocks[:2] if paragraph_blocks else p1.blocks[:2]
    return "\n\n".join(f"- {b.text_en}" for b in chosen) or "(no anchors available)"


_TRANSLATABLE_PLACEHOLDER_RE = re.compile(
    r"^\s*\[(STAMP|SEAL|LOGO|SIGNATURE|IMAGE)\b", re.IGNORECASE
)


def _needs_translation(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return False
    # Pure numeric / punctuation / very short tokens — keep verbatim.
    alnum = sum(1 for c in stripped if c.isalnum())
    if alnum < 2:
        return False
    return True


def _build_blocks_payload(
    page: VisionPage,
) -> tuple[list[dict[str, str]], dict[str, dict[str, str]]]:
    """Build the per-block payload with English-token masking applied.

    Returns (payload_for_prompt, keep_maps_by_id). Non-translatable blocks
    pass through unmasked so the LLM sees the literal text.
    """
    payload: list[dict[str, str]] = []
    keep_maps: dict[str, dict[str, str]] = {}
    for b in page.blocks:
        if not _needs_translation(b.text_en):
            payload.append({"id": b.id, "role": b.role.value, "text_en": b.text_en})
            continue
        masked_text, km = mask_english_tokens(b.text_en)
        if km:
            keep_maps[b.id] = km
        payload.append({"id": b.id, "role": b.role.value, "text_en": masked_text})
    return payload, keep_maps


async def _translate_one(
    client: Any,
    page: VisionPage,
    all_pages: list[VisionPage],
    glossary: dict[str, str],
    model: str,
    sem: asyncio.Semaphore,
    job_id: str,
) -> TranslatedPage:
    async with sem:
        filtered = _filter_glossary_for_page(glossary, page)
        payload, keep_maps = _build_blocks_payload(page)
        prompt = (
            _prompt_template()
            .replace("{glossary_table}", _glossary_table(filtered))
            .replace("{style_anchors}", _style_anchors(all_pages))
            .replace("{blocks_json}", json.dumps(payload, ensure_ascii=False, indent=2))
        )

        # If every block on the page is non-translatable (empty / numeric / placeholders),
        # short-circuit to avoid wasting an LLM call.
        translatable_ids = {b.id for b in page.blocks if _needs_translation(b.text_en)}
        if not translatable_ids:
            translated_blocks = [b.model_copy(update={"text_hi": b.text_en}) for b in page.blocks]
            return TranslatedPage(
                page_no=page.page_no,
                width_pt=page.width_pt,
                height_pt=page.height_pt,
                blocks=translated_blocks,
            )

        t0 = time.perf_counter()
        result = await call_gemini_json(
            client,
            model,
            [prompt],
            schema=_TranslateResponse,
            temperature=0.2,
            max_output_tokens=32768,
            retries=1,
            context=f"translate page {page.page_no}",
        )
        by_id = {tb.id: tb.text_hi for tb in result.blocks}

        translated_blocks: list[Block] = []
        sanitize_issue_count = 0
        for src in page.blocks:
            if not _needs_translation(src.text_en):
                # Non-translatable: copy text_en into text_hi verbatim.
                translated_blocks.append(src.model_copy(update={"text_hi": src.text_en}))
                continue
            raw_hi = by_id.get(src.id)
            missing = raw_hi is None or not raw_hi.strip()
            if missing:
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
            # Un-repairable kinds: fall back to source text.
            if any(i.kind in ("fallback_marker", "empty_translation") for i in issues):
                repaired = src.text_en

            final_hi = unicodedata.normalize("NFC", repaired)
            translated_blocks.append(src.model_copy(update={"text_hi": final_hi}))

        logger.info(
            "[%s] translate page %d took %.2fs (%d/%d blocks, %d masked, %d sanitize issues)",
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
    client: Any,
    pages: list[VisionPage],
    glossary: dict[str, str],
    model: str,
    concurrency: int,
    job_id: str,
) -> list[TranslatedPage]:
    """Fan out per-page translation with bounded concurrency."""
    if not pages:
        return []
    sem = asyncio.Semaphore(max(1, concurrency))
    results = await asyncio.gather(
        *(_translate_one(client, p, pages, glossary, model, sem, job_id) for p in pages),
        return_exceptions=False,
    )
    return sorted(results, key=lambda p: p.page_no)
