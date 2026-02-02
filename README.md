# Legal Agent Service

AI-powered legal document drafting service for Indian legal firms using Pydantic AI and RAG (Retrieval-Augmented Generation).

## Overview

This service provides asynchronous legal document drafting capabilities with support for:
- Contracts and Agreements
- Legal Notices and Demand Notices
- Court Filings (Petitions, Affidavits, Applications)
- RAG integration for context from reference documents
- Multiple LLM providers (OpenAI, Anthropic, Google Gemini)

## Architecture

```
┌─────────────────┐       ┌──────────────────┐       ┌─────────────┐
│  Legal Agent    │  <──> │   RAG Engine     │  <──> │  Vector DB  │
│  Service        │       │  (localhost:8000)│       │             │
│  (port 8001)    │       └──────────────────┘       └─────────────┘
└─────────────────┘
        │
        v
┌─────────────────┐
│  LLM Provider   │
│  (OpenAI/       │
│   Anthropic/    │
│   Gemini)       │
└─────────────────┘
```

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) (Python package manager)
- RAG engine running on `localhost:8000`
- API key for your chosen LLM provider

## Installation

1. Clone the repository:
```bash
cd legal-agent-service
```

2. Install dependencies using uv:
```bash
uv sync
```

3. Create `.env` file from example:
```bash
cp .env.example .env
```

4. Configure your `.env` file:
```env
# LLM Provider Configuration
LLM_PROVIDER=openai
LLM_MODEL=gpt-4o
OPENAI_API_KEY=your-openai-api-key-here

# RAG Engine Configuration
RAG_ENGINE_BASE_URL=http://localhost:8000
RAG_ENGINE_USER_ID=legal-agent-service

# Service Configuration
SERVICE_HOST=0.0.0.0
SERVICE_PORT=8001
DEBUG=false
```

## Running the Service

### Option 1: Using uv run
```bash
uv run legal-agent
```

### Option 2: Activate virtual environment
```bash
source .venv/bin/activate  # On Unix/macOS
# or
.venv\Scripts\activate     # On Windows

python -m legal_agent.main
```

The service will start on `http://localhost:8001`

### Verify Service is Running
```bash
curl http://localhost:8001/api/v1/health
```

Expected response:
```json
{
  "status": "healthy",
  "service": "legal-agent-service"
}
```

## API Usage

### 1. Create a Draft Job

**Endpoint:** `POST /api/v1/drafts`

**Without RAG (Simple):**
```bash
curl -X POST http://localhost:8001/api/v1/drafts \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Employment Agreement",
    "body": "Draft an employment agreement for a software engineer...",
    "document_type": "agreement",
    "file_ids": [],
    "metadata": {}
  }'
```

**With RAG (Using Reference Documents):**
```bash
curl -X POST http://localhost:8001/api/v1/drafts \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Employment Agreement",
    "body": "Draft an employment agreement using company template...",
    "document_type": "agreement",
    "file_ids": ["file-id-from-rag-engine"],
    "metadata": {}
  }'
```

Response:
```json
{
  "job_id": "uuid-here",
  "status": "pending",
  "created_at": "2024-01-15T10:30:00Z"
}
```

### 2. Check Draft Status

**Endpoint:** `GET /api/v1/drafts/{job_id}`

```bash
curl http://localhost:8001/api/v1/drafts/{job_id}
```

Response:
```json
{
  "job_id": "uuid-here",
  "status": "completed",
  "created_at": "2024-01-15T10:30:00Z",
  "completed_at": "2024-01-15T10:31:00Z",
  "result": {
    "draft": "Complete document text...",
    "sections": [...],
    "metadata": {...}
  },
  "error": null
}
```

**Job Statuses:**
- `pending` - Job created, not yet started
- `processing` - Job is being processed
- `completed` - Job finished successfully
- `failed` - Job failed (see error field)

### 3. List All Drafts

**Endpoint:** `GET /api/v1/drafts`

```bash
curl "http://localhost:8001/api/v1/drafts?limit=10&offset=0"
```

### 4. Cancel a Job

**Endpoint:** `DELETE /api/v1/drafts/{job_id}`

```bash
curl -X DELETE http://localhost:8001/api/v1/drafts/{job_id}
```

## Document Types

| Type | Value | Description |
|------|-------|-------------|
| Contract | `contract` | General contracts |
| Agreement | `agreement` | Employment, NDA, etc. |
| Legal Notice | `legal_notice` | General legal notices |
| Demand Notice | `demand_notice` | Payment/recovery notices |
| Petition | `petition` | Court petitions |
| Affidavit | `affidavit` | Sworn statements |
| Application | `application` | Court applications |

## Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `LLM_PROVIDER` | `openai` | LLM provider (openai/anthropic/gemini) |
| `LLM_MODEL` | `gpt-4o` | Model name |
| `OPENAI_API_KEY` | - | OpenAI API key |
| `ANTHROPIC_API_KEY` | - | Anthropic API key |
| `GEMINI_API_KEY` | - | Google Gemini API key |
| `RAG_ENGINE_BASE_URL` | `http://localhost:8000` | RAG engine URL |
| `RAG_ENGINE_USER_ID` | `legal-agent-service` | User ID for RAG requests |
| `SERVICE_HOST` | `0.0.0.0` | Service host |
| `SERVICE_PORT` | `8001` | Service port |
| `DEBUG` | `false` | Debug mode (uses MockRAGClient) |

### Debug Mode

Enable debug mode for development:
```env
DEBUG=true
```

This will:
- Use MockRAGClient instead of real RAG engine
- Enable verbose logging
- Enable hot reload

## RAG Integration

The service integrates with a RAG engine for retrieving context from indexed documents.

### How It Works

1. Documents are uploaded and indexed in the RAG engine
2. RAG engine returns `file_id` for each indexed document
3. When creating a draft, provide `file_ids` in the request
4. The agent queries RAG for relevant context using `/api/v1/retrieve`
5. Retrieved chunks are included in the LLM context for drafting

### RAG Request Flow

```
Legal Agent → POST /api/v1/retrieve
Headers: X-User-Id: legal-agent-service
Body: {
  "query": "employment agreement termination clauses",
  "filters": {
    "file_ids": ["file-123"],
    "content_type": "legal"
  },
  "top_k": 10
}

RAG Engine → Returns enriched chunks with:
- chunk_text
- relevance_score
- page_number
- concepts
```

## Testing with Postman

Import the included `postman_collection.json` to test the API:

1. Open Postman
2. Import `postman_collection.json`
3. Set variables:
   - `base_url`: `http://localhost:8001`
   - `rag_base_url`: `http://localhost:8000`
   - `file_id`: Your test file ID from RAG engine

The collection includes:
- Health check
- Create drafts (all document types)
- Get draft status
- List drafts
- Cancel draft
- RAG integration tests

## Logging

Logs are output to stdout with format:
```
%(asctime)s - %(name)s - %(levelname)s - %(message)s
```

**Log Levels:**
- `INFO`: Service lifecycle, job creation/completion, RAG responses
- `DEBUG`: Detailed execution flow, RAG requests, agent steps
- `WARNING`: RAG failures, timeouts
- `ERROR`: Job failures, HTTP errors

**Example logs:**
```
2024-01-15 10:30:00 - legal_agent.main - INFO - Starting legal-agent-service...
2024-01-15 10:30:01 - legal_agent.services.draft_service - INFO - Created draft job abc-123: type=agreement
2024-01-15 10:30:02 - legal_agent.clients.rag_client - INFO - RAG returned 5 chunks
2024-01-15 10:30:15 - legal_agent.services.job_manager - INFO - [abc-123] Job completed successfully
```

## Project Structure

```
legal-agent-service/
├── src/legal_agent/
│   ├── agents/           # Specialized drafting agents
│   │   ├── base.py       # Base agent with RAG tool
│   │   ├── contract_agent.py
│   │   ├── notice_agent.py
│   │   └── court_filing_agent.py
│   ├── api/              # FastAPI routes
│   │   └── routes.py
│   ├── clients/          # External service clients
│   │   └── rag_client.py # RAG engine HTTP client
│   ├── models/           # Pydantic models
│   │   ├── documents.py
│   │   ├── requests.py
│   │   ├── responses.py
│   │   └── rag_models.py
│   ├── services/         # Business logic
│   │   ├── draft_service.py
│   │   └── job_manager.py
│   ├── config.py         # Configuration
│   └── main.py           # Application entry point
├── tests/                # Test files
├── .env.example          # Environment template
├── pyproject.toml        # Project dependencies
├── uv.lock              # Locked dependencies
└── README.md            # This file
```

## Development

### Adding a New Document Type

1. Add to `DocumentType` enum in `models/documents.py`
2. Create or update agent in `agents/`
3. Register in `DraftService._agents` mapping

### Running Tests

```bash
pytest tests/
```

### Code Formatting

```bash
ruff check src/
ruff format src/
```

## Troubleshooting

### Service won't start
- Check if port 8001 is available
- Verify `.env` file exists with required variables
- Ensure LLM API key is valid

### RAG integration failing
- Verify RAG engine is running on port 8000
- Check `RAG_ENGINE_BASE_URL` in `.env`
- Enable `DEBUG=true` to see detailed logs
- Test RAG endpoint directly: `curl -H "X-User-Id: test" http://localhost:8000/api/v1/health`

### Jobs stuck in "pending"
- Check logs for errors
- Verify LLM API key is valid and has quota
- Try with `DEBUG=true` to use MockRAGClient

### No context from RAG
- Verify file IDs are correct and indexed
- Check RAG engine logs
- Test RAG retrieve endpoint directly (see Postman collection)

## API Documentation

Interactive API docs available at:
- Swagger UI: `http://localhost:8001/docs`
- ReDoc: `http://localhost:8001/redoc`

## License

[Your License Here]

## Support

For issues or questions, contact [Your Contact Info]
