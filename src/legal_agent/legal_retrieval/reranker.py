"""Legal reranker — uses the shared RAG reranker (which may be API-based or local),
falls back to a local BGE cross-encoder only when needed."""

import logging

from legal_agent.config import get_settings
from legal_agent.legal_retrieval.config import MODEL_DEVICE, RERANKER_MODEL

logger = logging.getLogger(__name__)
_local_reranker = None


def _get_local_reranker():
    global _local_reranker
    if _local_reranker is None:
        from sentence_transformers import CrossEncoder

        logger.info(f"Loading LOCAL reranker: {RERANKER_MODEL} (high memory)")
        _local_reranker = CrossEncoder(RERANKER_MODEL, device=MODEL_DEVICE)
    return _local_reranker


def rerank(query: str, documents: list[dict], top_k: int) -> list[dict]:
    """Score query-document pairs, return top_k sorted by score.

    When the shared RAG reranker is available and enabled, delegates to it to avoid
    loading a separate ~700MB local model. Falls back to local BGE cross-encoder
    only if the shared reranker is unavailable.
    """
    if not documents:
        return []

    # Try the shared RAG reranker first (may use a lighter model already loaded)
    settings = get_settings()
    if settings.rag_in_process and settings.reranker_enabled:
        try:
            from legal_agent.rag_engine.core.reranker import reranker as rag_reranker

            if rag_reranker.is_available():
                # Adapt documents to the format the RAG reranker expects
                reranked = rag_reranker.rerank(query, documents, top_k=top_k)
                for i, doc in enumerate(reranked):
                    doc.setdefault("rerank_score", 1.0 - (i * 0.01))
                return reranked
        except Exception:
            logger.debug("Shared RAG reranker unavailable, falling back to local model")

    # Fallback: local BGE cross-encoder
    scores = _get_local_reranker().predict([(query, doc["text"]) for doc in documents])
    for doc, score in zip(documents, scores):
        doc["rerank_score"] = float(score)

    return sorted(documents, key=lambda d: d["rerank_score"], reverse=True)[:top_k]
