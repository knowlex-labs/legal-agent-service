"""Lazy-loaded cross-encoder reranker for legal case search."""

import logging

from legal_agent.legal_retrieval.config import MODEL_DEVICE, RERANKER_MODEL

logger = logging.getLogger(__name__)

_reranker = None


def _get_reranker():
    """Lazy-load the CrossEncoder reranker model."""
    global _reranker
    if _reranker is None:
        from sentence_transformers import CrossEncoder

        logger.info(f"Loading reranker model: {RERANKER_MODEL} on {MODEL_DEVICE}")
        _reranker = CrossEncoder(RERANKER_MODEL, device=MODEL_DEVICE)
        logger.info("Reranker model loaded successfully")
    return _reranker


def rerank(query: str, documents: list[dict], top_k: int) -> list[dict]:
    """Rerank documents using the cross-encoder model.

    Args:
        query: The search query.
        documents: List of dicts, each must have a 'text' key.
        top_k: Number of top results to return.

    Returns:
        The top_k documents sorted by reranker score (descending).
    """
    if not documents:
        return []

    model = _get_reranker()
    pairs = [(query, doc["text"]) for doc in documents]
    scores = model.predict(pairs)

    for doc, score in zip(documents, scores):
        doc["rerank_score"] = float(score)

    ranked = sorted(documents, key=lambda d: d["rerank_score"], reverse=True)
    return ranked[:top_k]
