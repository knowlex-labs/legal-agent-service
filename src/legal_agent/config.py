"""Application configuration using Pydantic Settings."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).parent.parent.parent
ENV_FILE = PROJECT_ROOT / ".env"

_LANGCHAIN_PROVIDERS = {"openai": "openai", "anthropic": "anthropic", "gemini": "google-genai"}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ENV_FILE, env_file_encoding="utf-8", extra="ignore")

    # LLM (drafting)
    llm_provider: Literal["openai", "anthropic", "gemini"] = "openai"
    llm_model: str = "gpt-4o-mini"
    openai_api_key: str | None = None
    gemini_api_key: str | None = None
    anthropic_api_key: str | None = None

    # RAG Engine — when False, use HTTPRAGClient and do not import the in-process rag_engine
    # (required for low-memory hosts e.g. Render 512MB; point rag_engine_base_url at a RAG-capable service)
    rag_in_process: bool = True
    rag_engine_base_url: str = "http://localhost:8000"

    # Service
    service_host: str = "0.0.0.0"
    service_port: int = 8001
    debug: bool = False

    # PostgreSQL
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "knowlex"
    postgres_username: str = "postgres"
    postgres_password: str = ""
    legal_db_url: str | None = None

    # Chat LLM default (frontend sends model ID directly)
    chat_llm_default_model: str = "gpt-5-mini-2025-08-07"

    # Web search (Serper API)
    serper_api_key: str = ""

    # Jobs
    job_timeout_seconds: int = 300
    job_max_retries: int = 3

    # CORS
    cors_allowed_origins: list[str] = Field(default=["https://app.knowlex.ai"])
    trust_forwarded_headers: bool = False

    # S3
    s3_access_key: str = ""
    s3_secret_key: str = ""
    s3_region_name: str = "ap-south-1"
    s3_bucket_name: str = "knowlex-user-documents"
    s3_signed_url_expiry: int = 3600

    # Embeddings (RAG engine)
    embedding_model: str = "gemini-embedding-2-preview"
    embedding_provider: str = "gemini"
    vector_size: int = 1536
    distance_metric: str = "COSINE"
    chunk_size: int = 800
    chunk_overlap: int = 100
    max_chunk_size: int = 1200

    # LLM extras (RAG engine)
    openai_model: str = "gpt-4o"
    openai_max_tokens: int = 1000
    openai_temperature: float = 0.1
    gemini_model: str = "gemini-2.0-flash"
    gemini_max_tokens: int = 4000
    gemini_temperature: float = 0.1
    enable_json_response: bool = False

    # Qdrant
    qdrant_host: str = "localhost"
    qdrant_port: int = 6333
    qdrant_timeout: int = 30
    qdrant_api_key: str = ""

    # Reranking
    reranker_enabled: bool = True
    reranker_model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"
    reranker_top_k: int = 5

    # Critic
    critic_enabled: bool = False
    critic_model_name: str = "models/gemini-2.5-flash"
    critic_model_api_key: str = ""
    critic_model_temperature: float = 0.1

    # Feedback
    feedback_enabled: bool = True
    feedback_similarity_threshold: float = 0.8

    # Query
    relevance_threshold: float = 0.4

    # LlamaCloud
    llama_cloud_api_key: str = ""

    # Semantic chunking
    semantic_similarity_threshold: float = 0.8
    semantic_min_chunk_size: int = 100
    semantic_model: str = "sentence-transformers/all-MiniLM-L6-v2"

    # Parser
    web_scraper_user_agent: str = "RAG-Engine/1.0"
    web_scraper_timeout: int = 30
    youtube_transcript_fallback: str = "gemini"

    def get_langchain_provider(self) -> str:
        return _LANGCHAIN_PROVIDERS.get(self.llm_provider, self.llm_provider)

    @staticmethod
    def get_langchain_provider_for_model(model_id: str) -> str:
        """Infer LangChain provider from model ID string."""
        if model_id.startswith("gemini"):
            return "google-genai"
        return "openai"

    def get_llm_api_key(self) -> str | None:
        keys = {"openai": self.openai_api_key, "anthropic": self.anthropic_api_key, "gemini": self.gemini_api_key}
        return keys.get(self.llm_provider)



@lru_cache
def get_settings() -> Settings:
    return Settings()
