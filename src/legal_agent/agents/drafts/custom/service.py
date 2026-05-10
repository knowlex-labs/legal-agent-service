"""TemplateService — orchestrates template creation, retrieval, and deletion."""

import asyncio
import logging
import os

from fastapi import HTTPException

from legal_agent.agents.drafts.custom import db
from legal_agent.agents.drafts.custom.extractor import ExtractionError, extract_text_from_bytes
from legal_agent.agents.drafts.custom.models import CreateTemplateRequest, TemplateResponse
from legal_agent.agents.drafts.custom.prompt_generator import generate_template_prompt
from legal_agent.clients.s3_client import S3Client
from legal_agent.config import Settings

logger = logging.getLogger(__name__)


def _fast_model_string(settings: Settings) -> str:
    """Return 'provider:model' for the fast/cheap model matching the configured provider."""
    fast_models = {
        "openai": "gpt-4o-mini",
        "anthropic": "claude-3-5-haiku-latest",
        "gemini": "gemini-2.0-flash",
    }
    # Fall back to the configured model so we never produce an invalid cross-provider string
    model = fast_models.get(settings.draft_llm_provider, settings.draft_llm_model)
    return f"{settings.draft_llm_provider}:{model}"


def _filename_from_s3_path(s3_path: str) -> str:
    return os.path.basename(s3_path)


def _row_to_response(row: dict) -> TemplateResponse:
    return TemplateResponse(
        id=str(row["id"]),
        user_id=row["user_id"],
        name=row["name"],
        document_type=row["document_type"],
        s3_path=row.get("s3_path"),
        generated_prompt=row["generated_prompt"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


class TemplateService:
    def __init__(self, s3_client: S3Client, settings: Settings):
        self._s3 = s3_client
        self._settings = settings

    async def create_template(
        self, request: CreateTemplateRequest, user_id: str
    ) -> TemplateResponse:
        """Download file (if s3_path), extract text, generate prompt, save to DB."""

        # Step 1 — get raw bytes or content
        if request.s3_path:
            filename = _filename_from_s3_path(request.s3_path)
            logger.info(f"Downloading template from S3: {request.s3_path}")
            data = await self._s3.download_bytes(request.s3_path)
        else:
            # raw text provided directly
            filename = f"{request.name}.txt"
            data = (request.content or "").encode("utf-8")

        # Step 2 — extract text
        try:
            raw_text = extract_text_from_bytes(data, filename)
        except ExtractionError as exc:
            raise HTTPException(status_code=422, detail=str(exc))

        logger.info(f"Extracted {len(raw_text)} chars from '{filename}'")

        # Step 3 — generate drafting prompt via LLM
        model = _fast_model_string(self._settings)
        generated_prompt = await generate_template_prompt(raw_text, model)

        # Step 4 — persist (run sync DB call off the event loop)
        row = await asyncio.to_thread(
            db.insert_template,
            user_id=user_id,
            name=request.name,
            document_type=request.document_type,
            s3_path=request.s3_path,
            raw_text=raw_text,
            generated_prompt=generated_prompt,
        )
        logger.info(f"Template saved: id={row['id']} user={user_id} name='{request.name}'")
        return _row_to_response(row)

    async def get_template(self, template_id: str, user_id: str) -> TemplateResponse:
        row = await asyncio.to_thread(db.get_template, template_id, user_id)
        if not row:
            raise HTTPException(status_code=404, detail=f"Template '{template_id}' not found")
        return _row_to_response(row)

    async def list_templates(self, user_id: str) -> list[TemplateResponse]:
        rows = await asyncio.to_thread(db.list_templates, user_id)
        return [_row_to_response(r) for r in rows]

    async def delete_template(self, template_id: str, user_id: str) -> None:
        deleted = await asyncio.to_thread(db.delete_template, template_id, user_id)
        if not deleted:
            raise HTTPException(status_code=404, detail=f"Template '{template_id}' not found")
