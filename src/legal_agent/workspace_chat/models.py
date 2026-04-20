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
    model: str = Field(default="gemini-2.0-flash", description="Model ID to use")
    web_search: bool = Field(
        default=False,
        description=(
            "When True, the agent may call legal_case_search (internal SC judgment DB) "
            "and legal_web_search (Firecrawl → LiveLaw / SCC Online / Bar and Bench). "
            "When False (default), the agent is limited to the user's uploaded case "
            "documents via query_case_documents only."
        ),
    )


class WorkspaceChatConfigUpdate(BaseModel):
    name: str | None = Field(None, max_length=255, description="Session name")
    tone: Literal["formal", "conversational", "neutral"] | None = None
    style: Literal["precise", "balanced", "detailed"] | None = None


class CreateSessionRequest(BaseModel):
    case_folder_id: str = Field(..., min_length=1, description="Case folder this session belongs to")
    name: str | None = Field(None, max_length=255, description="Optional session name")


class WorkspaceChatSessionResponse(BaseModel):
    session_id: str
    case_folder_id: str
    name: str | None = None
    tone: str
    style: str
    created_at: datetime


class WorkspaceChatHistoryResponse(BaseModel):
    session_id: str
    messages: list[ChatHistoryMessage]
