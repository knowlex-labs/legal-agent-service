"""Request/response schemas for the chat API."""

from typing import Literal

from pydantic import BaseModel, Field


class ChatMessageRequest(BaseModel):
    message: str = Field(..., min_length=1)
    enable_kb: bool = Field(default=True, description="Use legal knowledge base for answers")
    enable_web: bool = Field(default=False, description="Enable web search (not yet implemented)")
    model: Literal["openai", "gemini"] = Field(default="openai", description="LLM provider to use")
    style: Literal["precise", "balanced", "detailed"] = Field(default="balanced", description="Answer style")


class ChatSessionResponse(BaseModel):
    session_id: str


class ChatSessionSummary(BaseModel):
    session_id: str
    last_checkpoint_id: str


class ChatSessionListResponse(BaseModel):
    sessions: list[ChatSessionSummary]


class ToolCallRecord(BaseModel):
    name: str
    args: dict = {}
    result: str | None = None


class ChatHistoryMessage(BaseModel):
    role: Literal["human", "ai"]
    content: str
    tool_calls: list[ToolCallRecord] | None = None


class ChatHistoryResponse(BaseModel):
    session_id: str
    messages: list[ChatHistoryMessage]
