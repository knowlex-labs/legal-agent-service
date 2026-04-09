"""Request/response schemas for the draft chat API."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from legal_agent.chat.models import ChatHistoryMessage


class DraftChatMessageRequest(BaseModel):
    message: str = Field(..., min_length=1)
    tone: Literal["formal", "conversational", "neutral"] = Field(
        default="formal", description="Response tone"
    )
    style: Literal["precise", "balanced", "detailed"] = Field(
        default="balanced", description="Response detail level"
    )
    file_ids: list[str] = Field(default_factory=list, description="File IDs for RAG scope")
    model: Literal["openai", "gemini"] = Field(default="openai", description="LLM provider")


class DraftChatConfigUpdate(BaseModel):
    tone: Literal["formal", "conversational", "neutral"] | None = None
    style: Literal["precise", "balanced", "detailed"] | None = None


class CreateSessionRequest(BaseModel):
    case_folder_id: str = Field(..., min_length=1, description="Case folder this session belongs to")


class DraftChatSessionResponse(BaseModel):
    session_id: str
    case_folder_id: str
    tone: str
    style: str
    created_at: datetime


class DraftChatHistoryResponse(BaseModel):
    session_id: str
    messages: list[ChatHistoryMessage]
