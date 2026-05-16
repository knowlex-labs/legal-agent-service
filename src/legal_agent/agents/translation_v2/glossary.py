"""Stage 3: document-level glossary builder.

Collects candidate English legal terms across all pages, sends to Gemini in
ONE call, returns {en_term → hi_term}. Fail-soft: on any error returns {} so
translation can proceed without a glossary.
"""

from __future__ import annotations

import logging
import re
import time
import unicodedata
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from legal_agent.agents.translation_v2.gemini_client import call_gemini_json
from legal_agent.agents.translation_v2.schemas import VisionPage

logger = logging.getLogger(__name__)

_PROMPT_PATH = Path(__file__).parent / "prompts" / "glossary.md"
_PROMPT_TEMPLATE: str | None = None

# Maximum candidate terms sent to the LLM in one call. Keeps prompt size bounded
# while covering legal documents up to ~200 pages worth of unique vocabulary.
_MAX_CANDIDATE_TERMS = 400

# A "legal-ish" term: title-case phrase of 1–4 words, or all-caps phrase,
# capturing things like "Show Cause Notice", "Section 482 CrPC", "Petitioner".
# Pure single common words (e.g. "the", "and") are filtered out by stop-list.
_PHRASE_RE = re.compile(
    r"\b("
    r"(?:[A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+){0,3})"  # Title Case 1-4 words
    r"|(?:[A-Z]{2,}(?:\s+[A-Z]{2,}){0,3})"  # ALL CAPS 1-4 words
    r")\b"
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


def _strip_inline_html(s: str) -> str:
    return re.sub(r"<[^>]+>", "", s)


def _candidate_terms(pages: list[VisionPage]) -> list[str]:
    """Extract candidate English legal terms from the document."""
    seen: dict[str, None] = {}
    for page in pages:
        for block in page.blocks:
            text = _strip_inline_html(block.text_en)
            for match in _PHRASE_RE.finditer(text):
                term = match.group(1).strip()
                if not term:
                    continue
                if term in _STOPWORDS:
                    continue
                if len(term) < 3:
                    continue
                # Dedup case-insensitively but keep the first casing seen.
                key = term.lower()
                if key not in {k.lower() for k in seen}:
                    seen[term] = None
                if len(seen) >= _MAX_CANDIDATE_TERMS:
                    return list(seen.keys())
    return list(seen.keys())


def _prompt(terms: list[str]) -> str:
    global _PROMPT_TEMPLATE
    if _PROMPT_TEMPLATE is None:
        _PROMPT_TEMPLATE = _PROMPT_PATH.read_text(encoding="utf-8")
    terms_block = "\n".join(f"- {t}" for t in terms)
    return _PROMPT_TEMPLATE.replace("{terms_block}", terms_block)


async def build_glossary(
    client: Any,
    pages: list[VisionPage],
    model: str,
    job_id: str,
) -> dict[str, str]:
    """Build a {en → hi} glossary across the whole document. Fail-soft."""
    terms = _candidate_terms(pages)
    if not terms:
        logger.info("[%s] glossary: no candidate terms found", job_id)
        return {}

    prompt = _prompt(terms)
    t0 = time.perf_counter()
    try:
        result = await call_gemini_json(
            client,
            model,
            [prompt],
            schema=_GlossaryResponse,
            temperature=0.1,
            max_output_tokens=16384,
            retries=1,
            context="glossary",
        )
    except Exception as exc:
        logger.warning(
            "[%s] glossary build failed (%s: %s); continuing without glossary",
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
    logger.info(
        "[%s] glossary: %d candidates → %d entries (%.2fs)",
        job_id,
        len(terms),
        len(out),
        time.perf_counter() - t0,
    )
    return out
