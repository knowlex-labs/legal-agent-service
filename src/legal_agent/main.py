"""FastAPI application entry point."""

import logging
import os
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI


def _setup_logging() -> None:
    """Configure logging for the application."""
    log_format = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    log_level = logging.DEBUG if os.getenv("DEBUG", "").lower() == "true" else logging.INFO

    logging.basicConfig(
        level=log_level,
        format=log_format,
        handlers=[logging.StreamHandler(sys.stdout)],
    )

    # Reduce noise from third-party libraries
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


_setup_logging()
logger = logging.getLogger(__name__)

from fastapi.middleware.cors import CORSMiddleware

from legal_agent.api.routes import router, set_services
from legal_agent.chat.agent import ChatAgent
from legal_agent.chat.routes import chat_router, set_chat_agent
from legal_agent.clients.rag_client import HTTPRAGClient, MockRAGClient
from legal_agent.config import get_settings
from legal_agent.legal_retrieval import LegalCaseRetriever
from legal_agent.legal_retrieval.config import get_legal_db_url
from legal_agent.legal_retrieval.db import close_pool
from legal_agent.services.draft_service import DraftService
from legal_agent.services.job_manager import JobManager


def _setup_llm_environment() -> None:
    """Set up LLM API keys as environment variables for LangChain."""
    settings = get_settings()

    # Set API keys in environment so LangChain provider packages can find them
    if settings.openai_api_key:
        os.environ.setdefault("OPENAI_API_KEY", settings.openai_api_key)
    if settings.anthropic_api_key:
        os.environ.setdefault("ANTHROPIC_API_KEY", settings.anthropic_api_key)
    if settings.gemini_api_key:
        os.environ.setdefault("GOOGLE_API_KEY", settings.gemini_api_key)


# Set up environment before anything else
_setup_llm_environment()

# Global instances
job_manager: JobManager | None = None
rag_client: HTTPRAGClient | MockRAGClient | None = None
chat_agent: ChatAgent | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler for startup and shutdown."""
    global job_manager, rag_client, chat_agent

    settings = get_settings()

    logger.info("Starting legal-agent-service...")
    logger.info(f"LLM provider: {settings.llm_provider}, model: {settings.llm_model}")
    logger.info(f"RAG engine: {settings.rag_engine_base_url}")

    job_manager = JobManager()

    if settings.debug:
        logger.info("Debug mode: using MockRAGClient")
        rag_client = MockRAGClient()
    else:
        rag_client = HTTPRAGClient(settings)

    draft_service = DraftService(
        settings=settings,
        job_manager=job_manager,
        rag_client=rag_client,
    )

    set_services(draft_service, job_manager)

    # Initialize legal retrieval + chat agent
    legal_retriever = LegalCaseRetriever()
    db_url = get_legal_db_url()

    chat_agent = ChatAgent(retriever=legal_retriever)
    await chat_agent.initialize(db_url)
    set_chat_agent(chat_agent)

    logger.info(
        f"Chat agent initialized (provider={settings.chat_llm_provider}, "
        f"model={settings.chat_llm_model})"
    )
    logger.info("Service initialized successfully")

    yield

    logger.info("Shutting down...")
    if chat_agent:
        await chat_agent.close()
    close_pool()
    if job_manager:
        await job_manager.cleanup()
    if isinstance(rag_client, HTTPRAGClient):
        await rag_client.close()
    logger.info("Shutdown complete")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    settings = get_settings()

    app = FastAPI(
        title="Legal Agent Service",
        description="AI-powered legal document drafting service for Indian legal firms",
        version="0.1.0",
        lifespan=lifespan,
    )

    # Add CORS middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # Configure appropriately for production
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Include API routes
    app.include_router(router, prefix="/api/v1", tags=["drafts"])
    app.include_router(chat_router, prefix="/api/v1", tags=["chat"])

    # Add a root health check
    @app.get("/")
    async def root():
        return {
            "service": "legal-agent-service",
            "version": "0.1.0",
            "docs": "/docs",
        }

    return app


# Create the app instance
app = create_app()


def run() -> None:
    """Run the application server. Entry point for `uv run legal-agent`."""
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "legal_agent.main:app",
        host=settings.service_host,
        port=settings.service_port,
        reload=settings.debug,
    )


if __name__ == "__main__":
    run()
