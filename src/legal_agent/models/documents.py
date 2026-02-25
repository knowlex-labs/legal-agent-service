"""Document type definitions and schemas."""

from enum import Enum

from pydantic import BaseModel, Field


class Language(str, Enum):
    """Language options for document drafting."""

    ENGLISH = "english"
    HINDI = "hindi"
    BILINGUAL = "bilingual"


class DocumentType(str, Enum):
    """Types of legal documents that can be drafted."""

    CONTRACT = "contract"
    AGREEMENT = "agreement"
    LEGAL_NOTICE = "legal_notice"
    DEMAND_NOTICE = "demand_notice"
    PETITION = "petition"
    AFFIDAVIT = "affidavit"
    APPLICATION = "application"
    BAIL_APPLICATION = "bail_application"
    CRIMINAL_APPEAL = "criminal_appeal"


class DocumentSection(BaseModel):
    """A section within a legal document."""

    title: str = Field(..., description="Section title")
    content: str = Field(..., description="Section content")
    order: int = Field(..., description="Section order in the document")


class GeneratedDocument(BaseModel):
    """Schema for a generated legal document."""

    title: str = Field(..., description="Document title")
    document_type: DocumentType = Field(..., description="Type of document")
    draft: str = Field(..., description="Full document text")
    sections: list[DocumentSection] = Field(
        default_factory=list, description="Structured sections if applicable"
    )
    summary: str | None = Field(None, description="Brief summary of the document")
