"""Request/response schemas for the research chat API."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from legal_agent.workspace_chat.models import ChatHistoryMessage  # noqa: F401


class ResearchChatMessageRequest(BaseModel):
    message: str = Field(..., min_length=1)
    tone: Literal["formal", "conversational", "neutral"] = "formal"
    style: Literal["precise", "balanced", "detailed"] = "balanced"
    model: str = Field(default="gpt-5-mini-2025-08-07", description="Model ID to use")


class ResearchChatConfigUpdate(BaseModel):
    tone: Literal["formal", "conversational", "neutral"] | None = None
    style: Literal["precise", "balanced", "detailed"] | None = None


class ResearchChatSessionResponse(BaseModel):
    session_id: str
    user_id: str
    tone: str
    style: str
    created_at: datetime


class ResearchChatHistoryResponse(BaseModel):
    session_id: str
    messages: list[ChatHistoryMessage]
