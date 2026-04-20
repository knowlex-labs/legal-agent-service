"""LangChain tool for web-based legal research via Serper API.

Runs site-scoped Google searches against authoritative Indian legal
databases (SCC Online, Manupatra first, Indian Kanoon as fallback).
"""

import logging
import re

import httpx
from langchain_core.tools import tool

from legal_agent.config import get_settings

logger = logging.getLogger(__name__)

_INDIAN_CITE_RE = re.compile(
    r'(?:'
    r'\(\d{4}\)\s+\d+\s+SCC(?:\s*\(Cri\))?\s+\d+'       # (2014) 8 SCC 273 / SCC (Cri)
    r'|AIR\s+\d{4}\s+\w+\s+\d+'                            # AIR 2019 SC 1234
    r'|\d{4}\s+CrLJ\s+\d+'                                 # 2020 CrLJ 456
    r'|\d{4}\s+SCR\s+\d+'                                   # 2018 SCR 100
    r'|\d{4}\s+Bom\s*CR\s+\d+'                              # 2021 Bom CR 789
    r'|\d{4}\s+SCC\s+OnLine\s+\w+\s+\d+'                   # 2022 SCC OnLine SC 100
    r')',
    re.IGNORECASE,
)
_YEAR_RE = re.compile(r'\b((?:19|20)\d{2})\b')

_BLOG_URL_SEGMENTS = ('/blog', '/article', '/column', '/news', '/newsline')
_BLOG_TITLE_KEYWORDS = ('blog', 'editorial', 'opinion piece')


def _extract_indian_citation(title: str, snippet: str) -> tuple[str | None, int | None]:
    combined = f"{title} {snippet}"
    cite_match = _INDIAN_CITE_RE.search(combined)
    citation = cite_match.group(0).strip() if cite_match else None
    year_match = _YEAR_RE.search(combined)
    year = int(year_match.group(1)) if year_match else None
    return citation, year


def _is_judgment_url(url: str, title: str) -> bool:
    url_lower = url.lower()
    if any(seg in url_lower for seg in _BLOG_URL_SEGMENTS):
        return False
    title_lower = title.lower()
    if any(kw in title_lower for kw in _BLOG_TITLE_KEYWORDS):
        return False
    return True

# Three trusted Indian legal sources. Indian Kanoon + Manupatra are
# intentionally excluded — Indian Kanoon is user-uploaded/unverified and
# Manupatra is out of scope for the current product. Do not add back
# without explicit product approval.
SITE_SCOPED_SOURCES = [
    ("scconline.com", "SCC Online"),
    ("livelaw.in", "LiveLaw"),
    ("barandbench.com", "Bar and Bench"),
]

def _serper_search(client: httpx.Client, api_key: str, query: str, num: int = 5) -> list[dict]:
    try:
        resp = client.post(
            "https://google.serper.dev/search",
            headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
            json={"q": query, "num": num, "gl": "in", "hl": "en"},
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json().get("organic", [])
    except Exception as e:
        logger.warning(f"Serper search failed for '{query[:80]}': {e}")
        return []


def create_web_search_tool():

    @tool
    def legal_web_search(query: str) -> str:
        """Find supporting citations from authoritative Indian legal databases.

        Searches SCC Online, Manupatra, LiveLaw, and Indian Kanoon.
        Returns the top sources prioritising SCC Online and Manupatra.

        Call this tool EXACTLY ONCE per user query, AFTER you have drafted your answer,
        to attach authoritative citations. Do NOT call it more than once.
        Do NOT call this tool for greetings, small talk, or non-legal queries.

        Cite each returned source using its [N] number and include URLs at the end.

        Args:
            query: Legal search query (e.g., "section 438 CrPC anticipatory bail supreme court").
        """
        settings = get_settings()
        api_key = settings.serper_api_key
        if not api_key:
            return "Web search is not configured (missing SERPER_API_KEY)."

        seen_urls: set[str] = set()
        # Buckets: high-priority (SCC/Manupatra) vs low-priority (Indian Kanoon, others)
        high: list[dict] = []
        low: list[dict] = []

        with httpx.Client() as client:
            for domain, source_name in SITE_SCOPED_SOURCES:
                site_query = f"site:{domain} {query}"
                results = _serper_search(client, api_key, site_query, num=5)
                is_high = domain in ("scconline.com", "manupatra.com")
                for r in results:
                    url = r.get("link", "")
                    title = r.get("title", "")
                    if url and url not in seen_urls and _is_judgment_url(url, title):
                        seen_urls.add(url)
                        r["_source"] = source_name
                        snippet = r.get("snippet", "")
                        citation, year = _extract_indian_citation(title, snippet)
                        r["_citation"] = citation
                        r["_year"] = year
                        (high if is_high else low).append(r)

        # High-priority (SCC/Manupatra) first, then low-priority
        selected = high + low

        if not selected:
            return "No relevant legal sources found."

        parts = [f"Found {len(selected)} sources:\n"]
        for i, r in enumerate(selected, 1):
            block = (
                f"[W{i}] {r.get('title', 'Untitled')}\n"
                f"Source: {r.get('_source', 'Web')}\n"
                f"URL: {r.get('link', '')}\n"
                f"Snippet: {r.get('snippet', '')}\n"
            )
            if r.get("_citation"):
                block += f"Citation: {r['_citation']}\n"
            if r.get("_year"):
                block += f"Year: {r['_year']}\n"
            parts.append(block)

        parts.append(
            "\n---\nCite sources using [W1], [W2], etc. "
            "If the answer is not supported by the provided sources, "
            'say "No authoritative source found for this proposition."'
        )
        return "\n".join(parts)

    return legal_web_search
