"""API request schemas."""

from typing import Any

from pydantic import BaseModel, Field

from legal_agent.models.documents import DocumentType


class CreateDraftRequest(BaseModel):
    """Request to create a new legal document draft."""

    title: str = Field(..., description="Title of the document to draft", min_length=1)
    body: str = Field(
        ...,
        description="Detailed instructions for drafting the document",
        min_length=10,
    )
    document_type: DocumentType = Field(..., description="Type of legal document to draft")
    file_ids: list[str] = Field(
        default_factory=list,
        description="Optional reference document IDs for RAG context",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Optional extra context or parameters"
    )
