"""API response schemas."""

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from legal_agent.models.documents import DocumentSection


class JobStatus(str, Enum):
    """Status of a drafting job."""

    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class CreateDraftResponse(BaseModel):
    """Response when a draft job is created."""

    job_id: str = Field(..., description="Unique job identifier")
    status: JobStatus = Field(..., description="Current job status")
    created_at: datetime = Field(..., description="Job creation timestamp")


class DraftResult(BaseModel):
    """Result of a completed draft job."""

    draft: str = Field(..., description="The generated document draft")
    sections: list[DocumentSection] = Field(
        default_factory=list, description="Structured sections if applicable"
    )
    metadata: dict[str, Any] = Field(default_factory=dict, description="Additional metadata")


class JobResponse(BaseModel):
    """Full job status and result response."""

    job_id: str = Field(..., description="Unique job identifier")
    status: JobStatus = Field(..., description="Current job status")
    created_at: datetime = Field(..., description="Job creation timestamp")
    completed_at: datetime | None = Field(None, description="Job completion timestamp")
    result: DraftResult | None = Field(None, description="Draft result if completed")
    error: str | None = Field(None, description="Error message if failed")


class JobListResponse(BaseModel):
    """Response for listing jobs."""

    jobs: list[JobResponse] = Field(..., description="List of jobs")
    total: int = Field(..., description="Total number of jobs")
