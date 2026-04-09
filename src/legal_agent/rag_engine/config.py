"""
Compatibility shim — maps the old rag_engine Config.* namespace to the
central Settings object so no rag_engine file needs to change.
"""

from legal_agent.config import get_settings


class _EmbeddingConfig:
    @property
    def MODEL_NAME(self):
        return get_settings().embedding_model

    @property
    def PROVIDER(self):
        return get_settings().embedding_provider

    @property
    def VECTOR_SIZE(self):
        return get_settings().vector_size

    @property
    def DISTANCE_METRIC(self):
        return get_settings().distance_metric

    @property
    def CHUNK_SIZE(self):
        return get_settings().chunk_size

    @property
    def CHUNK_OVERLAP(self):
        return get_settings().chunk_overlap

    @property
    def MAX_CHUNK_SIZE(self):
        return get_settings().max_chunk_size


class _LlmConfig:
    @property
    def PROVIDER(self):
        return get_settings().llm_provider

    @property
    def OPENAI_API_KEY(self):
        return get_settings().openai_api_key or ""

    @property
    def OPENAI_MODEL(self):
        return get_settings().openai_model

    @property
    def OPENAI_MAX_TOKENS(self):
        return get_settings().openai_max_tokens

    @property
    def OPENAI_TEMPERATURE(self):
        return get_settings().openai_temperature

    @property
    def GEMINI_API_KEY(self):
        return get_settings().gemini_api_key or ""

    @property
    def GEMINI_MODEL(self):
        return get_settings().gemini_model

    @property
    def GEMINI_MAX_TOKENS(self):
        return get_settings().gemini_max_tokens

    @property
    def GEMINI_TEMPERATURE(self):
        return get_settings().gemini_temperature

    @property
    def ENABLE_JSON_RESPONSE(self):
        return get_settings().enable_json_response


class _RerankingConfig:
    @property
    def RERANKER_ENABLED(self):
        return get_settings().reranker_enabled

    @property
    def RERANKER_MODEL(self):
        return get_settings().reranker_model

    @property
    def RERANKER_TOP_K(self):
        return get_settings().reranker_top_k


class _FeedbackConfig:
    @property
    def FEEDBACK_ENABLED(self):
        return get_settings().feedback_enabled

    @property
    def FEEDBACK_SIMILARITY_THRESHOLD(self):
        return get_settings().feedback_similarity_threshold


class _QueryConfig:
    @property
    def RELEVANCE_THRESHOLD(self):
        return get_settings().relevance_threshold


class _QdrantConfig:
    @property
    def HOST(self):
        return get_settings().qdrant_host

    @property
    def PORT(self):
        return get_settings().qdrant_port

    @property
    def TIMEOUT(self):
        return get_settings().qdrant_timeout

    @property
    def API_KEY(self):
        return get_settings().qdrant_api_key


class _Config:
    embedding = _EmbeddingConfig()
    llm = _LlmConfig()
    reranking = _RerankingConfig()
    feedback = _FeedbackConfig()
    query = _QueryConfig()
    qdrant = _QdrantConfig()


Config = _Config()
