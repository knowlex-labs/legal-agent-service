"""API response schemas."""

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class JobStatus(str, Enum):
    """Status of a job."""

    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class JobType(str, Enum):
    """Type of job."""

    DRAFT = "draft"
    SUMMARY = "summary"
    SYNOPSIS = "synopsis"
    TRANSLATION = "translation"
    PRECEDENT = "precedent"


class CreateJobResponse(BaseModel):
    """Response when a job is created."""

    job_id: str = Field(..., description="Unique job identifier")
    type: JobType = Field(..., description="Job type")
    status: JobStatus = Field(..., description="Current job status")
    created_at: datetime = Field(..., description="Job creation timestamp")


class JobResponse(BaseModel):
    """Full job status response."""

    job_id: str = Field(..., description="Unique job identifier")
    type: JobType = Field(..., description="Job type")
    status: JobStatus = Field(..., description="Current job status")
    created_at: datetime = Field(..., description="Job creation timestamp")
    completed_at: datetime | None = Field(None, description="Job completion timestamp")
    updated_at: datetime | None = Field(None, description="Last update timestamp")
    s3_path: str | None = Field(None, description="S3 key when completed")
    storage_url: str | None = Field(None, description="Full S3 URL for the file")
    signed_url: str | None = Field(None, description="Signed URL for downloading the result")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Job metadata")
    error: str | None = Field(None, description="Error message if failed")

    # Extended fields
    title: str | None = Field(None, description="Document title")
    subtype: str | None = Field(None, description="Document subtype")
    user_id: str | None = Field(None, description="User who created the job")
    legal_case_id: str | None = Field(None, description="Legal case/folder ID")
    file_name: str | None = Field(None, description="File name")
    file_type: str | None = Field(None, description="File type/MIME type")
    indexing_status: str | None = Field(None, description="Indexing status")
    version: int = Field(1, description="Document version")
    original_filename: str | None = Field(None, description="Original uploaded filename")


class JobListResponse(BaseModel):
    """Response for listing jobs."""

    jobs: list[JobResponse] = Field(..., description="List of jobs")
    total: int = Field(..., description="Total number of jobs")


class ExtractDraftFieldsRequest(BaseModel):
    file_id: str = Field(..., description="S3 file_id of the uploaded source document")


class ExtractDraftFieldsResponse(BaseModel):
    suggested_fields: dict[str, str] = Field(
        default_factory=dict,
        description="Form-field id → suggested value. Empty when no metadata could be extracted.",
    )
