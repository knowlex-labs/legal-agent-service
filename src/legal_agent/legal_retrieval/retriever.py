"""5-stage legal case retrieval pipeline: expand -> embed -> RRF -> citation boost -> rerank."""

import contextvars
import logging
import math

from legal_agent.legal_retrieval.config import CITATION_BOOST, DEFAULT_TOP_K, HYBRID_LIMIT, RERANK_CANDIDATES
from legal_agent.legal_retrieval.db import execute_hybrid_search, get_case_by_id, get_filter_options, get_paragraphs_for_case
from legal_agent.legal_retrieval.embeddings import embed_query
from legal_agent.legal_retrieval.query_expansion import expand_query
from legal_agent.legal_retrieval.reranker import rerank

logger = logging.getLogger(__name__)

_citation_context: contextvars.ContextVar[list[dict]] = contextvars.ContextVar("_citation_context", default=[])


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def _compute_confidence(doc: dict) -> float:
    """Blend rerank score (primary) with cosine similarity (secondary)."""
    rerank_score = doc.get("rerank_score", 0.0)
    sig = _sigmoid(rerank_score)

    cosine_distance = doc.get("cosine_distance")
    if cosine_distance is not None:
        cosine_similarity = 1.0 - (float(cosine_distance) / 2.0)
        confidence = 0.7 * sig + 0.3 * cosine_similarity
    else:
        confidence = sig

    return round(min(max(confidence, 0.0), 1.0), 4)


def get_last_citations() -> list[dict]:
    """Read the most recent structured citations from the context."""
    return _citation_context.get([])


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
