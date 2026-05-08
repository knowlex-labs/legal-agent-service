"""Post-generation safety checks for legal documents.

Applied to every draft and translation before it is uploaded to S3 /
returned to a lawyer. The goal is fail-loud defence-in-depth: prompts tell
the LLM to do the right thing, these helpers catch the cases where it
didn't.

Each helper is pure and idempotent — safe to call from any service layer.
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────
# Placeholder detection
# ──────────────────────────────────────────────────────────────────────

# Bracketed fill-ins the LLM is told to avoid: [Amount], [Date], [Party Name].
# Restricted to Title-cased tokens so markdown links `[text](url)` are not flagged.
# Links of the form `[Some Title](url)` are filtered explicitly below via lookahead.
_PLACEHOLDER_BRACKET = re.compile(r"\[([A-Z][A-Za-z][A-Za-z \-/]{1,38})\](?!\()")
# `{{name}}` Jinja-style
_PLACEHOLDER_MUSTACHE = re.compile(r"\{\{[^}]{1,60}\}\}")
# Long runs of underscores / dashes used as fill lines
_PLACEHOLDER_UNDERSCORES = re.compile(r"_{4,}")
# Sentinel words LLMs fall back to
_PLACEHOLDER_WORDS = re.compile(
    r"\b(XXXX|TBD|TO BE FILLED|TO BE DECIDED|NOT PROVIDED|INSERT HERE)\b",
    re.IGNORECASE,
)


# Legitimate "value not yet known" phrasing used in Indian legal drafts —
# e.g. "[To be assigned by Indian Patent Office]" on a patent application,
# "[To be allotted by Registrar]" on a company filing. These are real-world
# TBDs resolved by an external authority, not forgotten template variables.
_EXTERNAL_TBD = re.compile(r"(?i)^to be \w+(?:\s+\w+)*\s+by\s+\w+")


# Cause-title HTML emits its own bracketed gaps (e.g. [Court Name]) when
# extraction misses a field; those are advocate-editable and must not trip
# the placeholder regex.
_CAUSE_TITLE_SPAN = re.compile(
    r"<!-- cause-title:start -->.*?<!-- cause-title:end -->", re.DOTALL
)


def _strip_cause_title_spans(markdown: str) -> str:
    return _CAUSE_TITLE_SPAN.sub("", markdown)


def detect_placeholders(markdown: str) -> list[str]:
    """Return a list of placeholder snippets found in the draft, empty if clean.

    Distinct values only, capped at 20 entries so a runaway template with
    hundreds of placeholders produces a readable error message. Cause-title
    sentinel spans are stripped before scanning (see `_CAUSE_TITLE_SPAN`).
    """
    found: list[str] = []
    seen: set[str] = set()

    def _add(match: str) -> None:
        snippet = match.strip()
        if snippet and snippet not in seen:
            seen.add(snippet)
            found.append(snippet)

    scoped = _strip_cause_title_spans(markdown)

    for m in _PLACEHOLDER_BRACKET.finditer(scoped):
        if _EXTERNAL_TBD.match(m.group(1)):
            continue
        _add(m.group(0))
    for m in _PLACEHOLDER_MUSTACHE.finditer(scoped):
        _add(m.group(0))
    for m in _PLACEHOLDER_UNDERSCORES.finditer(scoped):
        _add(m.group(0))
    for m in _PLACEHOLDER_WORDS.finditer(scoped):
        _add(m.group(0))

    return found[:20]


def assert_no_placeholders(markdown: str) -> None:
    """Raise ValueError listing placeholders found. No-op if clean."""
    placeholders = detect_placeholders(markdown)
    if placeholders:
        preview = ", ".join(f"'{p}'" for p in placeholders[:8])
        more = f" (+{len(placeholders) - 8} more)" if len(placeholders) > 8 else ""
        raise ValueError(
            f"Draft contains unfilled placeholders: {preview}{more}. "
            "LLM failed to surface user-provided details or fill sensible defaults. "
            "Retry with explicit values in the request or adjust the prompt."
        )


# ──────────────────────────────────────────────────────────────────────
# Aadhaar masking (UIDAI guideline — reveal only last 4)
# ──────────────────────────────────────────────────────────────────────

# Aadhaar is 12 digits in 4-4-4 groups, optionally separated by space or dash.
# Require explicit word boundaries AND the 4-4-4 grouping — avoids catching
# plain 12-digit sequences that might be something else (bank ref, etc.).
_AADHAAR_GROUPED = re.compile(r"\b(\d{4})[- ](\d{4})[- ](\d{4})\b")
# Also cover the flat 12-digit form — but only when preceded by an Aadhaar-indicating
# keyword on the same line, to avoid false positives on long numeric IDs.
_AADHAAR_KEYWORD_LINE = re.compile(
    r"(aadhaar|आधार|uid|uidai)([^\n]{0,40}?)(\b\d{12}\b)",
    re.IGNORECASE,
)


def mask_aadhaar(markdown: str) -> tuple[str, int]:
    """Mask Aadhaar numbers to `XXXX-XXXX-lastfour`. Returns (masked_text, count)."""
    count = 0

    def _mask_grouped(m: re.Match) -> str:
        nonlocal count
        count += 1
        return f"XXXX-XXXX-{m.group(3)}"

    masked = _AADHAAR_GROUPED.sub(_mask_grouped, markdown)

    def _mask_keyword(m: re.Match) -> str:
        nonlocal count
        count += 1
        last4 = m.group(3)[-4:]
        return f"{m.group(1)}{m.group(2)}XXXX-XXXX-{last4}"

    masked = _AADHAAR_KEYWORD_LINE.sub(_mask_keyword, masked)

    if count:
        logger.info(f"[legal-postprocess] Masked {count} Aadhaar number(s) in draft")
    return masked, count


# ──────────────────────────────────────────────────────────────────────
# Draft length sanity
# ──────────────────────────────────────────────────────────────────────

_MIN_DRAFT_CHARS = 500


def assert_draft_length(markdown: str, min_chars: int = _MIN_DRAFT_CHARS) -> None:
    """Raise if the draft is implausibly short for a legal document."""
    n = len(markdown.strip())
    if n < min_chars:
        raise ValueError(
            f"Draft content implausibly short ({n} chars, required ≥{min_chars}). "
            "Possible generation failure; retry or inspect agent logs."
        )


# ──────────────────────────────────────────────────────────────────────
# Citation detection (observability only — warn, don't block)
# ──────────────────────────────────────────────────────────────────────

# Patterns for common Indian case-law citation shapes.
_CITATION_PATTERNS = [
    # (YYYY) VOLUME SCC PAGE  →  (2020) 5 SCC 123
    re.compile(r"\(\s*\d{4}\s*\)\s*\d+\s+SCC\s+\d+", re.IGNORECASE),
    # AIR YYYY SC PAGE  →  AIR 2020 SC 1234
    re.compile(r"\bAIR\s+\d{4}\s+(?:SC|Del|Bom|Cal|Mad|Kar|Guj|Ker|All|Raj|Pat|Ori)\s+\d+", re.IGNORECASE),
    # PartyA v(s). PartyB  —  rough heuristic, requires capitalized follow-on
    re.compile(r"\b[A-Z][A-Za-z.&\-' ]{2,60}\s+v(?:s|\.)?\s+[A-Z][A-Za-z.&\-' ]{2,60}"),
    # (YYYY) SCC OnLine SC NNNN
    re.compile(r"\(\s*\d{4}\s*\)\s*SCC\s+OnLine\s+\w+\s+\d+", re.IGNORECASE),
]


def detect_citations(markdown: str) -> list[str]:
    """Return distinct case-law-like citation snippets found in the draft."""
    found: list[str] = []
    seen: set[str] = set()
    for pat in _CITATION_PATTERNS:
        for m in pat.finditer(markdown):
            snippet = m.group(0).strip()
            if snippet and snippet not in seen:
                seen.add(snippet)
                found.append(snippet)
    return found


def check_citation_grounding(
    draft_markdown: str,
    tool_results_joined: str,
    tool_was_called: bool,
    document_type: str = "",
) -> None:
    """Log warnings about possibly ungrounded citations. Never raises.

    Args:
        draft_markdown: final draft text.
        tool_results_joined: concatenation of all legal_case_search tool outputs
            that were observed in the agent's message history. Used as a
            "verified-citations corpus" — any draft citation that appears here
            verbatim is considered grounded.
        tool_was_called: True if legal_case_search was invoked at least once
            during the agent run. False if the tool was available but never hit.
        document_type: optional identifier included in log lines for triage.
    """
    citations = detect_citations(draft_markdown)
    if not citations:
        return

    if not tool_was_called:
        logger.warning(
            f"[citation-check] document_type={document_type}: "
            f"legal_case_search tool was never invoked — all {len(citations)} "
            f"citation(s) in this draft are from model memory and may be fabricated. "
            f"Citations: {citations[:6]}"
        )
        return

    unverified = [c for c in citations if c not in tool_results_joined]
    if unverified:
        logger.warning(
            f"[citation-check] document_type={document_type}: "
            f"{len(unverified)}/{len(citations)} citation(s) not found in tool results; "
            f"possible hallucination. Unverified: {unverified[:6]}"
        )
    else:
        logger.info(
            f"[citation-check] document_type={document_type}: "
            f"all {len(citations)} citation(s) matched tool results"
        )


# ──────────────────────────────────────────────────────────────────────
# Convenience: run all draft-side checks in the right order
# ──────────────────────────────────────────────────────────────────────

def apply_draft_postprocess(markdown: str) -> str:
    """Run Aadhaar masking, log (don't raise on) placeholders, assert length.

    Mutating step (Aadhaar) runs first so detection sees the post-mask text
    and doesn't mistake a real Aadhaar number for a placeholder. Unfilled
    placeholders are logged as a warning and shipped as-is — the lawyer can
    edit them downstream. Length assertion still raises.
    """
    masked, _ = mask_aadhaar(markdown)
    placeholders = detect_placeholders(masked)
    if placeholders:
        preview = ", ".join(f"'{p}'" for p in placeholders[:8])
        more = f" (+{len(placeholders) - 8} more)" if len(placeholders) > 8 else ""
        logger.warning(
            "Draft shipped with unfilled placeholders: %s%s", preview, more
        )
    assert_draft_length(masked)
    return masked
