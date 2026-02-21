"""Request schemas for the case agent API."""

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class ConversationTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class CaseAgentRequest(BaseModel):
    mode: Literal["ask", "edit"]
    message: str = Field(..., min_length=1)
    source_ids: list[str] = []
    active_draft_content: str | None = None
    conversation_history: list[ConversationTurn] = []
    model: Literal["openai", "gemini"] = "openai"

    @model_validator(mode="after")
    def edit_requires_draft_content(self):
        if self.mode == "edit" and not self.active_draft_content:
            raise ValueError("active_draft_content is required when mode is 'edit'")
        return self
