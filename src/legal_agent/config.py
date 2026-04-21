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

    # LLM (drafting) — default is GPT-5.4 (OpenAI's current flagship as of Apr 2026).
    # Per-request model overrides still work via CreateDraftJobRequest.model.
    llm_provider: Literal["openai", "anthropic", "gemini"] = "openai"
    llm_model: str = "gpt-5.4"
    openai_api_key: str | None = None
    gemini_api_key: str | None = None
    anthropic_api_key: str | None = None
    sarvam_api_key: str | None = None

    # OCR backend selection. "gemini" = Gemini Vision (current default).
    # "sarvam" = Sarvam Document Intelligence (22 Indian languages + English; ≤10 pages per job).
    ocr_provider: Literal["gemini", "sarvam"] = "gemini"
    # Concurrent Sarvam jobs when chunking long PDFs. Each chunk is ≤10 pages.
    sarvam_ocr_concurrency: int = 4
    # Concurrent Gemini Vision calls per PDF (one call per page). Gemini API
    # rate-limits: free tier ~15 rpm, paid tier much higher. Lower if rate-limited.
    gemini_ocr_concurrency: int = 4
    # Language hint passed to Sarvam. Examples: "en-IN", "hi-IN", "ta-IN", "unknown".
    sarvam_ocr_language: str = "unknown"
    # Sarvam chat/translation model. Options: sarvam-m (24B, default), sarvam-30b, sarvam-105b.
    # Used when a request selects provider "sarvam" for translation or draft chat.
    sarvam_chat_model: str = "sarvam-m"
    # Sarvam's OpenAI-compatible base URL — override only if Sarvam changes hosts.
    sarvam_api_base_url: str = "https://api.sarvam.ai/v1"
    # Content-addressed OCR cache in S3. Same PDF bytes → same cache entry across
    # retries, different translations, and RAG/draft flows. Disable for debugging.
    ocr_cache_enabled: bool = True
    ocr_cache_prefix: str = "ocr-cache"

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
    chat_llm_default_model: str = "gemini-2.0-flash"

    # Web search — Firecrawl is primary (scrapes full article text), Serper is fallback.
    # Both restricted to the 3 trusted Indian legal sources below (no Indian Kanoon).
    # Scrape count is chosen per call by the LLM via the `num_sources` tool arg
    # (clamped 1-5 in legal_web_search_firecrawl.py), so it is not set here.
    firecrawl_api_key: str = ""
    firecrawl_search_domains: list[str] = Field(
        default=["livelaw.in", "scconline.com", "barandbench.com"]
    )
    serper_api_key: str = ""

    # Draft+verify pipeline for workspace chat with web_search=True.
    # Each claim verified = 1 Firecrawl search credit (no scrape).
    # Cap extraction to keep cost bounded; lower if credits spike.
    firecrawl_verify_max_claims: int = 7
    firecrawl_verify_concurrency: int = 5

    # Jobs
    job_timeout_seconds: int = 1800
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

    # Document encryption (AES-256-GCM envelope encryption, matches platform API)
    document_encryption_master_key: str = ""

    # ── Embeddings ────────────────────────────────────────────────────────
    # Each RAG system has its own embedding config so we can mix providers
    # without cross-contamination. `embedding_*` (legacy, below) is the
    # fallback for anything that hasn't been migrated yet.

    # Workspace RAG (rag_engine/) — user documents in Qdrant.
    # Default mirrors the legacy single config until we re-index to BGE.
    workspace_embedding_provider: str | None = None  # falls back to embedding_provider
    workspace_embedding_model: str | None = None     # falls back to embedding_model
    workspace_vector_size: int | None = None         # falls back to vector_size

    # Legal retrieval (legal_retrieval/) — Supreme Court judgments in
    # PostgreSQL + pgvector. DB column is halfvec(3072) — must use Gemini.
    legal_embedding_provider: str = "gemini"
    legal_embedding_model: str = "gemini-embedding-2-preview"
    legal_vector_size: int = 3072

    # Legacy single config (kept for backward compat + workspace fallback).
    embedding_model: str = "BAAI/bge-m3"
    embedding_provider: str = "huggingface"
    vector_size: int = 1024
    distance_metric: str = "COSINE"
    chunk_size: int = 800
    chunk_overlap: int = 100
    max_chunk_size: int = 1200

    def get_workspace_embedding_provider(self) -> str:
        return self.workspace_embedding_provider or self.embedding_provider

    def get_workspace_embedding_model(self) -> str:
        return self.workspace_embedding_model or self.embedding_model

    def get_workspace_vector_size(self) -> int:
        return self.workspace_vector_size or self.vector_size

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

    # Feedback
    feedback_enabled: bool = True
    feedback_similarity_threshold: float = 0.8

    # Query
    relevance_threshold: float = 0.4

    # Semantic chunking
    semantic_similarity_threshold: float = 0.8
    semantic_min_chunk_size: int = 100
    semantic_model: str = "sentence-transformers/all-MiniLM-L6-v2"

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
