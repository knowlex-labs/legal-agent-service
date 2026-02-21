"""FastAPI application entry point."""

import asyncio
import logging
import os
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI


def _setup_logging() -> None:
    log_format = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    log_level = logging.DEBUG if os.getenv("DEBUG", "").lower() == "true" else logging.INFO
    logging.basicConfig(level=log_level, format=log_format, handlers=[logging.StreamHandler(sys.stdout)])
    for noisy in ("httpx", "httpcore", "uvicorn.access"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


_setup_logging()
logger = logging.getLogger(__name__)

from fastapi.middleware.cors import CORSMiddleware

from legal_agent.api.routes import router, set_services
from legal_agent.case_agent import CaseAgent, case_agent_router, set_case_agent
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
    settings = get_settings()
    env_keys = {
        "OPENAI_API_KEY": settings.openai_api_key,
        "ANTHROPIC_API_KEY": settings.anthropic_api_key,
        "GOOGLE_API_KEY": settings.gemini_api_key,
    }
    for var, val in env_keys.items():
        if val:
            os.environ.setdefault(var, val)


_setup_llm_environment()

job_manager: JobManager | None = None
rag_client: HTTPRAGClient | MockRAGClient | None = None
chat_agent: ChatAgent | None = None
case_agent: CaseAgent | None = None


async def _init_chat_agent():
    """Initialize chat agent in background so the server can start accepting requests."""
    global chat_agent
    try:
        chat_agent = ChatAgent(retriever=LegalCaseRetriever())
        await chat_agent.initialize(get_legal_db_url())
        set_chat_agent(chat_agent)
        logger.info("Chat agent fully initialized")
    except Exception:
        logger.exception("Failed to initialize chat agent")


async def _init_case_agent():
    """Initialize case agent in background so the server can start accepting requests."""
    global case_agent
    try:
        case_agent = CaseAgent(retriever=LegalCaseRetriever(), rag_client=rag_client)
        await case_agent.initialize()
        set_case_agent(case_agent)
        logger.info("Case agent fully initialized")
    except Exception:
        logger.exception("Failed to initialize case agent")


@asynccontextmanager
async def lifespan(app: FastAPI):
    global job_manager, rag_client

    settings = get_settings()
    chat_models = settings.get_chat_models()
    models_str = ", ".join(f"{k}={v[0]}" for k, v in chat_models.items())
    logger.info(f"Starting legal-agent-service (draft={settings.llm_model}, chat=[{models_str}])")

    job_manager = JobManager()
    rag_client = MockRAGClient() if settings.debug else HTTPRAGClient(settings)

    draft_service = DraftService(settings=settings, job_manager=job_manager, rag_client=rag_client)
    set_services(draft_service, job_manager)

    # Start agent initialization in background to avoid blocking server startup
    init_task = asyncio.create_task(_init_chat_agent())
    case_init_task = asyncio.create_task(_init_case_agent())

    logger.info("Service ready")
    yield

    logger.info("Shutting down...")
    # Wait for init to finish before cleanup
    for task in (init_task, case_init_task):
        if not task.done():
            task.cancel()
    if chat_agent:
        await chat_agent.close()
    close_pool()
    if job_manager:
        await job_manager.cleanup()
    if isinstance(rag_client, HTTPRAGClient):
        await rag_client.close()


def create_app() -> FastAPI:
    app = FastAPI(
        title="Legal Agent Service",
        description="AI-powered legal document drafting service for Indian legal firms",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
    app.include_router(router, prefix="/api/v1", tags=["drafts"])
    app.include_router(chat_router, prefix="/api/v1", tags=["chat"])
    app.include_router(case_agent_router, prefix="/api/v1", tags=["case-agent"])

    @app.get("/")
    async def root():
        return {"service": "legal-agent-service", "version": "0.1.0", "docs": "/docs"}

    return app


app = create_app()


def run() -> None:
    """Entry point for `uv run legal-agent`."""
    import uvicorn

    settings = get_settings()
    uvicorn.run("legal_agent.main:app", host=settings.service_host, port=settings.service_port, reload=settings.debug)


if __name__ == "__main__":
    run()
