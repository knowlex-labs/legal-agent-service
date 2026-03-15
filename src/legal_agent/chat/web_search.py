"""LangChain tool for web-based legal research via Serper API.

Runs site-scoped Google searches against authoritative Indian legal
databases (SCC Online, Manupatra first, Indian Kanoon as fallback).
"""

import logging

import httpx
from langchain_core.tools import tool

from legal_agent.config import get_settings

logger = logging.getLogger(__name__)

# Priority order: SCC Online and Manupatra first, Indian Kanoon as fallback
SITE_SCOPED_SOURCES = [
    ("scconline.com", "SCC Online"),
    ("manupatra.com", "Manupatra"),
    ("livelaw.in", "LiveLaw"),
    ("indiankanoon.org", "Indian Kanoon"),
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
                    if url and url not in seen_urls:
                        seen_urls.add(url)
                        r["_source"] = source_name
                        (high if is_high else low).append(r)

        # High-priority (SCC/Manupatra) first, then low-priority
        selected = high + low

        if not selected:
            return "No relevant legal sources found."

        parts = [f"Found {len(selected)} sources:\n"]
        for i, r in enumerate(selected, 1):
            parts.append(
                f"[{i}] {r.get('title', 'Untitled')}\n"
                f"Source: {r.get('_source', 'Web')}\n"
                f"URL: {r.get('link', '')}\n"
                f"Snippet: {r.get('snippet', '')}\n"
            )

        parts.append(
            "\n---\nCite sources using [1], [2], etc. "
            "If the answer is not supported by the provided sources, "
            'say "No authoritative source found for this proposition."'
        )
        return "\n".join(parts)

    return legal_web_search
