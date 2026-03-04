"""Data models for the Legal Agent Service."""

from legal_agent.models.requests import CreateDraftJobRequest, CreateJobRequest, CreateSummaryJobRequest
from legal_agent.models.responses import (
    CreateJobResponse,
    JobListResponse,
    JobResponse,
    JobStatus,
    JobType,
)

__all__ = [
    "CreateDraftJobRequest",
    "CreateSummaryJobRequest",
    "CreateJobRequest",
    "CreateJobResponse",
    "JobListResponse",
    "JobResponse",
    "JobStatus",
    "JobType",
]
