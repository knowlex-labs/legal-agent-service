"""Pydantic schemas for custom drafting templates."""

from datetime import datetime

from pydantic import BaseModel, Field, model_validator


class CreateTemplateRequest(BaseModel):
    """Request to register a user's custom drafting template."""

    name: str = Field(..., description="Human-readable name for the template", min_length=1)
    document_type: str = Field(
        ..., description="Free-form label, e.g. 'My NDA', 'bail_application'", min_length=1
    )
    s3_path: str | None = Field(None, description="S3 key of the already-uploaded template file")
    content: str | None = Field(None, description="Raw text content (alternative to s3_path)")

    @model_validator(mode="after")
    def require_s3_path_or_content(self) -> "CreateTemplateRequest":
        if not self.s3_path and not self.content:
            raise ValueError("Either 's3_path' or 'content' must be provided")
        return self


class TemplateResponse(BaseModel):
    """Response with template metadata and generated drafting prompt."""

    id: str
    user_id: str
    name: str
    document_type: str
    s3_path: str | None
    generated_prompt: str
    created_at: datetime
    updated_at: datetime
