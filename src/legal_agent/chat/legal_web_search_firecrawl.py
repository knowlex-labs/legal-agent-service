"""Firecrawl-backed legal web search tool for workspace chat.

Search → scrape → cite flow:
1. Firecrawl `/search` with a domain-restricted query across the three
   trusted Indian legal sources (LiveLaw, SCC Online, Bar and Bench).
2. Scrape the top N results in parallel to get clean article markdown,
   not just snippet text — the LLM cites from real content.
3. Emit `[W1]..[WN]` blocks in the exact format
   `chat.citation_utils.parse_legal_web_search_citations` already parses.

Indian Kanoon and Manupatra are hard-blocked per product decision — only
the three trusted sources are searched.

Fallback chain (for robustness):
- Firecrawl raises / times out → Serper (snippet-only) with same 3-domain
  restriction, logged clearly.
- Both unavailable → tool returns a "not configured" message; LLM answers
  without citations and must say "No authoritative source found" per
  its docstring.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from langchain_core.tools import tool

from legal_agent.chat.web_search import _INDIAN_CITE_RE, _YEAR_RE
from legal_agent.config import get_settings

logger = logging.getLogger(__name__)


# Source-name display labels keyed by domain substring.
_DOMAIN_LABELS: dict[str, str] = {
    "livelaw.in": "LiveLaw",
    "scconline.com": "SCC Online",
    "barandbench.com": "Bar and Bench",
}

# Minimum body length below which we treat a scrape as paywalled/blocked.
# SCC Online returns short preview text when the full judgment is paywalled;
# we still emit the result but without snippet, so the lawyer can click through.
_PAYWALL_BODY_THRESHOLD = 200
# Max chars of scraped content to embed per result (keeps tool output bounded).
# 2500 gives Flash enough grounded material to capture bench composition,
# ratio, and subsequent cases — which news articles often bury after the lede.
_SNIPPET_MAX_CHARS = 2500

# Bounds on LLM-chosen scrape count. Keeps credits predictable per turn.
_MIN_SOURCES = 1
_MAX_SOURCES = 5

_TOOL_DOCSTRING = """Find authoritative citations from LiveLaw, SCC Online, and Bar and Bench.

Call this tool ONLY when your answer asserts a specific legal proposition
that needs authoritative backing — e.g. a statutory rule, a case holding,
a procedural requirement, a constitutional question.

Do NOT call for:
- Greetings, small talk, definitional questions
- Questions fully answered by the user's uploaded case documents
- Follow-up clarifications that don't introduce new legal claims

Call ONCE per user turn at most. Use [W1], [W2], … markers to cite returned
sources. If the tool returns no sources, say "No authoritative source found
for this proposition" — never invent citations.

Args:
    query: Legal search query (e.g., "section 438 CrPC anticipatory bail supreme court").
    num_sources: How many sources to scrape (1-5). Pick based on question
        complexity:
        - 1: quick verification of a definitional or uncontested rule.
        - 2-3 (DEFAULT): typical legal proposition needing authority.
        - 4-5: contested or multi-faceted topic where multiple viewpoints help
          (e.g. ongoing constitutional controversy, recent judgment with
          divergent commentary). Each additional source costs ~1 Firecrawl
          credit and adds ~2-3s latency.
