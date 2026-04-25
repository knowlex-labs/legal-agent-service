# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

AI-powered legal document drafting service for Indian legal firms. Uses LangGraph agents with RAG to produce contracts, notices, court filings, bail applications, criminal appeals, and 14+ other document types. Supports multi-LLM (OpenAI, Anthropic, Gemini). This is the Python service in a two-service backend — the Java platform API (`../knowlex-platform-api/`, port 8080) calls this service (port 8001), saves results to PostgreSQL, and handles JWT auth. This service itself uses a simple `X-User-Id` header and is not exposed directly to end users.

## Commands

- **Install deps:** `uv sync`
- **Run service:** `uv run legal-agent` (port 8001)
- **Tests:** `pytest tests/` / single: `pytest tests/test_file.py::test_name`
- **Lint/format:** `ruff check src/` / `ruff format src/`
- **Health:** `curl http://localhost:8001/api/v1/health`
- **Model comparison test:** `python test_docs/compare_models.py` (fires draft jobs across multiple models for quality comparison — see `test_docs/` for Postman collections too)

## Architecture

### Request flow

```
Platform API (Java) → POST /api/v1/jobs (this service)
                    → JobManager creates job (returns job_id immediately, async)
                    → Draft/Translation/Summary service routes to appropriate agent
                    → Agent runs LangGraph state machine (RAG tools + LLM)
                    → Result uploaded to S3
                    → Job status updated (polled via GET /api/v1/jobs/{job_id})
```

### Key modules

- **`agents/drafts/`** — 16 LangGraph agents, one per document type (bail, anticipatory bail, quashing petition, revision petition, SLP, criminal appeal, written statement, written arguments, execution petition, consumer complaint, patent, contract, notice, court filing, application, + generic). All extend `base.py::BaseDraftingAgent` which builds a state graph: `agent_node` (LLM with tool calls) → `tools` (RAG/legal search) → `output_node` (extracts `GeneratedDocument`). Also houses `drafts/custom/` and `drafts/templates/`. **Important**: `output_node` captures the raw markdown from the agent's last message (preserving all formatting) and only uses structured output for metadata — see `base.py` for the rationale.
- **`agents/translation/`** — Document translation pipeline. `service.py` orchestrates: extraction → LLM translation → PDF rendering. OCR for scanned PDFs is pluggable (Gemini Vision or Sarvam; see `utils/ocr.py`). WeasyPrint for PDF output (falls back to fpdf2), with `html_builder.py` providing script-aware CSS for 22 Indian languages.
- **`services/draft_service.py`** — Routes to the right agent. Supports **per-request model override** (`request.model` field) via `_agent_classes` mapping + `_build_agent()`. Without override, uses the default-model instances in `_agents`.
- **`services/job_manager.py`** — In-memory async job queue. All jobs (draft/translation/summary/synopsis) flow through this.
- **`services/content_preprocessor.py`** — Pre-processes uploaded content before agents consume it (chunk budgeting, text extraction normalisation).
- **`utils/ocr.py`** — Shared OCR utility with dual backends: Gemini Vision (default) and Sarvam (better for Indic scripts; chunks >10-page PDFs via `SARVAM_OCR_CONCURRENCY`). Content-hashed S3 cache (`OCR_CACHE_ENABLED`, `OCR_CACHE_PREFIX`) avoids re-OCRing the same file. Used by both the translation extractor and `rag_engine/parsers/pdf_parser.py`. Returns structured markdown or plain text based on `output_format` arg.
- **`utils/legal_postprocess.py`** — Post-processing pass over generated legal drafts (citation normalisation, structure fixes).
- **`clients/`** — HTTP clients: `rag_client.py` (LocalRAGClient vs HTTPRAGClient based on `RAG_IN_PROCESS`), `s3_client.py`, `decryption.py` (AES-256-GCM for encrypted S3 files).
- **`rag_engine/`** — In-process RAG stack (Qdrant + embeddings + parsers + reranker). Only loaded when `RAG_IN_PROCESS=true`. The in-process mode adds ~2.5GB memory footprint — see `legal_retrieval/` for the lighter case-law-only alternative.
- **`legal_retrieval/`** — PostgreSQL + pgvector hybrid search over Indian case law. Independent of `rag_engine/`. **Schema note**: `judgment_paragraphs.judgment_id` is the FK to `judgments.id` (not `case_id`) — see `db.py` for the hybrid RRF query.
- **`workspace_chat/`** — Conversational agent over workspace docs. Uses `chat_llm_default_model` (Gemini by default) with per-request model override. Does not go through `JobManager` — streams via SSE.
- **`chat/`** — Chat-side utilities shared across conversational flows: `citation_utils.py`, `session_title.py`, `web_search.py` (Serper), and Firecrawl-based legal web search (`legal_web_search_firecrawl.py`, `firecrawl_verify.py`).
- **`summary/`, `synopsis/`** — Document summarization/synopsis (supports multi-document input). Output markdown to S3, same async job pattern.
- **`precedents/`** — Precedent extraction/generation over case-law retrieval.
- **`causelist/`** — Court cause list scraping via Camoufox (anti-detect Firefox). **Memory-heavy**: launches a fresh browser per request, consuming 400-800MB. A single instance running this + Playwright + torch needs ~2-4GB in production.
- **`prompts/`** — Centralized prompt templates. Per-agent prompts live in each agent file (e.g. `bail_agent.py`).
- **`data/`** — Few-shot examples loaded at draft time via `services/examples_loader.py`.

