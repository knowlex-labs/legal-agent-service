"""Claim verification via Firecrawl search-only.

Pipeline for the workspace_chat draft-then-verify flow: given a list of
factual claims extracted from an LLM draft, fire one Firecrawl search per
claim (1 credit, no scrape) and decide whether the top snippets support the
claim. Results feed back into the rewrite stage so unsupported claims can
be removed or softened before the user sees anything.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Literal

from pydantic import BaseModel, Field

from legal_agent.chat.legal_web_search_firecrawl import firecrawl_search_only
from legal_agent.config import get_settings

logger = logging.getLogger(__name__)


ClaimType = Literal["case_citation", "statute", "date", "number", "entity", "quote", "other"]


class Claim(BaseModel):
    """A single factual claim extracted from an LLM draft."""

    text: str = Field(..., description="The exact sentence or span containing the claim.")
    type: ClaimType = Field(..., description="What kind of fact this is.")
    verification_query: str = Field(
        ...,
        description=(
            "A search-engine-ready query that targets THIS specific claim. "
            "Should include the key specific token (case name, date, number, "
            "statute section) that we need to confirm."
        ),
    )


class ClaimList(BaseModel):
    """Wrapper model for structured-output extraction."""

    claims: list[Claim] = Field(default_factory=list)


class ClaimVerification(BaseModel):
    """Result of verifying one claim."""

    claim: Claim
    supported: bool
    supporting_url: str | None = None
    supporting_snippet: str | None = None


# Match a 4-digit year, useful for date-type claims.
_YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")
# Extract numeric tokens (including percentages and decimals) for number-type claims.
_NUMBER_RE = re.compile(r"\b\d+(?:[.,]\d+)*\s*%?")
# Named-entity-ish substring matching — fallback to the longest capitalised phrase
# inside the claim if no better anchor token is available.
_CAPS_PHRASE_RE = re.compile(r"(?:\b[A-Z][A-Za-z&]+(?:\s+[A-Z][A-Za-z&]+)+)")


def _anchor_tokens(claim: Claim) -> list[str]:
    """Pull the specific tokens that must appear in a supporting snippet.

    Heuristic: for dates/numbers we look for the literal digits; for
    entities/case_citation/statute we look for capitalised phrases; for
    quotes we use the longest 5+ word run.
    """
    text = claim.text
    anchors: list[str] = []

    if claim.type in {"date", "number"}:
        anchors.extend(_YEAR_RE.findall(text))
        anchors.extend(_NUMBER_RE.findall(text))
    if claim.type in {"case_citation", "statute", "entity"}:
        anchors.extend(_CAPS_PHRASE_RE.findall(text))
    if claim.type == "quote":
        # Prefer the content inside the first pair of quotation marks, else
        # fall back to the claim text truncated to a 60-char substring.
        q = re.search(r"[\"\u201c]([^\"\u201d]{10,})[\"\u201d]", text)
        anchors.append(q.group(1) if q else text[:60])

    # Dedupe while preserving order, strip empties / very-short noise.
    seen: set[str] = set()
    deduped: list[str] = []
    for a in anchors:
        s = a.strip()
        if len(s) < 3 or s.lower() in seen:
            continue
        seen.add(s.lower())
        deduped.append(s)
    return deduped


def _snippet_supports(anchors: list[str], snippet: str, title: str = "") -> bool:
    """Loose substring check — does any anchor token appear in the snippet/title?"""
    if not anchors:
        return False
    haystack = (snippet + " " + title).lower()
    return any(anchor.lower() in haystack for anchor in anchors)


async def verify_claim(claim: Claim) -> ClaimVerification:
    """Verify a single claim via one Firecrawl search (1 credit, no scrape)."""
    hits = await firecrawl_search_only(
        query=claim.verification_query, num_results=3, scope_to_legal_domains=False
    )
    if not hits:
        return ClaimVerification(claim=claim, supported=False)

    anchors = _anchor_tokens(claim)

    # For "other" type with no usable anchors, we fall through unsupported —
    # the rewrite stage will decide whether to keep a loose paraphrase or
    # drop the claim entirely. We deliberately do NOT spend an LLM call per
    # claim just to check a soft claim; keeps the verify stage cheap.
    for hit in hits:
        snippet = hit.get("snippet") or ""
        title = hit.get("title") or ""
        if _snippet_supports(anchors, snippet, title):
            return ClaimVerification(
                claim=claim,
                supported=True,
                supporting_url=hit.get("url"),
                supporting_snippet=snippet or title,
            )
    return ClaimVerification(claim=claim, supported=False)


async def verify_claims(claims: list[Claim]) -> list[ClaimVerification]:
    """Verify each claim in parallel (bounded by firecrawl_verify_concurrency)."""
    settings = get_settings()
    max_claims = settings.firecrawl_verify_max_claims
    if len(claims) > max_claims:
        logger.info(
            f"[verify] capping claims from {len(claims)} to {max_claims}"
        )
        claims = claims[:max_claims]

    semaphore = asyncio.Semaphore(settings.firecrawl_verify_concurrency)

    async def _bounded(c: Claim) -> ClaimVerification:
        async with semaphore:
            try:
                return await verify_claim(c)
            except Exception:
                logger.exception("[verify] unexpected error verifying claim")
                return ClaimVerification(claim=c, supported=False)

    results = await asyncio.gather(*(_bounded(c) for c in claims))
    supported_count = sum(1 for r in results if r.supported)
    logger.info(f"[verify] {supported_count}/{len(results)} claims supported")
    return list(results)
