"""Application configuration using Pydantic Settings."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

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

    # RAG Engine
    rag_engine_base_url: str = "http://localhost:8000"
    rag_engine_user_id: str = "legal-agent-service"

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

    # Chat LLM (two models, selected per-request)
    chat_llm_openai_model: str = "gpt-4o-mini"
    chat_llm_gemini_model: str = "gemini-2.0-flash"
    chat_llm_default_provider: Literal["openai", "gemini"] = "openai"

    # Jobs
    job_timeout_seconds: int = 300
    job_max_retries: int = 3

    def get_langchain_provider(self) -> str:
        return _LANGCHAIN_PROVIDERS.get(self.llm_provider, self.llm_provider)

    def get_chat_models(self) -> dict[str, tuple[str, str]]:
        """Return {provider_key: (model_name, langchain_provider)} for chat."""
        return {
            "openai": (self.chat_llm_openai_model, "openai"),
            "gemini": (self.chat_llm_gemini_model, "google-genai"),
        }

    def get_llm_api_key(self) -> str | None:
        keys = {"openai": self.openai_api_key, "anthropic": self.anthropic_api_key, "gemini": self.gemini_api_key}
        return keys.get(self.llm_provider)


@lru_cache
def get_settings() -> Settings:
    return Settings()
