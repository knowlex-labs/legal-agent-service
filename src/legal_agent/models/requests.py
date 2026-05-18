"""API request schemas."""

import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from legal_agent.models.documents import DocumentType, Language, TranslationLanguage
from legal_agent.summary.models import DraftContext


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
    # Bail / Criminal appeal specific fields
    criminal_history: str | None = Field(
        None, description="Accused's criminal history / prior cases"
    )
    bail_history: str | None = Field(
        None, description="Prior bail applications and their outcomes"
    )
    impugned_order: str | None = Field(
        None, description="Details of the impugned/challenged order"
    )
    fir_details: str | None = Field(
        None, description="FIR number, date, police station, sections invoked"
    )
    co_accused_details: str | None = Field(
        None, description="Details of co-accused persons and their bail status"
    )
    applicant: str | None = Field(
        None, description="Applicant name (bail / anticipatory bail / generic application)"
    )
    opposite_party: str | None = Field(
        None, description="Opposite party / complainant / state in criminal templates"
    )
    appellant: str | None = Field(
        None, description="Appellant name (criminal / civil appeal)"
    )
    respondent: str | None = Field(
        None, description="Respondent name (writ / appeal / SLP)"
    )
    petitioner: str | None = Field(
        None, description="Petitioner name (writ / SLP / quashing)"
    )
    grounds: str | None = Field(
        None, description="Legal grounds being argued (writ / appeal / revision)"
    )
    writ_type: str | None = Field(
        None, description="Specific writ being invoked (writ petition only)"
    )
    impugned_judgment: str | None = Field(
        None, description="Details of the judgment being challenged (criminal appeal / SLP)"
    )


class CreateDraftJobRequest(BaseModel):
    """Request to create a new legal document draft job."""

    type: Literal["draft"]
    case_folder_id: str = Field(..., description="Case folder identifier")
    title: str = Field(..., description="Title of the document to draft", min_length=1)

    @field_validator("case_folder_id")
    @classmethod
    def validate_case_folder_id(cls, v: str) -> str:
        if not re.match(r"^[a-zA-Z0-9_-]+$", v):
            raise ValueError("case_folder_id must contain only alphanumeric characters, underscores, or dashes")
        return v

    document_type: DocumentType = Field(..., description="Type of legal document to draft")
    language: Language = Field(
        Language.ENGLISH,
        description="Language for the document: english, hindi, or bilingual",
    )
    body: str | None = Field(
        None,
        description="Detailed instructions for drafting the document",
        min_length=10,
    )
    config: DraftConfig | None = Field(
        None, description="Structured config with labeled fields for drafting"
    )
    file_ids: list[str] = Field(
        default_factory=list,
        description="Optional reference document IDs for RAG context",
    )
    template_id: str | None = Field(
        None,
        description="Custom template ID (from /api/v1/drafts/templates). "
                    "When set, drafts using the user's stored template prompt instead of the system agent.",
    )
    model: str | None = Field(
        None,
        description="Override LLM for this draft (e.g. 'gemini-3-flash', 'gpt-4o', 'gpt-5.4-mini'). "
                    "If omitted, uses the service default.",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Optional extra context or parameters"
    )

    @model_validator(mode="after")
    def require_body_or_config(self) -> "CreateDraftJobRequest":
        if not self.body and not self.config:
            raise ValueError("Either 'body' or 'config' must be provided")
        return self


class CreateSummaryJobRequest(BaseModel):
    """Request to create a case summary job."""

    type: Literal["summary"]
    case_folder_id: str = Field(..., description="Case folder identifier")
    file_ids: list[str] = Field(
        default_factory=list,
        description="File IDs to fetch from RAG engine",
    )
    drafts: list[DraftContext] = Field(
        default_factory=list, description="Optional generated drafts to include"
    )
    chat_highlights: list[str] = Field(
        default_factory=list, description="Key conversation points"
    )
    model: str = "gemini-3.1-flash-lite-preview"
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Optional extra context or parameters"
    )

    @field_validator("case_folder_id")
    @classmethod
    def validate_case_folder_id(cls, v: str) -> str:
        if not re.match(r"^[a-zA-Z0-9_-]+$", v):
            raise ValueError("case_folder_id must contain only alphanumeric characters, underscores, or dashes")
        return v


