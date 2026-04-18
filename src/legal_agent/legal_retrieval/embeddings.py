"""Legal query embedding — dedicated config for case law retrieval.

This module is intentionally isolated from `rag_engine/utils/embedding_client.py`
(which serves workspace docs). Case law judgments are stored in pgvector with
halfvec(3072) and must be queried with the exact same model that indexed them —
currently Gemini `gemini-embedding-2-preview`. The config is in
`Settings.legal_embedding_*`, independent of the workspace config.
"""

import logging

from legal_agent.config import get_settings
from legal_agent.legal_retrieval.config import EMBEDDING_QUERY_PREFIX, MODEL_DEVICE

logger = logging.getLogger(__name__)

# Cached local model (HuggingFace fallback only — avoids loading ~700MB until needed).
_local_model = None


def _embed_via_gemini(text: str, model: str, dim: int) -> list[float]:
    """Embed using Google Gemini's embedding API at the configured dimensionality."""
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=get_settings().gemini_api_key or "")
    response = client.models.embed_content(
        model=model,
        contents=text,
        config=types.EmbedContentConfig(
            task_type="RETRIEVAL_QUERY",
            output_dimensionality=dim,
        ),
    )
    # Newer SDK returns a list of ContentEmbedding objects.
    embedding = response.embeddings[0].values
    return list(embedding)


def _embed_via_openai(text: str, model: str, dim: int | None) -> list[float]:
    """Embed using OpenAI's embeddings API. `dim` truncates 3-large/3-small."""
    from openai import OpenAI

    client = OpenAI(api_key=get_settings().openai_api_key)
    kwargs: dict = {"model": model, "input": text}
    # `text-embedding-3-*` supports the `dimensions` param; `ada-002` does not.
    if dim and not model.startswith("text-embedding-ada"):
        kwargs["dimensions"] = dim
    response = client.embeddings.create(**kwargs)
    return response.data[0].embedding


def _embed_via_local(text: str, model_name: str) -> list[float]:
    """Embed using a local HuggingFace model (e.g. BAAI/bge-m3). Lazy-loads."""
    global _local_model
    if _local_model is None:
        from sentence_transformers import SentenceTransformer

        logger.info(f"Loading LOCAL legal embedding model: {model_name}")
        _local_model = SentenceTransformer(model_name, device=MODEL_DEVICE)
    return _local_model.encode(text, normalize_embeddings=True).tolist()


def embed_query(query: str) -> list[float]:
    """Embed a legal search query using the dedicated legal_embedding config.

    Uses `legal_embedding_provider` + `legal_embedding_model` + `legal_vector_size`.
    Matches the model that indexed the case law DB so dimensions align.
    """
    settings = get_settings()
    provider = settings.legal_embedding_provider
    model = settings.legal_embedding_model
    dim = settings.legal_vector_size

    # BGE-style models expect a query-instruction prefix; Gemini/OpenAI don't.
    prefixed = EMBEDDING_QUERY_PREFIX + query if provider == "huggingface" else query

    if provider == "gemini":
        return _embed_via_gemini(prefixed, model, dim)
    if provider == "openai":
        return _embed_via_openai(prefixed, model, dim)
    if provider == "huggingface":
        return _embed_via_local(prefixed, model)

    raise ValueError(
        f"Unsupported legal_embedding_provider: {provider!r}. "
        f"Expected one of: gemini, openai, huggingface."
    )
