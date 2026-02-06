"""Application configuration using Pydantic Settings."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

# Project root directory (where pyproject.toml is)
PROJECT_ROOT = Path(__file__).parent.parent.parent
ENV_FILE = PROJECT_ROOT / ".env"


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # LLM Configuration
    llm_provider: Literal["openai", "anthropic", "gemini"] = "openai"
    llm_model: str = "gpt-4o-mini"  # Fast model for drafting
    openai_api_key: str | None = None
    gemini_api_key: str | None = None
    anthropic_api_key: str | None = None

    # RAG Engine Configuration
    rag_engine_base_url: str = "http://localhost:8000"
    rag_engine_user_id: str = "legal-agent-service"

    # Service Configuration
    service_host: str = "0.0.0.0"
    service_port: int = 8001
    debug: bool = False

    # Job Configuration
    job_timeout_seconds: int = 300
    job_max_retries: int = 3

    def get_llm_api_key(self) -> str | None:
        """Get the API key for the configured LLM provider."""
        if self.llm_provider == "openai":
            return self.openai_api_key
        elif self.llm_provider == "anthropic":
            return self.anthropic_api_key
        elif self.llm_provider == "gemini":
            return self.gemini_api_key
        return None


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
