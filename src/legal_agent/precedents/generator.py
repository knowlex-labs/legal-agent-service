"""Precedent finder: build a case brief, search internal SC DB + Firecrawl, synthesise."""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from langchain.chat_models import init_chat_model
from langchain_core.messages import HumanMessage, SystemMessage

from legal_agent.chat.legal_web_search_firecrawl import _firecrawl_search_and_scrape
from legal_agent.clients.rag_client import RAGClient
from legal_agent.config import get_settings
from legal_agent.legal_retrieval.retriever import LegalCaseRetriever
from legal_agent.precedents.prompts import (
    CASE_BRIEF_RAG_QUERY,
    CASE_BRIEF_SYSTEM_PROMPT,
    PRECEDENT_SYNTHESIS_SYSTEM_PROMPT,
)

logger = logging.getLogger(__name__)

# Threshold for skipping the Firecrawl supplement. If the internal DB returns
# at least this many hits, we trust the DB and save credits. Tune if precedent
# quality drops because the DB is still being populated.
_INTERNAL_DB_SUFFICIENT = 5

# Bound Firecrawl scrape cost for the supplement path. This is the only place
# in the precedent pipeline that consumes Firecrawl credits.
_FIRECRAWL_SUPPLEMENT_SOURCES = 3


class PrecedentGenerator:
    """Run the 4-stage precedent-finder pipeline."""

    def __init__(self, rag_client: RAGClient, retriever: LegalCaseRetriever | None):
        self._rag_client = rag_client
        self._retriever = retriever

    async def generate(
        self,
        file_ids: list[str],
        user_id: str,
        top_k: int,
        model: str,
    ) -> str:
        settings = get_settings()
        model_id = model if model else settings.chat_llm_default_model
        langchain_provider = settings.get_langchain_provider_for_model(model_id)
        llm = init_chat_model(model_id, model_provider=langchain_provider)

        # Stage 1: pull relevant passages from case documents
        document_context = ""
        if file_ids:
            logger.info(
                f"[precedents] Fetching RAG context | user={user_id} | files={len(file_ids)}"
            )
            document_context = await self._rag_client.query(
                file_ids=file_ids,
                query=CASE_BRIEF_RAG_QUERY,
                user_id=user_id,
            )

        if not document_context:
            return (
                "# Relevant Precedents\n\n"
                "No case documents were provided, so a case brief could not be built. "
                "Upload case documents and rerun the precedent finder.\n"
            )

        # Stage 2: synthesise case brief + search query
        brief_md = await self._build_brief(llm, document_context)
        search_query = self._extract_search_query(brief_md)
        if not search_query:
            logger.warning("[precedents] Could not extract search query from brief, aborting")
            return (
                "# Relevant Precedents\n\n"
                "The case brief did not yield a usable search query. This usually means the "
                "documents are too sparse to identify concrete legal issues.\n"
            )
        logger.info(f"[precedents] search query: {search_query[:120]}")

        # Stage 3a: internal SC judgments DB
        internal_hits = await self._search_internal_db(search_query, top_k=top_k)
        logger.info(f"[precedents] internal DB returned {len(internal_hits)} hits")

        # Stage 3b: Firecrawl supplement (only if internal DB is thin)
        web_hits: list[dict[str, Any]] = []
        if len(internal_hits) < _INTERNAL_DB_SUFFICIENT:
            web_hits = await self._firecrawl_supplement(search_query)
            logger.info(f"[precedents] Firecrawl supplement returned {len(web_hits)} hits")
        else:
            logger.info(
                f"[precedents] internal DB has ≥ {_INTERNAL_DB_SUFFICIENT} hits, "
                "skipping Firecrawl supplement"
            )

        if not internal_hits and not web_hits:
            return (
                "# Relevant Precedents\n\n"
                "No on-point precedents were found in the internal Supreme Court judgments "
                "database or on the trusted legal web sources for this case.\n"
            )

        # Stage 4: LLM synthesis — rank, dedupe, explain relevance
        synthesis_md = await self._synthesise(llm, brief_md, internal_hits, web_hits)
        return synthesis_md

    async def _build_brief(self, llm, document_context: str) -> str:
        logger.info("[precedents] Building case brief")
        response = await llm.ainvoke(
            [
                SystemMessage(content=CASE_BRIEF_SYSTEM_PROMPT),
                HumanMessage(
                    content="# CASE DOCUMENTS (retrieved from RAG)\n\n" + document_context
                ),
            ]
        )
        content = response.content
        if isinstance(content, list):
            return "".join(
                part if isinstance(part, str) else part.get("text", "") for part in content
            )
        return content

    @staticmethod
    def _extract_search_query(brief_md: str) -> str | None:
        """Pull the single-line search query out of the brief's 'Search Query' section."""
        match = re.search(
            r"##\s*Search Query\s*\n+([^\n#]+)", brief_md, flags=re.IGNORECASE
        )
        if not match:
            return None
        query = match.group(1).strip()
        # Strip any markdown emphasis / quote punctuation
        query = query.strip("*_`\"' ")
        return query or None

    async def _search_internal_db(self, query: str, top_k: int) -> list[dict[str, Any]]:
        if not self._retriever:
            logger.info("[precedents] LegalCaseRetriever unavailable, skipping internal DB")
            return []
        try:
            # retriever.search is sync — run in a thread so we don't block the event loop
            hits = await asyncio.to_thread(self._retriever.search, query, None, top_k)
            return hits or []
        except Exception:
            logger.exception("[precedents] internal DB search failed; falling back to web only")
            return []

    async def _firecrawl_supplement(self, query: str) -> list[dict[str, Any]]:
        settings = get_settings()
        if not settings.firecrawl_api_key:
            logger.info("[precedents] FIRECRAWL_API_KEY not set, skipping web supplement")
            return []
        try:
            results = await _firecrawl_search_and_scrape(
                query=query, num_sources=_FIRECRAWL_SUPPLEMENT_SOURCES
            )
            return results or []
        except Exception:
            logger.exception("[precedents] Firecrawl supplement failed")
            return []

    async def _synthesise(
        self,
        llm,
        brief_md: str,
        internal_hits: list[dict[str, Any]],
        web_hits: list[dict[str, Any]],
    ) -> str:
        candidates_md = self._format_candidates(internal_hits, web_hits)

        user_content = (
            "# CASE BRIEF\n\n"
            + brief_md
            + "\n\n# CANDIDATE PRECEDENTS\n\n"
            + candidates_md
        )

        logger.info(
            f"[precedents] Synthesising | internal={len(internal_hits)} web={len(web_hits)}"
        )
        response = await llm.ainvoke(
            [
                SystemMessage(content=PRECEDENT_SYNTHESIS_SYSTEM_PROMPT),
                HumanMessage(content=user_content),
            ]
        )
        content = response.content
        if isinstance(content, list):
            return "".join(
                part if isinstance(part, str) else part.get("text", "") for part in content
            )
        return content

    @staticmethod
    def _format_candidates(
        internal_hits: list[dict[str, Any]], web_hits: list[dict[str, Any]]
    ) -> str:
        parts: list[str] = []

        if internal_hits:
            parts.append("## From internal Supreme Court judgments database\n")
            for i, hit in enumerate(internal_hits, 1):
                parts.append(f"### Internal candidate {i}")
                parts.append(f"- Case: {hit.get('case_title', 'Unknown')}")
                if hit.get("citation"):
                    parts.append(f"- Citation: {hit.get('citation')}")
                if hit.get("court"):
                    parts.append(f"- Court: {hit.get('court')}")
                if hit.get("year"):
                    parts.append(f"- Year: {hit.get('year')}")
                if hit.get("bench"):
                    parts.append(f"- Bench: {hit.get('bench')}")
                if hit.get("paragraph_number") is not None:
                    parts.append(f"- Paragraph number: {hit.get('paragraph_number')}")
                if hit.get("text"):
                    parts.append(f"- Paragraph text: {hit.get('text')}")
                parts.append("")

        if web_hits:
            parts.append("## From trusted legal web sources\n")
            for i, hit in enumerate(web_hits, 1):
                parts.append(f"### Web candidate {i}")
                if hit.get("title"):
                    parts.append(f"- Title: {hit.get('title')}")
                if hit.get("url"):
                    parts.append(f"- URL: {hit.get('url')}")
                if hit.get("label"):
                    parts.append(f"- Source: {hit.get('label')}")
                body = hit.get("body") or ""
                if body:
                    # Cap body per candidate so the synthesis prompt stays bounded
                    snippet = body[:1500].rstrip()
                    if len(body) > 1500:
                        snippet += "…"
                    parts.append(f"- Excerpt: {snippet}")
                parts.append("")

        return "\n".join(parts) if parts else "No candidates retrieved."
