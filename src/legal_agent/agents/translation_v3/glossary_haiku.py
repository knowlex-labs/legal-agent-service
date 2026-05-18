"""Stage 3 (v3): document-level glossary builder via Anthropic Haiku.

Mirrors v2's glossary.py — same baseline YAML, same regex candidate extractors,
same fail-soft merge, same prompts — but the two LLM calls go to Anthropic
Haiku instead of Gemini.

Layers (later wins on collision):
  1. Baseline YAML (curated legal terms)
  2. Legal-term Haiku call (domain vocabulary not in YAML)
  3. Name-transliteration Haiku call (proper nouns from titles/headings/signatures)

Either Haiku call can fail without aborting — the merge always keeps the baseline.
"""

from __future__ import annotations

import asyncio
import logging
import time
import unicodedata
from pathlib import Path

from pydantic import BaseModel

# Reuse v2's regex extractors + baseline YAML loader so we don't drift.
from legal_agent.agents.translation_v2.glossary import (
    _candidate_names,  # type: ignore[attr-defined]
    _candidate_terms,  # type: ignore[attr-defined]
    load_baseline_glossary,
)
from legal_agent.agents.translation_v2.schemas import VisionPage
from legal_agent.agents.translation_v3.anthropic_client import call_anthropic_json

logger = logging.getLogger(__name__)

# Reuse v2's prompt files verbatim (same task, different backend).
_V2_PROMPTS = Path(__file__).parent.parent / "translation_v2" / "prompts"
_LEGAL_PROMPT_PATH = _V2_PROMPTS / "glossary.md"
_NAME_PROMPT_PATH = _V2_PROMPTS / "name_transliterate.md"

_legal_prompt_template: str | None = None
_name_prompt_template: str | None = None


class _GlossaryEntry(BaseModel):
    en: str
    hi: str


class _GlossaryResponse(BaseModel):
    glossary: list[_GlossaryEntry]


class _EntityEntry(BaseModel):
    en: str
    hi: str


class _EntityResponse(BaseModel):
    entities: list[_EntityEntry]


def _legal_prompt(terms: list[str]) -> str:
    global _legal_prompt_template
    if _legal_prompt_template is None:
        _legal_prompt_template = _LEGAL_PROMPT_PATH.read_text(encoding="utf-8")
    return _legal_prompt_template.replace("{terms_block}", "\n".join(f"- {t}" for t in terms))


def _name_prompt(names: list[str]) -> str:
    global _name_prompt_template
    if _name_prompt_template is None:
        _name_prompt_template = _NAME_PROMPT_PATH.read_text(encoding="utf-8")
    return _name_prompt_template.replace("{names_block}", "\n".join(f"- {n}" for n in names))


async def _call_legal_terms(terms: list[str], model: str, job_id: str) -> dict[str, str]:
    if not terms:
        return {}
    try:
        result = await call_anthropic_json(
            model,
            _GlossaryResponse,
            prompt=_legal_prompt(terms),
            tool_name="submit_glossary",
            max_tokens=8192,
            temperature=0.1,
            retries=1,
            context="glossary legal terms",
        )
    except Exception as exc:  # noqa: BLE001 — fail-soft
        logger.warning(
            "[%s] glossary legal-terms call failed (%s: %s); skipping",
            job_id,
            type(exc).__name__,
            exc,
        )
        return {}
    out: dict[str, str] = {}
    for entry in result.glossary:
        en = entry.en.strip()
        hi = unicodedata.normalize("NFC", entry.hi.strip())
        if en and hi:
            out[en] = hi
    return out


async def _call_name_transliterate(names: list[str], model: str, job_id: str) -> dict[str, str]:
    if not names:
        return {}
    try:
        result = await call_anthropic_json(
            model,
            _EntityResponse,
            prompt=_name_prompt(names),
            tool_name="submit_entities",
            max_tokens=4096,
            temperature=0.1,
            retries=1,
            context="glossary names",
        )
    except Exception as exc:  # noqa: BLE001 — fail-soft
        logger.warning(
            "[%s] glossary names call failed (%s: %s); skipping",
            job_id,
            type(exc).__name__,
            exc,
        )
        return {}
    out: dict[str, str] = {}
    for entry in result.entities:
        en = entry.en.strip()
        hi = unicodedata.normalize("NFC", entry.hi.strip())
        if en and hi:
            out[en] = hi
    return out


async def build_glossary(
    pages: list[VisionPage],
    model: str,
    job_id: str,
) -> dict[str, str]:
    """Build the merged {EN→HI} document glossary. Fail-soft on Haiku errors.

    Layers (later wins): baseline YAML → legal-terms Haiku → names Haiku.
    """
    baseline = dict(load_baseline_glossary())
    terms = _candidate_terms(pages)
    names = _candidate_names(pages)

    t0 = time.perf_counter()
    legal_map, name_map = await asyncio.gather(
        _call_legal_terms(terms, model, job_id),
        _call_name_transliterate(names, model, job_id),
    )

    merged = baseline.copy()
    merged.update(legal_map)
    merged.update(name_map)

    logger.info(
        "[%s] v3 glossary: baseline=%d, legal=%d/%d, names=%d/%d → merged=%d (%.2fs)",
        job_id,
        len(baseline),
        len(legal_map),
        len(terms),
        len(name_map),
        len(names),
        len(merged),
        time.perf_counter() - t0,
    )
    return merged
