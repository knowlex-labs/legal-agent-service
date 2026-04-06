"""Legal query embedding — uses the shared RAG embedding client (API-based) when available,
falls back to a local BGE-M3 model only if no API provider is configured."""

import logging

from legal_agent.config import get_settings
from legal_agent.legal_retrieval.config import EMBEDDING_MODEL, EMBEDDING_QUERY_PREFIX, MODEL_DEVICE

logger = logging.getLogger(__name__)
_local_model = None


def _get_local_model():
    global _local_model
    if _local_model is None:
        from sentence_transformers import SentenceTransformer

        logger.info(f"Loading LOCAL embedding model: {EMBEDDING_MODEL} (high memory)")
        _local_model = SentenceTransformer(EMBEDDING_MODEL, device=MODEL_DEVICE)
    return _local_model


def _embed_via_api(text: str) -> list[float]:
    """Embed using the configured API provider (OpenAI / Gemini) — zero local memory."""
    from legal_agent.rag_engine.utils.embedding_client import embedding_client

    return embedding_client.generate_single_embedding(text)


def embed_query(query: str) -> list[float]:
    """Embed a query. Prefers API-based embeddings to avoid loading ~700MB local model."""
    provider = get_settings().embedding_provider
    if provider in ("openai", "gemini"):
        return _embed_via_api(EMBEDDING_QUERY_PREFIX + query)

    # Fallback: local model (only for HuggingFace provider)
    model = _get_local_model()
    return model.encode(EMBEDDING_QUERY_PREFIX + query, normalize_embeddings=True).tolist()