class CreateTranslationJobRequest(BaseModel):
    """Request to create a legal document translation job."""

    type: Literal["translation"]
    case_folder_id: str | None = Field(None, description="Case folder identifier")

    @field_validator("case_folder_id")
    @classmethod
    def validate_case_folder_id(cls, v: str | None) -> str | None:
        if v is not None and not re.match(r"^[a-zA-Z0-9_-]+$", v):
            raise ValueError(
                "case_folder_id must contain only alphanumeric characters, underscores, or dashes"
            )
        return v

    target_language: TranslationLanguage = Field(
        ..., description="Target language for translation"
    )
    source_language: TranslationLanguage | None = Field(
        None, description="Source language (auto-detected if omitted)"
    )
    document_type: DocumentType | None = Field(
        None,
        description=(
            "Type of legal document (court filing, contract, letter, etc.). "
            "Drives layout CSS, terminology register, and structure inference. "
            "If omitted and `auto_detect_document_type=True`, a cheap classifier "
            "infers it from the source text."
        ),
    )
    auto_detect_document_type: bool = Field(
        True,
        description=(
            "When `document_type` is not provided, run a lightweight LLM classifier "
            "over the first ~2000 chars of the source to infer it. Set to False to "
            "skip classification and use the default profile."
        ),
    )
    file_id: str | None = Field(
        None, description="S3 key of the encrypted source document to decrypt and translate"
    )
    file_name: str | None = Field(
        None, description="Original document name (e.g. 'WhatsApp Image 2026-03-13.pdf'). Used for naming the translated output."
    )
    content: str | None = Field(
        None, description="Raw text content to translate"
    )
    model: str | None = Field(
        None,
        description=(
            "Model alias: 'sarvam', 'gemini', 'claude', or 'openai'. "
            "Or a full model name (e.g. 'sarvam-30b', 'gpt-5.4'). "
            "Defaults to TRANSLATION_LLM_MODEL env var (server default: sarvam)."
        ),
    )
    translation_pipeline: Literal["v1", "v2", "v3"] | None = Field(
        None,
        description=(
            "Per-request override of the translation pipeline. v1 = PyMuPDF + Sarvam, "
            "v2 = Gemini vision + Gemini translate, v3 = Azure Document Intelligence + "
            "Haiku/Sarvam (cheap, editable, multilingual). "
            "Falls back to settings.translation_pipeline when omitted."
        ),
    )
    translate_engine: Literal["haiku", "sarvam"] | None = Field(
        None,
        description=(
            "v3 only: which engine drives the per-page translation. 'haiku' (default) uses "
            "Anthropic Claude Haiku 4.5; 'sarvam' uses Sarvam REST formal mode. Ignored on v1/v2."
        ),
    )
    metadata: dict[str, Any] = Field(default_factory=dict, description="Optional extra context")

    @model_validator(mode="after")
    def require_file_id_or_content(self) -> "CreateTranslationJobRequest":
        has_content = bool(self.content and self.content.strip())
        if not self.file_id and not has_content:
            raise ValueError("Either 'file_id' or 'content' must be provided")
        return self


class CreateSynopsisJobRequest(BaseModel):
    """Request to create a case synopsis job."""

    type: Literal["synopsis"]
    case_folder_id: str = Field(..., description="Case folder identifier")
    file_ids: list[str] = Field(
        default_factory=list,
        description="File IDs to fetch from RAG engine",
    )
    model: str = "gemini-3.1-flash-lite-preview"
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Optional extra context or parameters"
    )

    @field_validator("case_folder_id")
    @classmethod
    def validate_case_folder_id(cls, v: str) -> str:
        if not re.match(r"^[a-zA-Z0-9_-]+$", v):
            raise ValueError(
                "case_folder_id must contain only alphanumeric characters, underscores, or dashes"
            )
        return v


class CreatePrecedentJobRequest(BaseModel):
    """Request to find precedents relevant to a case folder.

    Pipeline: build a brief from the case documents, search the internal
    Supreme Court judgments DB first, supplement with Firecrawl on trusted
    legal domains if needed, then synthesise a ranked precedent list.
    """

    type: Literal["precedent"]
    case_folder_id: str = Field(..., description="Case folder identifier")
    file_ids: list[str] = Field(
        default_factory=list,
        description="File IDs to fetch from RAG engine for case-brief extraction",
    )
    top_k: int = Field(
        8,
        ge=1,
        le=20,
        description="Number of precedents to return from the internal DB before synthesis",
    )
    model: str = "gemini-3.1-flash-lite-preview"
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Optional extra context or parameters"
    )

    @field_validator("case_folder_id")
    @classmethod
    def validate_case_folder_id(cls, v: str) -> str:
        if not re.match(r"^[a-zA-Z0-9_-]+$", v):
            raise ValueError(
                "case_folder_id must contain only alphanumeric characters, underscores, or dashes"
            )
        return v


CreateJobRequest = Annotated[
    CreateDraftJobRequest
    | CreateSummaryJobRequest
    | CreateSynopsisJobRequest
    | CreateTranslationJobRequest
    | CreatePrecedentJobRequest,
    Field(discriminator="type"),
]
