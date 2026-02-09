"""Lazy-loaded embedding model for legal case search."""

import logging

from legal_agent.legal_retrieval.config import (
    EMBEDDING_MODEL,
    EMBEDDING_QUERY_PREFIX,
    MODEL_DEVICE,
)

logger = logging.getLogger(__name__)

_model = None


def _get_model():
    """Lazy-load the SentenceTransformer embedding model."""
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer

        logger.info(f"Loading embedding model: {EMBEDDING_MODEL} on {MODEL_DEVICE}")
        _model = SentenceTransformer(EMBEDDING_MODEL, device=MODEL_DEVICE)
        logger.info("Embedding model loaded successfully")
    return _model


def embed_query(query: str) -> list[float]:
    """Embed a query string, prepending the BGE query prefix and normalizing.

    Returns a list of floats (the embedding vector).
    """
    model = _get_model()
    prefixed = EMBEDDING_QUERY_PREFIX + query
    embedding = model.encode(prefixed, normalize_embeddings=True)
    return embedding.tolist()
