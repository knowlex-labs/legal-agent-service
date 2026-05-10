"""Judgment summary endpoint — POST /api/v1/judgments/summarize."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from legal_agent.judgments.summarizer import generate_summary

logger = logging.getLogger(__name__)

router = APIRouter()


class JudgmentSummarizeRequest(BaseModel):
    text: str = Field(..., min_length=1, description="Raw judgment markdown/text from S3")
    judgment_id: str = Field(..., description="Judgment UUID for logging/tracing")
    model: str | None = Field(None, description="Optional model override")


class JudgmentSummarizeResponse(BaseModel):
    summary: str


@router.post("/summarize", response_model=JudgmentSummarizeResponse)
async def summarize_judgment(request: JudgmentSummarizeRequest) -> JudgmentSummarizeResponse:
    """Generate a structured 10-section legal summary for a judgment.

    Synchronous (no job queue) — the Java service calls this inline and saves
    the result to the DB so subsequent calls return instantly from cache.
    """
    logger.info("POST /judgments/summarize judgment_id=%s text_len=%d", request.judgment_id, len(request.text))
    try:
        summary = await generate_summary(request.text, model=request.model)
    except Exception as exc:
        logger.exception("Judgment summary generation failed: %s", exc)
        raise HTTPException(status_code=502, detail="Summary generation failed")

    return JudgmentSummarizeResponse(summary=summary)
