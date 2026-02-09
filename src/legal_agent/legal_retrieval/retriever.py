"""5-stage legal case retrieval pipeline: expand -> embed -> RRF -> citation boost -> rerank."""

import logging

from legal_agent.legal_retrieval.config import CITATION_BOOST, DEFAULT_TOP_K, HYBRID_LIMIT, RERANK_CANDIDATES
from legal_agent.legal_retrieval.db import execute_hybrid_search, get_case_by_id, get_filter_options, get_paragraphs_for_case
from legal_agent.legal_retrieval.embeddings import embed_query
from legal_agent.legal_retrieval.query_expansion import expand_query
from legal_agent.legal_retrieval.reranker import rerank

logger = logging.getLogger(__name__)


class LegalCaseRetriever:

    def search(self, query: str, filters: dict | None = None, top_k: int = DEFAULT_TOP_K) -> list[dict]:
        expanded = expand_query(query)
        logger.info(f"[retriever] query='{query}' | expanded='{expanded}'")

        embedding = embed_query(expanded)
        logger.info(f"[retriever] embedding generated (dim={len(embedding)})")

        results = execute_hybrid_search(embedding=embedding, fts_query=expanded, filters=filters, limit=HYBRID_LIMIT)
        logger.info(f"[retriever] hybrid search: {len(results)} candidates")
        if not results:
            return []

        for r in results:
            r["rrf_score"] = float(r["rrf_score"])
            if r.get("citation"):
                r["rrf_score"] += CITATION_BOOST

        results.sort(key=lambda r: r["rrf_score"], reverse=True)
        reranked = rerank(query=query, documents=results[:RERANK_CANDIDATES], top_k=top_k)
        logger.info(f"[retriever] reranked: {len(reranked)} final results")
        return reranked

    def get_case_details(self, case_id: str) -> dict | None:
        return get_case_by_id(case_id)

    def get_case_paragraphs(self, case_id: str, query: str | None = None, limit: int = 10) -> list[dict]:
        embedding = embed_query(query) if query else None
        return get_paragraphs_for_case(case_id, embedding=embedding, limit=limit)

    def get_filter_options(self) -> dict:
        return get_filter_options()
