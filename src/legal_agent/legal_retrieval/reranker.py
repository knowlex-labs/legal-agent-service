"""Lazy-loaded BGE cross-encoder reranker."""

import logging

from legal_agent.legal_retrieval.config import MODEL_DEVICE, RERANKER_MODEL

logger = logging.getLogger(__name__)
_reranker = None


def _get_reranker():
    global _reranker
    if _reranker is None:
        from sentence_transformers import CrossEncoder

        logger.info(f"Loading reranker: {RERANKER_MODEL}")
        _reranker = CrossEncoder(RERANKER_MODEL, device=MODEL_DEVICE)
    return _reranker


def rerank(query: str, documents: list[dict], top_k: int) -> list[dict]:
    """Score query-document pairs with cross-encoder, return top_k sorted by score."""
    if not documents:
        return []

    scores = _get_reranker().predict([(query, doc["text"]) for doc in documents])
    for doc, score in zip(documents, scores):
        doc["rerank_score"] = float(score)

    return sorted(documents, key=lambda d: d["rerank_score"], reverse=True)[:top_k]
