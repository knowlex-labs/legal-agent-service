"""API request schemas."""

from typing import Any

from pydantic import BaseModel, Field, model_validator

from legal_agent.models.documents import DocumentType


class DraftConfig(BaseModel):
    """Flexible config for document drafting. Fill fields relevant to your document type."""

    party_one_details: str | None = Field(
        None, description="Plaintiff / Sender / First Party — name, age, address, phone, etc."
    )
    party_two_details: str | None = Field(
        None, description="Defendant / Recipient / Second Party details"
    )
    court_details: str | None = Field(
        None, description="Court name, location, case number"
    )
    property_details: str | None = Field(
        None, description="Property or subject matter description"
    )
    advocate_details: str | None = Field(
        None, description="Advocate name, credentials"
    )
    facts: str | None = Field(
        None, description="Chronological facts or scope of work"
    )
    relief_sought: str | None = Field(
        None, description="Relief, demand, or action required"
    )
    terms: str | None = Field(
        None, description="Key terms: duration, payment, notice period, etc."
    )
    special_clauses: str | None = Field(
        None, description="Specific clauses or requirements"
    )
    additional_instructions: str | None = Field(
        None, description="Any other instructions"
    )


class CreateDraftRequest(BaseModel):
    """Request to create a new legal document draft."""

    title: str = Field(..., description="Title of the document to draft", min_length=1)
    body: str | None = Field(
        None,
        description="Detailed instructions for drafting the document",
        min_length=10,
    )
    document_type: DocumentType = Field(..., description="Type of legal document to draft")
    config: DraftConfig | None = Field(
        None, description="Structured config with labeled fields for drafting"
    )
    file_ids: list[str] = Field(
        default_factory=list,
        description="Optional reference document IDs for RAG context",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Optional extra context or parameters"
    )

    @model_validator(mode="after")
    def require_body_or_config(self) -> "CreateDraftRequest":
        if not self.body and not self.config:
            raise ValueError("Either 'body' or 'config' must be provided")
        return self
