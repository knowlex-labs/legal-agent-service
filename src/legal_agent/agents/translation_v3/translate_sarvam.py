"""Stage 4 (v3, sarvam variant): per-block translation via Sarvam REST formal.

Each block is translated individually so the EN→HI mapping at the block level
stays clean (matches the Haiku translator's output shape). Glossary
consistency is enforced via the same sentinel mechanism v1 uses: replace each
English headword with `[__NNNN__]` before sending to Sarvam, then substitute
the Hindi target back after the call. Sarvam's `mode=formal` preserves the
sentinels verbatim (verified in v1's production usage).

Cost note: Sarvam REST is ₹0.002/char, so a 3000-char page costs ~₹6 — this
engine can exceed the 5₹/page ceiling on dense pages. Prefer Haiku.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
import unicodedata
from typing import Any

from legal_agent.agents.translation.sarvam_translate import (
    SARVAM_LANG_CODES,
    call_sarvam_translate,
    clean_sarvam_translate_output,
)
from legal_agent.agents.translation_v2.keep_english import (
    mask_english_tokens,
    restore_english_tokens,
)
from legal_agent.agents.translation_v2.sanitize import sanitize_translation
from legal_agent.agents.translation_v2.schemas import Block, TranslatedPage, VisionPage
from legal_agent.agents.translation_v3._translate_common import needs_translation
from legal_agent.config import get_settings

logger = logging.getLogger(__name__)

_SENTINEL_FUZZY_RE = re.compile(r"\[__0*(\d+)__\]")


def _freeze_glossary(text: str, glossary: dict[str, str]) -> tuple[str, dict[int, str]]:
    """Replace each English headword with `[__NNNN__]`. Longest-first to avoid
    sub-word matches stealing multi-word terms.

    Returns (frozen_text, {idx → hindi_target}).
    """
    if not text or not glossary:
        return text, {}

    by_idx: dict[int, str] = {}
    next_idx = 0
    # Sort longest first so "petitioner counsel" wins over "petitioner".
    for en in sorted(glossary.keys(), key=len, reverse=True):
        if not en or en not in text:
            # Cheap pre-check before regex compile.
            if not re.search(rf"\b{re.escape(en)}\b", text):
                continue
        pattern = re.compile(rf"\b{re.escape(en)}\b")
        hi = glossary[en]

        def _repl(_m: re.Match, _hi: str = hi) -> str:
            nonlocal next_idx
            sid = f"[__{next_idx:04d}__]"
            by_idx[next_idx] = _hi
            next_idx += 1
            return sid

        text = pattern.sub(_repl, text)
    return text, by_idx


def _restore_glossary(translated: str, by_idx: dict[int, str]) -> str:
    """Substitute `[__NNNN__]` sentinels back to their Hindi targets.

    Tolerates Sarvam padding/stripping a zero (e.g. `[__00007__]` or `[__7__]`).
    Unresolved sentinels are stripped with a warning.
    """
    if not translated:
        return translated

    def _repl(m: re.Match) -> str:
        return by_idx.get(int(m.group(1)), m.group(0))

    out = _SENTINEL_FUZZY_RE.sub(_repl, translated)
    leftover = _SENTINEL_FUZZY_RE.findall(out)
    if leftover:
        logger.warning("[sarvam-v3] stripping %d unresolved sentinels", len(leftover))
        out = _SENTINEL_FUZZY_RE.sub("", out)
    return unicodedata.normalize("NFC", out)


async def _translate_one_block(
    text_en: str,
    glossary: dict[str, str],
    api_key: str,
    model: str | None,
) -> str:
    """Translate one block via Sarvam REST. Returns Hindi text, glossary-restored."""
    if not text_en or not text_en.strip():
        return text_en
    frozen, by_idx = _freeze_glossary(text_en, glossary)
    raw_hi = await call_sarvam_translate(
        frozen,
        source_code=SARVAM_LANG_CODES["english"],
        target_code=SARVAM_LANG_CODES["hindi"],
        api_key=api_key,
        model=model,
    )
    cleaned = clean_sarvam_translate_output(raw_hi)
    return _restore_glossary(cleaned, by_idx)


async def _translate_one_page(
    page: VisionPage,
    glossary: dict[str, str],
    api_key: str,
    model: str | None,
    block_concurrency: int,
    job_id: str,
) -> TranslatedPage:
    t0 = time.perf_counter()

    block_sem = asyncio.Semaphore(max(1, block_concurrency))

    async def _process(src: Block) -> Block:
        if not needs_translation(src.text_en):
            return src.model_copy(update={"text_hi": src.text_en})

        masked, km = mask_english_tokens(src.text_en)
        async with block_sem:
            try:
                raw_hi = await _translate_one_block(masked, glossary, api_key, model)
            except Exception as exc:  # noqa: BLE001 — per-block fallback
                logger.warning(
                    "[%s] sarvam page %d block %s failed (%s: %s); using source",
                    job_id,
                    page.page_no,
                    src.id,
                    type(exc).__name__,
                    exc,
                )
                raw_hi = src.text_en

        restored = restore_english_tokens(raw_hi, km)
        repaired, issues = sanitize_translation(src.id, src.text_en, restored, km)
        if any(i.kind in ("fallback_marker", "empty_translation") for i in issues):
            repaired = src.text_en
        final_hi = unicodedata.normalize("NFC", repaired)
        return src.model_copy(update={"text_hi": final_hi})

    translated_blocks = await asyncio.gather(*(_process(b) for b in page.blocks))

    logger.info(
        "[%s] sarvam translate page %d took %.2fs (%d blocks)",
        job_id,
        page.page_no,
        time.perf_counter() - t0,
        len(translated_blocks),
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
    model: str,  # noqa: ARG001 — unused for Sarvam (engine = "haiku"|"sarvam"); kept for signature parity
    concurrency: int,
    job_id: str,
) -> list[TranslatedPage]:
    """Fan out per-page Sarvam translation, bounded by `concurrency`."""
    if not pages:
        return []
    settings = get_settings()
    api_key = settings.sarvam_api_key
    if not api_key:
        raise RuntimeError("translation_v3 translate_engine=sarvam requires SARVAM_API_KEY")
    sarvam_model = getattr(settings, "sarvam_translate_model", None)

    page_sem = asyncio.Semaphore(max(1, concurrency))
    # Per-block concurrency inside a page — Sarvam tolerates a few parallel calls.
    block_concurrency = 4

    async def _gated_page(p: VisionPage) -> TranslatedPage:
        async with page_sem:
            return await _translate_one_page(
                p, glossary, api_key, sarvam_model, block_concurrency, job_id
            )

    results: list[Any] = await asyncio.gather(*(_gated_page(p) for p in pages))
    return sorted(results, key=lambda p: p.page_no)