"""


def _label_for_url(url: str) -> str:
    url_lower = url.lower()
    for domain, label in _DOMAIN_LABELS.items():
        if domain in url_lower:
            return label
    return "Web"


def _extract_citation_and_year(title: str, body: str) -> tuple[str | None, int | None]:
    combined = f"{title}\n{body}"
    cite_match = _INDIAN_CITE_RE.search(combined)
    citation = cite_match.group(0).strip() if cite_match else None
    year_match = _YEAR_RE.search(combined)
    year = int(year_match.group(1)) if year_match else None
    return citation, year


def _truncate_markdown(text: str, max_chars: int = _SNIPPET_MAX_CHARS) -> str:
    cleaned = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(cleaned) <= max_chars:
        return cleaned
    return cleaned[:max_chars].rstrip() + "…"


def _diversify_urls(search_hits: list[dict], limit: int) -> list[dict]:
    """Pick up to `limit` URLs, preferring one per domain before doubling up.

    Firecrawl `/search` can return multiple results from the same domain
    clustered at the top; this ensures we scrape across LiveLaw +
    SCC Online + Bar and Bench when available.
    """
    per_domain: dict[str, list[dict]] = {}
    for hit in search_hits:
        url = hit.get("url") or hit.get("link") or ""
        if not url:
            continue
        label = _label_for_url(url)
        per_domain.setdefault(label, []).append(hit)

    ordered: list[dict] = []
    round_idx = 0
    while len(ordered) < limit:
        added_this_round = False
        for hits in per_domain.values():
            if round_idx < len(hits):
                ordered.append(hits[round_idx])
                added_this_round = True
                if len(ordered) == limit:
                    break
        if not added_this_round:
            break
        round_idx += 1
    return ordered


def _format_block(
    idx: int, title: str, url: str, source_label: str, body: str,
    citation: str | None, year: int | None, paywalled: bool,
) -> str:
    source_display = f"{source_label} (paywalled)" if paywalled else source_label
    parts = [
        f"[W{idx}] {title or 'Untitled'}",
        f"Source: {source_display}",
        f"URL: {url}",
    ]
    if paywalled or not body:
        parts.append("Snippet: (content paywalled — click URL for full article)")
    else:
        parts.append(f"Snippet: {_truncate_markdown(body)}")
    if citation:
        parts.append(f"Citation: {citation}")
    if year:
        parts.append(f"Year: {year}")
    return "\n".join(parts) + "\n"


async def _firecrawl_search_and_scrape(
    query: str, num_sources: int
) -> list[dict[str, Any]] | None:
    """Primary path: Firecrawl search + scrape top `num_sources`. Returns None on failure."""
    settings = get_settings()
    try:
        from firecrawl import AsyncFirecrawl  # type: ignore
    except ImportError:
        logger.warning("[legal-web-search] firecrawl-py not installed; skipping Firecrawl")
        return None

    client = AsyncFirecrawl(api_key=settings.firecrawl_api_key)
    site_filter = " OR ".join(f"site:{d}" for d in settings.firecrawl_search_domains)
    scoped_query = f"({site_filter}) {query}"

    try:
        search_resp = await client.search(
            query=scoped_query,
            limit=max(6, num_sources * 2),
        )
    except Exception as exc:
        logger.warning(f"[legal-web-search] Firecrawl search failed: {exc}")
        return None

    # firecrawl-py v2 returns an object with a `web` attribute (list of dicts).
    hits_raw = getattr(search_resp, "web", None) or (
        search_resp.get("web") if isinstance(search_resp, dict) else None
    ) or []
    hits = [
        {
            "url": getattr(h, "url", None) or (h.get("url") if isinstance(h, dict) else None),
            "title": getattr(h, "title", None) or (h.get("title") if isinstance(h, dict) else None),
            "description": getattr(h, "description", None) or (
                h.get("description") if isinstance(h, dict) else None
            ),
        }
        for h in hits_raw
    ]
    hits = [h for h in hits if h["url"]]
    if not hits:
        logger.info(f"[legal-web-search] Firecrawl returned no hits for '{query[:80]}'")
        return []

    selected = _diversify_urls(hits, num_sources)
    logger.info(
        f"[legal-web-search] Scraping {len(selected)} URLs from Firecrawl search "
        f"(query='{query[:80]}', requested={num_sources})"
    )

    async def _scrape(hit: dict[str, Any]) -> dict[str, Any]:
        url = hit["url"]
        result: dict[str, Any] = {
            "url": url,
            "title": hit.get("title") or "",
            "body": hit.get("description") or "",
            "paywalled": False,
            "label": _label_for_url(url),
        }
        try:
            scraped = await client.scrape(url=url, formats=["markdown"])
            markdown = getattr(scraped, "markdown", None) or (
                scraped.get("markdown") if isinstance(scraped, dict) else None
            )
            title = getattr(scraped, "metadata", None)
            if title is not None:
                meta_title = getattr(title, "title", None) or (
                    title.get("title") if isinstance(title, dict) else None
                )
                if meta_title:
                    result["title"] = meta_title
            if markdown:
                result["body"] = markdown
        except Exception as exc:
            logger.warning(f"[legal-web-search] Scrape failed for {url}: {exc}")

        if len(result["body"]) < _PAYWALL_BODY_THRESHOLD:
            result["paywalled"] = True
        return result

    scraped_results = await asyncio.gather(
        *(_scrape(h) for h in selected), return_exceptions=False
    )
    return scraped_results


def _serper_fallback(query: str, num_sources: int) -> list[dict[str, Any]]:
    """Fallback path: Serper snippets restricted to the 3 domains. Sync by design."""
    import httpx

    settings = get_settings()
    if not settings.serper_api_key:
        return []

    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    with httpx.Client() as client:
        for domain in settings.firecrawl_search_domains:
            try:
                resp = client.post(
                    "https://google.serper.dev/search",
                    headers={"X-API-KEY": settings.serper_api_key, "Content-Type": "application/json"},
                    json={"q": f"site:{domain} {query}", "num": 3, "gl": "in", "hl": "en"},
                    timeout=15,
                )
                resp.raise_for_status()
                organic = resp.json().get("organic", [])
            except Exception as exc:
                logger.warning(f"[legal-web-search] Serper fallback failed for {domain}: {exc}")
                continue
            for r in organic:
                url = r.get("link", "")
                if not url or url in seen:
                    continue
                seen.add(url)
                body = r.get("snippet", "") or ""
                results.append({
                    "url": url,
                    "title": r.get("title", ""),
                    "body": body,
                    "label": _label_for_url(url),
                    "paywalled": len(body) < _PAYWALL_BODY_THRESHOLD,
                })
                if len(results) >= num_sources:
                    return results
    return results


def _format_output(results: list[dict[str, Any]]) -> str:
    if not results:
        return "No authoritative source found on LiveLaw, SCC Online, or Bar and Bench."
    parts = [f"Found {len(results)} source(s):\n"]
    for i, r in enumerate(results, 1):
        citation, year = _extract_citation_and_year(r.get("title", ""), r.get("body", ""))
        parts.append(
            _format_block(
                idx=i,
                title=r.get("title", ""),
                url=r.get("url", ""),
                source_label=r.get("label", "Web"),
                body=r.get("body", ""),
                citation=citation,
                year=year,
                paywalled=bool(r.get("paywalled")),
            )
        )
    parts.append(
        "---\n"
        "Cite sources using [W1], [W2], [W3]. "
        "If none of these sources support your answer, say "
        '"No authoritative source found for this proposition." '
        "Do NOT fabricate citations not present in the list above."
    )
    return "\n".join(parts)


def create_legal_web_search_tool():
    """Construct the LangChain tool. Name and docstring are frontend-visible."""

    @tool(description=_TOOL_DOCSTRING)
    async def legal_web_search(query: str, num_sources: int = 3) -> str:
        # Clamp LLM-chosen value so a runaway tool call can't blow the credit budget.
        n = max(_MIN_SOURCES, min(int(num_sources or 3), _MAX_SOURCES))

        settings = get_settings()
        if not settings.firecrawl_api_key and not settings.serper_api_key:
            return (
                "Web search is not configured (set FIRECRAWL_API_KEY or SERPER_API_KEY). "
                "Answer from the user's documents only, or say no authoritative source was available."
            )

        results: list[dict[str, Any]] | None = None
        if settings.firecrawl_api_key:
            results = await _firecrawl_search_and_scrape(query, num_sources=n)
            if results is None:
                logger.info("[legal-web-search] Firecrawl unavailable, trying Serper fallback")

        if not results:
            # Serper fallback (sync, but cheap — run in default thread executor).
            fallback = await asyncio.to_thread(_serper_fallback, query, n)
            if fallback:
                logger.info(
                    f"[legal-web-search] Serper fallback returned {len(fallback)} snippets"
                )
            results = fallback

        return _format_output(results)

    return legal_web_search
