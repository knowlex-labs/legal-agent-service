"""Request/response schemas for the workspace chat API."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class ToolCallRecord(BaseModel):
    name: str
    args: dict = {}
    result: str | None = None


class ChatHistoryMessage(BaseModel):
    role: Literal["human", "ai"]
    content: str
    tool_calls: list[ToolCallRecord] | None = None


class WorkspaceChatMessageRequest(BaseModel):
    message: str = Field(..., min_length=1)
    tone: Literal["formal", "conversational", "neutral"] = Field(
        default="formal", description="Response tone"
    )
    style: Literal["precise", "balanced", "detailed"] = Field(
        default="balanced", description="Response detail level"
    )
    file_ids: list[str] = Field(default_factory=list, description="File IDs for RAG scope")
    model: str = Field(default="gpt-5-mini-2025-08-07", description="Model ID to use")


class WorkspaceChatConfigUpdate(BaseModel):
    tone: Literal["formal", "conversational", "neutral"] | None = None
    style: Literal["precise", "balanced", "detailed"] | None = None


class CreateSessionRequest(BaseModel):
    case_folder_id: str = Field(..., min_length=1, description="Case folder this session belongs to")


class WorkspaceChatSessionResponse(BaseModel):
    session_id: str
    case_folder_id: str
    tone: str
    style: str
    created_at: datetime


class WorkspaceChatHistoryResponse(BaseModel):
    session_id: str
    messages: list[ChatHistoryMessage]
