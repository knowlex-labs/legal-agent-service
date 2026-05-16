"""Stage 3: document-level glossary builder.

Three layers merged into a single {en → hi} dict (later wins on collision):

  1. Baseline YAML — curated legal terms shipped in `data/legal_glossary_en_hi.yaml`.
  2. Per-document legal-term LLM call — captures domain-specific vocabulary
     not in the baseline (statute names, party-style headings, etc.).
  3. Per-document name-transliteration LLM call — proper nouns extracted from
     title/heading/signature blocks transliterated into formal Devanagari.

Both LLM calls run in parallel via asyncio.gather. Either can fail without
aborting — the merge always keeps the baseline.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

from legal_agent.agents.translation_v2.gemini_client import call_gemini_json
from legal_agent.agents.translation_v2.schemas import BlockRole, VisionPage

logger = logging.getLogger(__name__)

_LEGAL_PROMPT_PATH = Path(__file__).parent / "prompts" / "glossary.md"
_NAME_PROMPT_PATH = Path(__file__).parent / "prompts" / "name_transliterate.md"
_BASELINE_YAML_PATH = Path(__file__).parent / "data" / "legal_glossary_en_hi.yaml"

_legal_prompt_template: str | None = None
_name_prompt_template: str | None = None

_MAX_CANDIDATE_TERMS = 400
_MAX_CANDIDATE_NAMES = 200

# Title-Case 1-4 word phrase OR ALL-CAPS 1-4 word phrase. Used for legal terms.
_PHRASE_RE = re.compile(
    r"\b("
    r"(?:[A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+){0,3})"
    r"|(?:[A-Z]{2,}(?:\s+[A-Z]{2,}){0,3})"
    r")\b"
)

# A "name-like" phrase: 1-4 Title Case words, allowing trailing initials/periods.
# We restrict NAME extraction to roles where names appear (title, heading,
# signature, header, page_number) so we don't pull "Section" or "Court" from
# body prose — those go through the legal-term path.
_NAME_RE = re.compile(r"\b([A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z.]+){0,3})\b")

# Roles where proper-noun extraction is most useful. Body paragraphs are
# excluded — names appearing there will still match via the title/heading
# blocks that introduce them.
_NAME_ROLES: frozenset[BlockRole] = frozenset(
    {
        BlockRole.title,
        BlockRole.heading,
        BlockRole.subheading,
        BlockRole.signature,
        BlockRole.header,
    }
)

_STOPWORDS: frozenset[str] = frozenset(
    {
        "I",
        "A",
        "An",
        "The",
        "And",
        "Or",
        "But",
        "If",
        "Of",
        "In",
        "On",
        "At",
        "To",
        "By",
        "For",
        "With",
        "From",
        "As",
        "Is",
        "Are",
        "Was",
        "Were",
        "Be",
        "Been",
        "Mr",
        "Mrs",
        "Ms",
        "Dr",
        "Shri",
        "Smt",
    }
)


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


# ── Baseline YAML loader ─────────────────────────────────────────────────


@lru_cache(maxsize=1)
def load_baseline_glossary() -> dict[str, str]:
    """Load the curated YAML glossary. Keys preserved as-typed (case-insensitive
    lookup is the caller's job)."""
    try:
        raw = yaml.safe_load(_BASELINE_YAML_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        logger.warning("baseline glossary YAML missing: %s", _BASELINE_YAML_PATH)
        return {}
    except yaml.YAMLError as exc:
        logger.error("baseline glossary YAML invalid (%s); ignoring", exc)
        return {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for k, v in raw.items():
        if not isinstance(k, str) or not isinstance(v, str):
            continue
        en = k.strip()
        hi = unicodedata.normalize("NFC", v.strip())
        if en and hi:
            out[en] = hi
    return out


# ── Candidate extraction ─────────────────────────────────────────────────


def _strip_inline_html(s: str) -> str:
    return re.sub(r"<[^>]+>", "", s)


def _candidate_terms(pages: list[VisionPage]) -> list[str]:
    """Generic Title-Case / ALL-CAPS phrases across all blocks (legal terms)."""
    seen: dict[str, None] = {}
    seen_lower: set[str] = set()
    for page in pages:
        for block in page.blocks:
            text = _strip_inline_html(block.text_en)
            for match in _PHRASE_RE.finditer(text):
                term = match.group(1).strip()
                if not term or term in _STOPWORDS or len(term) < 3:
                    continue
                key = term.lower()
                if key in seen_lower:
                    continue
                seen[term] = None
                seen_lower.add(key)
                if len(seen) >= _MAX_CANDIDATE_TERMS:
                    return list(seen.keys())
    return list(seen.keys())


def _candidate_names(pages: list[VisionPage]) -> list[str]:
    """Proper-noun candidates from title/heading/signature/header blocks."""
    seen: dict[str, None] = {}
    seen_lower: set[str] = set()
    for page in pages:
        for block in page.blocks:
            if block.role not in _NAME_ROLES:
                continue
            text = _strip_inline_html(block.text_en)
            for match in _NAME_RE.finditer(text):
                name = match.group(1).strip().rstrip(".")
                if not name or name in _STOPWORDS or len(name) < 3:
                    continue
                key = name.lower()
                if key in seen_lower:
                    continue
                seen[name] = None
                seen_lower.add(key)
                if len(seen) >= _MAX_CANDIDATE_NAMES:
                    return list(seen.keys())
    return list(seen.keys())


# ── LLM sub-calls ────────────────────────────────────────────────────────


def _legal_prompt(terms: list[str]) -> str:
    global _legal_prompt_template
    if _legal_prompt_template is None:
        _legal_prompt_template = _LEGAL_PROMPT_PATH.read_text(encoding="utf-8")
    terms_block = "\n".join(f"- {t}" for t in terms)
    return _legal_prompt_template.replace("{terms_block}", terms_block)


def _name_prompt(names: list[str]) -> str:
    global _name_prompt_template
    if _name_prompt_template is None:
        _name_prompt_template = _NAME_PROMPT_PATH.read_text(encoding="utf-8")
    names_block = "\n".join(f"- {n}" for n in names)
    return _name_prompt_template.replace("{names_block}", names_block)


async def _call_legal_terms(
    client: Any, terms: list[str], model: str, job_id: str
) -> dict[str, str]:
    if not terms:
        return {}
    try:
        result = await call_gemini_json(
            client,
            model,
            [_legal_prompt(terms)],
            schema=_GlossaryResponse,
            temperature=0.1,
            max_output_tokens=16384,
            retries=1,
            context="glossary legal terms",
        )
    except Exception as exc:
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


async def _call_name_transliterate(
    client: Any, names: list[str], model: str, job_id: str
) -> dict[str, str]:
    if not names:
        return {}
    try:
        result = await call_gemini_json(
            client,
            model,
            [_name_prompt(names)],
            schema=_EntityResponse,
            temperature=0.1,
            max_output_tokens=8192,
            retries=1,
            context="glossary names",
        )
    except Exception as exc:
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


# ── Public entry point ───────────────────────────────────────────────────


async def build_glossary(
    client: Any,
    pages: list[VisionPage],
    model: str,
    job_id: str,
) -> dict[str, str]:
    """Build the merged document glossary. Fail-soft on LLM errors.

    Layers (later wins): baseline YAML → legal-terms LLM → names LLM.
    """
    baseline = dict(load_baseline_glossary())
    terms = _candidate_terms(pages)
    names = _candidate_names(pages)

    t0 = time.perf_counter()
    legal_map, name_map = await asyncio.gather(
        _call_legal_terms(client, terms, model, job_id),
        _call_name_transliterate(client, names, model, job_id),
    )

    merged = baseline.copy()
    merged.update(legal_map)
    merged.update(name_map)

    logger.info(
        "[%s] glossary: baseline=%d, legal=%d/%d, names=%d/%d → merged=%d (%.2fs)",
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
