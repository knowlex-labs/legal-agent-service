"""Stage A — per-document glossary extractor.

Single LLM call over the full source text that returns:
- a one-line `subject` summary used as document-context preamble in Stage B/C,
- a list of `terms` (source → target, with a `role` tag) for entity names,
  scheme names, position titles, statutes, and identifiers that must stay
  consistent across the document.

The extracted terms are layered onto the static `glossary_en_hi.yaml` via
`Glossary.with_dynamic_entries`, so they flow through the existing
`freeze()`/`restore()` sentinel mechanism in `glossary.py` unchanged —
locking the chosen target on every occurrence inside the translator call.
"""

from __future__ import annotations

import json
import logging
from typing import Literal

from langchain.chat_models import init_chat_model
from langchain_core.messages import HumanMessage
from pydantic import BaseModel, Field

from legal_agent.agents.translation._llm_common import (
    extract_json_blob,
    infer_provider,
    message_content_to_text,
)
from legal_agent.agents.translation.glossary import GlossaryEntry
from legal_agent.config import get_settings

logger = logging.getLogger(__name__)

_MAX_SOURCE_CHARS = 24_000  # bounds Haiku prompt; truncate excess from middle


TermRole = Literal[
    "entity_name",
    "legal_term",
    "position_title",
    "scheme_name",
    "proper_noun",
    "identifier",
    "other",
]


# Two registers steer downstream prompts. government_legal keeps the existing
# CBIC/Rajbhasha-flavoured prompt; general drops those rules so academic,
# journalistic, contractual, or resume content reads naturally.
DocRegister = Literal["government_legal", "general"]


class ExtractedTerm(BaseModel):
    source: str = Field(..., description="Source-language surface form")
    target: str = Field(..., description="Target-language rendering to enforce")
    role: TermRole = "other"


class DocumentGlossary(BaseModel):
    subject: str = ""
    # Pydantic v2's ModelMetaclass exposes a `register` method, so we use
    # `doc_register` on the Python side. The extractor prompt asks for the same
    # JSON key so Pydantic validates without aliasing.
    doc_register: DocRegister = "government_legal"
    terms: list[ExtractedTerm] = Field(default_factory=list)

    def to_glossary_entries(self) -> list[GlossaryEntry]:
        """Convert extracted terms into GlossaryEntry list for layering.

        Reuses the existing `GlossaryEntry.hi` field as the generic "target"
        slot — it is renamed only semantically; the freeze()/restore() path
        already uses it as the substitution output regardless of language.
        """
        seen: set[str] = set()
        out: list[GlossaryEntry] = []
        for t in self.terms:
            term = (t.source or "").strip()
            target = (t.target or "").strip()
            if not term or not target or term in seen:
                continue
            seen.add(term)
            out.append(GlossaryEntry(term=term, hi=target))
        return out


_EXTRACTOR_PROMPT = """\
You are a terminology extractor. You are given the full source text of one
document. Return a JSON object that locks the document's terminology contract
so a downstream translator never drifts mid-document, and that classifies the
document's register so downstream prompts match its tone.

Return ONLY this JSON shape (no markdown fences, no commentary):

{{
  "subject": "<one short line describing what this document is about>",
  "doc_register": "government_legal" | "general",
  "terms": [
    {{"source": "<surface form as it appears in the source>",
      "target": "<exact {target_language} rendering to enforce>",
      "role": "entity_name | legal_term | position_title | scheme_name | proper_noun | identifier | other"}}
  ]
}}

doc_register rules (pick exactly one):
- "government_legal": Indian administrative / legal / tax / regulatory documents.
  Signals: DIN / F.NO / CBIC / DGGI / GST / PAN identifiers, "Section 138"-style
  statutory references, government letterhead, "सेवा में" / "To," notice
  salutation, formal hereby/whereas phrasing, departmental signatures.
- "general": Everything else — academic papers, journal articles, books,
  resumes, business letters, news, marketing, fiction, generic commercial
  contracts without Indian statutory framing. When in doubt and the document
  shows no Indian-government signals, pick "general".

Extract terms that MUST stay consistent across the document:
- Position titles and roles (e.g. "Guest Faculty / अतिथि विद्वान", "Joint Commissioner", "Chief Justice").
- Scheme / programme / notification names (e.g. "PM-USHA", "Vidya Samiksha").
- Institutions, departments, organisations, journals, courts.
- Personal and place names that appear more than once.
- Statutory / legal terms specific to this document.
- Recurring identifiers and abbreviations (DIN, F.NO, CGST, DRC-22, S. Ct.).
- For academic / journal sources: case names (e.g. "Printz v. United States"),
  cited authors, and recurring technical concepts.

For each term, choose ONE canonical {target_language} rendering. If the source
already pairs Hindi and English (e.g. "अतिथि विद्वान (Guest Faculty)"), use the
established pair — never invent a new translation.

Do NOT include: one-off common nouns, generic verbs, numerals, dates, or
boilerplate phrases.

Source language: {source_language}
Target language: {target_language}

SOURCE DOCUMENT:
{source_text}
"""


def _truncate_middle(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    half = limit // 2
    return text[:half] + "\n\n[...TRUNCATED FOR EXTRACTION...]\n\n" + text[-half:]


async def extract_document_glossary(
    source_text: str,
    source_language: str,
    target_language: str,
) -> DocumentGlossary:
    """Run the Stage A extractor. Any failure → empty glossary so the
    translator can proceed without the document-glossary layer."""
    settings = get_settings()
    model = settings.translation_glossary_extractor_model
    snippet = _truncate_middle(source_text.strip(), _MAX_SOURCE_CHARS)
    if not snippet:
        return DocumentGlossary()

    prompt = _EXTRACTOR_PROMPT.format(
        source_language=source_language,
        target_language=target_language,
        source_text=snippet,
    )
    try:
        llm = init_chat_model(model, model_provider=infer_provider(model))
        resp = await llm.ainvoke([HumanMessage(content=prompt)])
    except Exception as exc:
        logger.warning("[glossary-extractor] %s call failed: %s", model, exc)
        return DocumentGlossary()

    cleaned = extract_json_blob(message_content_to_text(resp.content), "{")
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        logger.warning(
            "[glossary-extractor] %s returned non-JSON; head=%r", model, cleaned[:200]
        )
        return DocumentGlossary()

    try:
        gloss = DocumentGlossary.model_validate(parsed)
    except Exception as exc:
        logger.warning("[glossary-extractor] schema validation failed: %s", exc)
        return DocumentGlossary()

    logger.info(
        "[glossary-extractor] subject=%r doc_register=%s terms=%d",
        gloss.subject[:80], gloss.doc_register, len(gloss.terms),
    )
    return gloss