### LLM configuration

- **Draft model**: `LLM_PROVIDER` + `LLM_MODEL` in `.env` (`.env.example` ships OpenAI flagship `gpt-5.4`). Applied at service init; every agent gets the same model.
- **Per-request override**: `CreateDraftJobRequest.model` lets callers override per job. The provider is inferred from the model prefix (`gemini-*` → google-genai, `gpt-*`/`o*` → openai, `claude-*` → anthropic).
- **Max output tokens**: `base.py::_PROVIDER_MAX_TOKENS` — 16384 for OpenAI and Google, 8192 for Anthropic.
- **Workspace chat default**: `chat_llm_default_model` (separate from drafting).
- **Translation**: `generator.py::_MODEL_ALIASES` maps `"gemini"`/`"claude"`/`"openai"` to specific model IDs — requests can send either an alias or a full model name.

### RAG modes

- **In-process** (`RAG_IN_PROCESS=true`, default): Qdrant + embeddings + reranker all in this service. Use for dev or if you don't have a separate RAG service.
- **Remote** (`RAG_IN_PROCESS=false`): Delegates to `RAG_ENGINE_BASE_URL`. Use in low-memory deployments.

### Embedding configs — two independent systems

Embedding configs are **split per data source** because each uses a different provider/dimension and changing one must not break the other:

| System | Data | Storage | Env vars | Default |
|---|---|---|---|---|
| **Workspace RAG** (`rag_engine/`) | User-uploaded docs | Qdrant | `WORKSPACE_EMBEDDING_PROVIDER/MODEL`, `WORKSPACE_VECTOR_SIZE` | falls back to `EMBEDDING_*` legacy vars |
| **Legal retrieval** (`legal_retrieval/`) | SC judgments | Postgres+pgvector `halfvec(3072)` | `LEGAL_EMBEDDING_PROVIDER/MODEL`, `LEGAL_VECTOR_SIZE` | Gemini `gemini-embedding-2-preview` @ 3072 |

**Key rule**: `legal_retrieval/embeddings.py` uses only `legal_*` config and calls Gemini/OpenAI/HF directly — it does NOT go through `rag_engine/utils/embedding_client.py`. That client is reserved for workspace docs. This isolation means you can switch workspace docs to BGE without breaking case law queries.

**Adding a new indexed data source** (e.g. MP High Court judgments): add `mphc_embedding_*` vars and a dedicated embedding helper that uses them. Do not reuse an existing system's config.

### Adding a new document type

1. Add to `DocumentType` enum in `models/documents.py`
2. Create agent in `agents/drafts/` extending `BaseDraftingAgent` — provide a `system_prompt` and (optionally) override `_build_graph()` for custom tool wiring
3. Register in both `DraftService._agent_classes` and `DraftService._agents` (constructor in `draft_service.py`)
4. Add few-shot examples to `data/examples/` if desired

## Configuration

Copy `.env.example` to `.env`. Key vars:
- `LLM_PROVIDER` / `LLM_MODEL` — Default LLM for drafting
- `CHAT_LLM_DEFAULT_MODEL` — Default for workspace chat
- `GEMINI_API_KEY` — Used for Vision OCR (when `OCR_PROVIDER=gemini`) + translation regardless of LLM_PROVIDER
- `OCR_PROVIDER` — `gemini` (default) or `sarvam`. Sarvam caps at ≤10 pages/job; long PDFs are chunked (`SARVAM_OCR_CONCURRENCY`, `SARVAM_OCR_LANGUAGE`)
- `OCR_CACHE_ENABLED` / `OCR_CACHE_PREFIX` — S3-backed content-hashed OCR cache; disable only for debugging
- `SARVAM_API_KEY`, `SARVAM_CHAT_MODEL`, `SARVAM_API_BASE_URL` — Sarvam OCR + OpenAI-compatible chat
- `SERPER_API_KEY`, `FIRECRAWL_API_KEY` — web search + legal web research tools (chat flows)
- `RAG_IN_PROCESS` — Toggle in-process RAG
- `QDRANT_HOST` / `QDRANT_PORT`, `EMBEDDING_PROVIDER` / `EMBEDDING_MODEL` — RAG stack
- `LEGAL_DB_URL` — Postgres for `legal_retrieval/` case law search
- `S3_*` — AWS S3 for document storage
- `DOCUMENT_ENCRYPTION_MASTER_KEY` — Needed for decrypting user-uploaded files in the translation flow
- `DEBUG=true` — MockRAGClient + verbose logging + hot reload

## Style

- Python 3.11+ (dev targets 3.12), ruff line-length 100
- Pydantic v2 everywhere
- Async throughout (FastAPI + async agents + `asyncio.to_thread` for sync libs like PyMuPDF)
- `pytest-asyncio` with `asyncio_mode = "auto"`
- Tool results and LangGraph state: keep messages list append-only (`add_messages` annotation)

## Testing drafts

The `test_docs/` folder contains:
- `postman_draft_tests.json` — hits this service directly (`localhost:8001`)
- `postman_platform_draft_tests.json` — hits the platform API (`localhost:8080`) so drafts are saved to DB
- `compare_models.py` — Python script for side-by-side quality comparison across models; writes `model_comparison_report.md`
