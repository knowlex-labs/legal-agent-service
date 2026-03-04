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
    s3_path: str | None = Field(None, description="S3 key when completed")
    signed_url: str | None = Field(None, description="Signed URL for downloading the result")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Job metadata")
    error: str | None = Field(None, description="Error message if failed")


class JobListResponse(BaseModel):
    """Response for listing jobs."""

    jobs: list[JobResponse] = Field(..., description="List of jobs")
    total: int = Field(..., description="Total number of jobs")
