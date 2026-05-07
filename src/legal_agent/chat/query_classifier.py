"""Two-layer query classifier for the workspace chat.

Layer 1 (`is_trivial_message`) is a regex/length heuristic that catches obvious
greetings, thanks, and very short non-questions with zero added latency. Layer 2
(`classify_query`) is a small LLM call that returns a structured intent for
ambiguous short messages. Both feed the verify-pipeline fast path so trivial
messages never run the full draft → verify → rewrite flow.

The `recommended_model` field on `QueryClassification` is reserved for the
future "auto mode" that will route simple/research queries to different model
tiers. It is unused today.
"""

from __future__ import annotations

import logging
import re
from typing import Literal

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from legal_agent.prompts.query_classifier import QUERY_CLASSIFIER_SYSTEM_PROMPT

logger = logging.getLogger(__name__)


_TRIVIAL_PATTERNS = [
    r"^\s*(hi+|hello+|hey+|yo|namaste|namaskar|good\s+(morning|afternoon|evening|night))\b[\s!.?,]*$",
    r"^\s*(thanks|thank\s+you|ty|thx|cheers|ok|okay|okie|got\s+it|noted|cool|nice|great)\b[\s!.?,]*$",
    r"^\s*(bye|goodbye|see\s+you|see\s+ya|talk\s+(later|soon)|catch\s+you\s+later)\b[\s!.?,]*$",
    r"^\s*(how\s+are\s+you|how's\s+it\s+going|what's\s+up|sup)\b[\s!.?,]*$",
]
_TRIVIAL_RE = re.compile("|".join(_TRIVIAL_PATTERNS), re.IGNORECASE)

# Tokens that signal a legal lookup — if any appear in a short message, do
# NOT treat it as trivial even if it lacks a question mark.
_LEGAL_KEYWORDS = (
    "section", "act", "court", "case", "law", "ipc", "crpc", "bns", "bnss",
    "bsa", "evidence", "constitution", "article", "writ", "bail", "petition",
    "appeal", "judgment", "judgement", "supreme", "high court",
)


def is_trivial_message(text: str | None) -> bool:
    """Regex/length fast path: returns True for empty/greeting/pleasantry messages."""
    if not text or not text.strip():
        return True
    s = text.strip()
    if _TRIVIAL_RE.match(s):
        return True
    # ≤3 words, no question, no legal-keyword signals → likely small talk
    if len(s.split()) <= 3 and "?" not in s:
        lower = s.lower()
        if not any(kw in lower for kw in _LEGAL_KEYWORDS):
            return True
    return False


QueryIntent = Literal["trivial", "simple", "research"]


class QueryClassification(BaseModel):
    """Output of the LLM classifier."""

    intent: QueryIntent = Field(
        ..., description="trivial=greeting/pleasantry, simple=quick lookup, research=needs web verification"
    )
    recommended_model: str | None = Field(
        default=None,
        description="Reserved for future auto-mode model routing; ignored today.",
    )


async def classify_query(text: str, llm) -> QueryClassification:
    """Run the LLM classifier on a user message. Caller should fall through to
    the existing pipeline on any exception."""
    structured = llm.with_structured_output(QueryClassification)
    return await structured.ainvoke([
        SystemMessage(content=QUERY_CLASSIFIER_SYSTEM_PROMPT),
        HumanMessage(content=text or ""),
    ])
