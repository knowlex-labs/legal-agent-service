"""Request/response schemas for the chat API."""

from pydantic import BaseModel, Field


class ChatMessageRequest(BaseModel):
    message: str = Field(..., min_length=1)
    enable_kb: bool = Field(default=True, description="Use legal knowledge base for answers")
    enable_web: bool = Field(default=False, description="Enable web search (not yet implemented)")


class ChatSessionResponse(BaseModel):
    session_id: str


class ChatHistoryMessage(BaseModel):
    role: str  # "human", "ai", "tool"
    content: str


class ChatHistoryResponse(BaseModel):
    session_id: str
    messages: list[ChatHistoryMessage]
