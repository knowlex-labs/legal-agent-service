"""API routes for the Legal Agent Service."""

import logging

from fastapi import APIRouter, Depends, Header, HTTPException, Query

from legal_agent.models.requests import CreateDraftRequest

logger = logging.getLogger(__name__)
from legal_agent.models.responses import (
    CreateDraftResponse,
    JobListResponse,
    JobResponse,
    JobStatus,
)
from legal_agent.services.draft_service import DraftService
from legal_agent.services.job_manager import JobManager

router = APIRouter()


# Dependency injection placeholders - will be set by main.py
_draft_service: DraftService | None = None
_job_manager: JobManager | None = None


def set_services(draft_service: DraftService, job_manager: JobManager) -> None:
    """Set the service instances for dependency injection."""
    global _draft_service, _job_manager
    _draft_service = draft_service
    _job_manager = job_manager


def get_draft_service() -> DraftService:
    """Get the draft service instance."""
    if _draft_service is None:
        raise RuntimeError("Draft service not initialized")
    return _draft_service


def get_job_manager() -> JobManager:
    """Get the job manager instance."""
    if _job_manager is None:
        raise RuntimeError("Job manager not initialized")
    return _job_manager


@router.post("/drafts", response_model=CreateDraftResponse, status_code=201)
async def create_draft(
    request: CreateDraftRequest,
    x_user_id: str = Header(..., alias="X-User-Id"),
    draft_service: DraftService = Depends(get_draft_service),
    job_manager: JobManager = Depends(get_job_manager),
) -> CreateDraftResponse:
    """Create a new draft job."""
    logger.info(f"POST /drafts: type={request.document_type.value}, title='{request.title}', user={x_user_id}")

    job_id = await draft_service.create_draft_job(request, user_id=x_user_id)
    job = await job_manager.get_job(job_id)

    if not job:
        logger.error("Failed to create job")
        raise HTTPException(status_code=500, detail="Failed to create job")

    return CreateDraftResponse(
        job_id=job.job_id,
        status=job.status,
        created_at=job.created_at,
    )


@router.get("/drafts/{job_id}", response_model=JobResponse)
async def get_draft(
    job_id: str,
    job_manager: JobManager = Depends(get_job_manager),
) -> JobResponse:
    """Get the status and result of a draft job."""
    job = await job_manager.get_job(job_id)

    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    logger.debug(f"GET /drafts/{job_id}: status={job.status.value}")

    return JobResponse(
        job_id=job.job_id,
        status=job.status,
        created_at=job.created_at,
        completed_at=job.completed_at,
        result=job.result,
        error=job.error,
    )


@router.get("/drafts", response_model=JobListResponse)
async def list_drafts(
    status: JobStatus | None = Query(None, description="Filter by job status"),
    limit: int = Query(100, ge=1, le=1000, description="Maximum number of jobs to return"),
    offset: int = Query(0, ge=0, description="Number of jobs to skip"),
    job_manager: JobManager = Depends(get_job_manager),
) -> JobListResponse:
    """List all draft jobs with optional filtering."""
    jobs, total = await job_manager.list_jobs(status=status, limit=limit, offset=offset)

    return JobListResponse(
        jobs=[
            JobResponse(
                job_id=job.job_id,
                status=job.status,
                created_at=job.created_at,
                completed_at=job.completed_at,
                result=job.result,
                error=job.error,
            )
            for job in jobs
        ],
        total=total,
    )


@router.get("/health")
async def health_check() -> dict:
    """Health check endpoint."""
    return {"status": "healthy", "service": "legal-agent-service"}
