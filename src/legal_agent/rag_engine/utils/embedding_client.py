from typing import List
from legal_agent.config import get_settings
import logging

logger = logging.getLogger(__name__)

MULTIMODAL_EMBEDDING_MODEL = "gemini-embedding-2-preview"

class EmbeddingClient:
    _client = None
    _gemini_client = None  # Dedicated Gemini client for multimodal (image) embeddings

    def __init__(self):
        if EmbeddingClient._client is None:
            provider = get_settings().embedding_provider

            if provider == "gemini":
                logger.info("Using Gemini embeddings via google-genai")
                from google import genai

                api_key = get_settings().gemini_api_key or ""
                if not api_key:
                    raise ValueError("GEMINI_API_KEY not found in environment variables")

                EmbeddingClient._client = genai.Client(api_key=api_key)
                EmbeddingClient._model_name = get_settings().embedding_model
                logger.info(f"Gemini embedding client initialized: {EmbeddingClient._model_name}")

            elif provider == "openai":
                logger.info(f"Using OpenAI embeddings: {get_settings().embedding_model}")
                from openai import OpenAI
                EmbeddingClient._client = OpenAI(api_key=get_settings().openai_api_key or "")
                EmbeddingClient._model_name = get_settings().embedding_model

            else:
                logger.info(f"Using HuggingFace embeddings: {get_settings().embedding_model}")
                from sentence_transformers import SentenceTransformer
                EmbeddingClient._client = SentenceTransformer(get_settings().embedding_model)

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

        provider = get_settings().embedding_provider

        if provider == "gemini":
            from google.genai import types
            BATCH_SIZE = 100
            all_embeddings = []

            for i in range(0, len(texts), BATCH_SIZE):
                batch = texts[i:i + BATCH_SIZE]
                logger.debug(f"Processing embedding batch {i // BATCH_SIZE + 1} ({len(batch)} texts)")
                result = EmbeddingClient._client.models.embed_content(
                    model=EmbeddingClient._model_name,
                    contents=batch,
                    config=types.EmbedContentConfig(output_dimensionality=get_settings().vector_size)
                )
                all_embeddings.extend([obj.values for obj in result.embeddings])

            return all_embeddings

        elif provider == "openai":
            response = EmbeddingClient._client.embeddings.create(
                input=texts,
                model=EmbeddingClient._model_name
            )
            return [data.embedding for data in response.data]

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
            config=types.EmbedContentConfig(output_dimensionality=get_settings().vector_size)
        )
        return result.embeddings[0].values

embedding_client = EmbeddingClient()
