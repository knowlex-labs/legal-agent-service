"""FastAPI application entry point."""

import asyncio
import logging
import logging.config
import os
from contextlib import asynccontextmanager
from pathlib import Path

import yaml
from fastapi import FastAPI


def _setup_logging() -> None:
    """Load logging configuration from YAML and apply DEBUG override if set."""
    config_path = Path(__file__).parent / "logging_config.yaml"
    with open(config_path) as f:
        config = yaml.safe_load(f)

    if os.getenv("DEBUG", "").lower() == "true":
        config["root"]["level"] = "DEBUG"
        config["loggers"]["legal_agent"]["level"] = "DEBUG"

    logging.config.dictConfig(config)


_setup_logging()
logger = logging.getLogger(__name__)

from fastapi.middleware.cors import CORSMiddleware

from legal_agent.middleware import RequestContextMiddleware

from legal_agent.api.routes import router, set_services
from legal_agent.causelist.routes import causelist_router
from legal_agent.clients.rag_client import HTTPRAGClient, LocalRAGClient, MockRAGClient
from legal_agent.clients.s3_client import S3Client
from legal_agent.config import get_settings
from legal_agent.legal_retrieval import LegalCaseRetriever
from legal_agent.legal_retrieval.config import get_legal_db_url
from legal_agent.legal_retrieval.db import close_pool
from legal_agent.services.draft_service import DraftService
from legal_agent.services.job_manager import JobManager
from legal_agent.summary.generator import SummaryGenerator
from legal_agent.summary.service import SummaryService
from legal_agent.workspace_chat.agent import WorkspaceChatAgent
from legal_agent.workspace_chat.routes import set_workspace_chat_agent, workspace_chat_router


def _setup_llm_environment() -> None:
    settings = get_settings()
    env_keys = {
        "OPENAI_API_KEY": settings.openai_api_key,
        "ANTHROPIC_API_KEY": settings.anthropic_api_key,
        "GEMINI_API_KEY": settings.gemini_api_key,
    }
    for var, val in env_keys.items():
        if val:
            os.environ.setdefault(var, val)


_setup_llm_environment()

job_manager: JobManager | None = None
rag_client: LocalRAGClient | HTTPRAGClient | MockRAGClient | None = None
workspace_chat_agent: WorkspaceChatAgent | None = None


async def _init_workspace_chat_agent():
    """Initialize workspace chat agent in background."""
    global workspace_chat_agent
    try:
        workspace_chat_agent = WorkspaceChatAgent()
        await workspace_chat_agent.initialize(get_legal_db_url(), rag_client, None)
        set_workspace_chat_agent(workspace_chat_agent)
        logger.info("Workspace chat agent fully initialized")
    except Exception:
        logger.exception("Failed to initialize workspace chat agent")


@asynccontextmanager
async def lifespan(app: FastAPI):
    global job_manager, rag_client

    settings = get_settings()
    logger.info(f"Starting legal-agent-service (draft={settings.llm_model}, chat_default={settings.chat_llm_default_model})")

    job_manager = JobManager()
    if settings.rag_in_process:
        rag_client = MockRAGClient() if settings.debug else LocalRAGClient()
        logger.info("RAG mode: in-process (LocalRAGClient)")
    else:
        rag_client = HTTPRAGClient(settings)
        logger.info(f"RAG mode: external HTTP → {settings.rag_engine_base_url}")
        _rag_url = settings.rag_engine_base_url.lower()
        if "localhost" in _rag_url or "127.0.0.1" in _rag_url:
            logger.warning(
                "rag_in_process=false but RAG_ENGINE_BASE_URL looks local; set a reachable URL in production"
            )

    legal_retriever: LegalCaseRetriever | None = None
    try:
        legal_retriever = LegalCaseRetriever()
        logger.info("LegalCaseRetriever created (DB pool lazy-initialized on first query)")
    except Exception:
        logger.warning("LegalCaseRetriever unavailable — drafts will not have case law tool")

    s3_client = S3Client(settings)
    draft_service = DraftService(
        settings=settings,
        job_manager=job_manager,
        rag_client=rag_client,
        s3_client=s3_client,
        retriever=legal_retriever,
    )
    summary_service = SummaryService(
        generator=SummaryGenerator(rag_client), job_manager=job_manager, s3_client=s3_client
    )
    set_services(draft_service, summary_service, job_manager, s3_client)

    workspace_chat_init_task = asyncio.create_task(_init_workspace_chat_agent())

    logger.info("Service ready")
    yield

    logger.info("Shutting down...")
    if not workspace_chat_init_task.done():
        workspace_chat_init_task.cancel()
    if workspace_chat_agent:
        await workspace_chat_agent.close()
    close_pool()
    if job_manager:
        await job_manager.cleanup()
    if rag_client and hasattr(rag_client, "close"):
        await rag_client.close()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Legal Agent Service",
        description="AI-powered legal document drafting service for Indian legal firms",
        version="0.1.0",
        lifespan=lifespan,
    )
    origins = settings.cors_allowed_origins if not settings.debug else ["*"]
    app.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
    app.add_middleware(RequestContextMiddleware)
    app.include_router(router, prefix="/api/v1", tags=["jobs"])
    app.include_router(workspace_chat_router, prefix="/api/v1", tags=["workspace-chat"])
    app.include_router(causelist_router, prefix="/api/v1", tags=["causelist"])
    if settings.rag_in_process:
        from legal_agent.rag_engine.api.routes.collections import router as rag_collections_router

        app.include_router(rag_collections_router, prefix="/api/v1/collections", tags=["rag"])

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
