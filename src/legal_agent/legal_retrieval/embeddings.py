"""Lazy-loaded BGE-M3 embedding model."""

import logging

from legal_agent.legal_retrieval.config import EMBEDDING_MODEL, EMBEDDING_QUERY_PREFIX, MODEL_DEVICE

logger = logging.getLogger(__name__)
_model = None


def _get_model():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer

        logger.info(f"Loading embedding model: {EMBEDDING_MODEL}")
        _model = SentenceTransformer(EMBEDDING_MODEL, device=MODEL_DEVICE)
    return _model


def warmup():
    """Pre-load the embedding model so the first request is fast."""
    _get_model()


def embed_query(query: str) -> list[float]:
    """Embed a query with BGE prefix and L2 normalization."""
    model = _get_model()
    return model.encode(EMBEDDING_QUERY_PREFIX + query, normalize_embeddings=True).tolist()
