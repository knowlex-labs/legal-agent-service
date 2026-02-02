"""Data models for the Legal Agent Service."""

from legal_agent.models.documents import DocumentType
from legal_agent.models.requests import CreateDraftRequest
from legal_agent.models.responses import (
    CreateDraftResponse,
    DraftResult,
    JobResponse,
    JobStatus,
)

__all__ = [
    "DocumentType",
    "CreateDraftRequest",
    "CreateDraftResponse",
    "DraftResult",
    "JobResponse",
    "JobStatus",
]
