"""Request/response models for case summary."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class DraftContext(BaseModel):
    title: str
    document_type: str
    content: str | None = None


class GenerateSummaryRequest(BaseModel):
    case_folder_id: str
    file_ids: list[str] = Field(default_factory=list, description="File IDs to fetch from RAG engine")
    drafts: list[DraftContext] = Field(default_factory=list, description="Optional generated drafts to include")
    chat_highlights: list[str] = Field(default_factory=list, description="Key conversation points")
    force_regenerate: bool = False
    model: Literal["openai", "gemini"] = "openai"


class CaseSummaryResponse(BaseModel):
    case_folder_id: str
    summary_md: str
    generated_at: datetime
