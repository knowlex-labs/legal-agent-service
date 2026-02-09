"""Legal case retriever with 5-stage hybrid search pipeline."""

import logging

from legal_agent.legal_retrieval.config import (
    CITATION_BOOST,
    DEFAULT_TOP_K,
    HYBRID_LIMIT,
    RERANK_CANDIDATES,
)
from legal_agent.legal_retrieval.db import (
    execute_hybrid_search,
    get_case_by_id,
    get_filter_options,
    get_paragraphs_for_case,
)
from legal_agent.legal_retrieval.embeddings import embed_query
from legal_agent.legal_retrieval.query_expansion import expand_query
from legal_agent.legal_retrieval.reranker import rerank

logger = logging.getLogger(__name__)


class LegalCaseRetriever:
    """5-stage legal case retrieval pipeline.

    Pipeline stages:
    1. Query expansion (legal abbreviation expansion)
    2. Embedding generation
    3. Hybrid search (semantic + FTS with RRF)
    4. Citation boost (boost results with citations)
    5. Cross-encoder reranking
    """

    def search(
        self,
        query: str,
        filters: dict | None = None,
        top_k: int = DEFAULT_TOP_K,
    ) -> list[dict]:
        """Run the full 5-stage retrieval pipeline.

        Args:
            query: The search query.
            filters: Optional filters (court, year_from, year_to, judge, act_section_id).
            top_k: Number of final results to return.

        Returns:
            List of result dicts with case metadata and paragraph text.
        """
        # Stage 1: Expand query
        expanded_query = expand_query(query)
        logger.debug(f"Expanded query: {expanded_query}")

        # Stage 2: Embed query
        embedding = embed_query(expanded_query)

        # Stage 3: Hybrid search (semantic + FTS with RRF)
        hybrid_results = execute_hybrid_search(
            embedding=embedding,
            fts_query=expanded_query,
            filters=filters,
            limit=HYBRID_LIMIT,
        )
        logger.debug(f"Hybrid search returned {len(hybrid_results)} results")

        if not hybrid_results:
            return []

        # Stage 4: Citation boost
        for result in hybrid_results:
            result["rrf_score"] = float(result["rrf_score"])
            if result.get("citation"):
                result["rrf_score"] += CITATION_BOOST

        # Sort again after boost
        hybrid_results.sort(key=lambda r: r["rrf_score"], reverse=True)

        # Take top candidates for reranking
        candidates = hybrid_results[:RERANK_CANDIDATES]

        # Stage 5: Cross-encoder reranking
        reranked = rerank(query=query, documents=candidates, top_k=top_k)
        logger.debug(f"Reranking returned {len(reranked)} results")

        return reranked

    def get_case_details(self, case_id: str) -> dict | None:
        """Get full details for a specific case."""
        return get_case_by_id(case_id)

    def get_case_paragraphs(
        self,
        case_id: str,
        query: str | None = None,
        limit: int = 10,
    ) -> list[dict]:
        """Get paragraphs for a case, optionally ordered by relevance to a query."""
        embedding = embed_query(query) if query else None
        return get_paragraphs_for_case(case_id, embedding=embedding, limit=limit)

    def get_filter_options(self) -> dict:
        """Get available filter options for the search UI."""
        return get_filter_options()
