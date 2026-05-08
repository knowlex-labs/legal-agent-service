import re
import time
from typing import Callable, List, Optional, TypeVar
from legal_agent.config import get_settings
import logging

logger = logging.getLogger(__name__)

MULTIMODAL_EMBEDDING_MODEL = "gemini-embedding-2-preview"

_T = TypeVar("_T")
_RETRY_AFTER_MS_RE = re.compile(r"try again in\s+(\d+(?:\.\d+)?)\s*ms", re.IGNORECASE)
_RETRY_AFTER_S_RE = re.compile(r"try again in\s+(\d+(?:\.\d+)?)\s*s\b", re.IGNORECASE)
_RETRY_DELAY_RE = re.compile(r"retry[_-]?delay[^0-9]*(\d+(?:\.\d+)?)\s*s", re.IGNORECASE)


def _is_rate_limit(exc: BaseException) -> bool:
    try:
        from openai import RateLimitError as _OpenAIRateLimitError
        if isinstance(exc, _OpenAIRateLimitError):
            return True
    except Exception:
        pass
    status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    if status == 429:
        return True
    msg = str(exc).lower()
    return (
        "429" in msg
        or "rate limit" in msg
        or "resource_exhausted" in msg
        or "resource has been exhausted" in msg
    )


def _extract_retry_after(exc: BaseException) -> Optional[float]:
    resp = getattr(exc, "response", None)
    headers = getattr(resp, "headers", None) if resp is not None else None
    if headers:
        try:
            ms = headers.get("retry-after-ms") or headers.get("Retry-After-Ms")
            if ms is not None:
                return max(0.001, float(ms) / 1000.0)
            sec = headers.get("retry-after") or headers.get("Retry-After")
            if sec is not None:
                return max(0.0, float(sec))
        except (TypeError, ValueError):
            pass
    msg = str(exc)
    m = _RETRY_AFTER_MS_RE.search(msg)
    if m:
        return max(0.001, float(m.group(1)) / 1000.0)
    m = _RETRY_AFTER_S_RE.search(msg)
    if m:
        return max(0.0, float(m.group(1)))
    m = _RETRY_DELAY_RE.search(msg)
    if m:
        return max(0.0, float(m.group(1)))
    return None


def _retry_on_rate_limit(provider: str, fn: Callable[[], _T], *, max_retries: int = 3) -> _T:
    for attempt in range(max_retries + 1):
        try:
            return fn()
        except Exception as e:
            if attempt >= max_retries or not _is_rate_limit(e):
                raise
            hint = _extract_retry_after(e)
            wait = hint if hint is not None else (1.0 * (2 ** attempt))  # 1s, 2s, 4s
            logger.warning(
                f"{provider} embedding rate-limited; "
                f"retrying in {wait:.3f}s (attempt {attempt + 1}/{max_retries})"
            )
            time.sleep(wait)
    raise RuntimeError(f"_retry_on_rate_limit fell through for {provider}")


class EmbeddingClient:
    _client = None
    _gemini_client = None  # Dedicated Gemini client for multimodal (image) embeddings

    def __init__(self):
        if EmbeddingClient._client is None:
            settings = get_settings()
            # Workspace RAG uses its own config. Falls back to legacy
            # embedding_* for backward compat (see config.py).
            provider = settings.get_workspace_embedding_provider()
            model = settings.get_workspace_embedding_model()

            if provider == "gemini":
                logger.info("Using Gemini embeddings via google-genai (workspace)")
                from google import genai

                api_key = settings.gemini_api_key or ""
                if not api_key:
                    raise ValueError("GEMINI_API_KEY not found in environment variables")

                EmbeddingClient._client = genai.Client(api_key=api_key)
                EmbeddingClient._model_name = model
                logger.info(f"Gemini embedding client initialized: {EmbeddingClient._model_name}")

            elif provider == "openai":
                logger.info(f"Using OpenAI embeddings: {model}")
                from openai import OpenAI
                EmbeddingClient._client = OpenAI(api_key=settings.openai_api_key or "")
                EmbeddingClient._model_name = model

            else:
                logger.info(f"Using HuggingFace embeddings: {model}")
                from sentence_transformers import SentenceTransformer
                EmbeddingClient._client = SentenceTransformer(model)
                EmbeddingClient._model_name = model

        # Always initialise a dedicated Gemini client for image (multimodal) embeddings
        if EmbeddingClient._gemini_client is None:
            api_key = get_settings().gemini_api_key or ""
            if api_key:
                from google import genai
                EmbeddingClient._gemini_client = genai.Client(api_key=api_key)
                logger.info(f"Gemini multimodal embedding client initialised: {MULTIMODAL_EMBEDDING_MODEL}")
            else:
                logger.warning("GEMINI_API_KEY not set — image embedding will not work")

    def generate_embeddings(self, texts: List[str]) -> List[List[float]]:
        if not texts:
            logger.warning("Empty texts list provided for embedding generation")
            return []

        settings = get_settings()
        provider = settings.get_workspace_embedding_provider()
        vector_size = settings.get_workspace_vector_size()

        if provider == "gemini":
            from google.genai import types
            BATCH_SIZE = 100
            all_embeddings = []

            for i in range(0, len(texts), BATCH_SIZE):
                batch = texts[i:i + BATCH_SIZE]
                logger.debug(f"Processing embedding batch {i // BATCH_SIZE + 1} ({len(batch)} texts)")
                result = _retry_on_rate_limit(
                    "Gemini",
                    lambda: EmbeddingClient._client.models.embed_content(
                        model=EmbeddingClient._model_name,
                        contents=batch,
                        config=types.EmbedContentConfig(output_dimensionality=vector_size),
                    ),
                )
                all_embeddings.extend([obj.values for obj in result.embeddings])

            return all_embeddings

        elif provider == "openai":
            BATCH_SIZE = 20  # text-embedding-3-* accepts ≤8192 tokens per input; batch of 20 fits Tier 1 TPM headroom even for Indic text
            all_embeddings = []
            for i in range(0, len(texts), BATCH_SIZE):
                batch = texts[i:i + BATCH_SIZE]
                response = _retry_on_rate_limit(
                    "OpenAI",
                    lambda: EmbeddingClient._client.embeddings.create(
                        input=batch,
                        model=EmbeddingClient._model_name,
                    ),
                )
                all_embeddings.extend([data.embedding for data in response.data])
            return all_embeddings

        else:
            return EmbeddingClient._client.encode(texts).tolist()

    def generate_single_embedding(self, text: str) -> List[float]:
        return self.generate_embeddings([text])[0]

    def generate_image_embedding(self, image_bytes: bytes, mime_type: str = "image/png") -> List[float]:
        """Embed an image using Gemini multimodal embeddings (always uses Gemini regardless of text provider)."""
        from google.genai import types

        if EmbeddingClient._gemini_client is None:
            raise RuntimeError("Gemini API key not configured — cannot generate image embeddings")

        result = EmbeddingClient._gemini_client.models.embed_content(
            model=MULTIMODAL_EMBEDDING_MODEL,
            contents=types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
            config=types.EmbedContentConfig(output_dimensionality=get_settings().get_workspace_vector_size())
        )
        return result.embeddings[0].values

embedding_client = EmbeddingClient()
