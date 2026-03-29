# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

AI-powered legal document drafting service for Indian legal firms. Uses LangGraph agents with RAG (Retrieval-Augmented Generation) to produce contracts, notices, court filings, bail applications, and criminal appeals. Multi-LLM support (OpenAI, Anthropic, Gemini).

## Commands

- **Install dependencies:** `uv sync`
- **Run service:** `uv run legal-agent` (starts on port 8001)
- **Run tests:** `pytest tests/`
- **Run single test:** `pytest tests/test_file.py::test_name`
- **Lint:** `ruff check src/`
- **Format:** `ruff format src/`
- **Health check:** `curl http://localhost:8001/api/v1/health`

## Architecture

### 5-Layer Flow

```
API Routes (src/legal_agent/api/) → Services (services/) → Agents (agents/) → RAG/DB → External LLMs
```

### Key Modules

- **`agents/`** — LangGraph state-graph agents per document type. Each extends `base.py` which provides RAG tooling. Agents: `contract_agent`, `notice_agent`, `court_filing_agent`, `bail_agent`, `criminal_appeal_agent`.
- **`services/draft_service.py`** — Orchestrates draft creation. Routes to the correct agent based on `document_type`. Runs jobs async via `job_manager.py`.
- **`services/job_manager.py`** — In-memory async job queue. Clients submit a draft request, get a `job_id`, then poll for results.
- **`clients/`** — HTTP clients for external services: `rag_client.py` (RAG engine), `s3_client.py` (S3 storage), `gcs_client.py` (GCS), `google_search_client.py`.
- **`rag_engine/`** — Optional in-process RAG stack (toggle via `RAG_IN_PROCESS=true`). Includes parsers, Qdrant vector store, chunking, reranking, and its own API layer. When disabled, delegates to a remote RAG service at `RAG_ENGINE_BASE_URL`.
- **`legal_retrieval/`** — Case law retrieval pipeline: statute extraction → judgment filtering → pgvector semantic search → cross-encoder reranking. Uses PostgreSQL with pgvector.
- **`chat/`** — Shared chat models and web search tool.
- **`workspace_chat/`** — Conversational agent for workspace-level Q&A with session persistence.
- **`draft_chat/`** — Conversational agent for iterating on existing drafts.
- **`research_chat/`** — Research-oriented conversational agent.
- **`summary/`** — Document summarization service.
- **`case_agent/`** — Case management from cause lists.
- **`models/`** — Pydantic request/response models. `documents.py` has the `DocumentType` enum.
- **`config.py`** — Pydantic Settings configuration loaded from `.env`.
- **`main.py`** — FastAPI app entry point, registers routers and middleware.

### Adding a New Document Type

1. Add to `DocumentType` enum in `models/documents.py`
2. Create agent in `agents/` extending `base.py`
3. Register in `DraftService._agents` mapping

### RAG Modes

- **In-process** (`RAG_IN_PROCESS=true`): Full RAG stack runs inside this service (Qdrant, embeddings, parsers, reranker)
- **Remote** (`RAG_IN_PROCESS=false`): Delegates to external RAG service at `RAG_ENGINE_BASE_URL`

### Key Patterns

- **Async job queue**: Draft requests return a `job_id` immediately; clients poll `GET /api/v1/drafts/{job_id}` for results
- **Multi-provider LLM abstraction**: LLM provider/model configured via env vars, agents are provider-agnostic
- **Dependency injection**: FastAPI `Depends()` for service/client wiring
- **Structured logging**: YAML-configured via `logging_config.yaml`
- **LangGraph state graphs**: Each agent defines nodes and edges as a compiled state graph

## Configuration

Copy `.env.example` to `.env`. Key variables:
- `LLM_PROVIDER` / `LLM_MODEL` — Which LLM to use for drafting
- `RAG_IN_PROCESS` — Whether to run RAG stack in-process
- `QDRANT_HOST` / `QDRANT_PORT` — Vector DB connection
- `EMBEDDING_PROVIDER` / `EMBEDDING_MODEL` — Embedding model config
- `DEBUG=true` — Uses MockRAGClient, enables verbose logging and hot reload

## Style

- Python 3.11+, line length 100 (`ruff`)
- Pydantic v2 for all models
- Async throughout (FastAPI + async agents)
- `pytest-asyncio` with `asyncio_mode = "auto"`
