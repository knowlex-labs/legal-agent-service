"""API routes for the Legal Agent Service."""

import logging

from fastapi import APIRouter, Depends, Header, HTTPException, Query

from legal_agent.clients.s3_client import S3Client
from legal_agent.models.requests import (
    CreateDraftJobRequest,
    CreateJobRequest,
    CreateSummaryJobRequest,
    CreateSynopsisJobRequest,
    CreateTranslationJobRequest,
)
from legal_agent.models.responses import (
    CreateJobResponse,
    JobListResponse,
    JobResponse,
    JobStatus,
    JobType,
)
from legal_agent.services.draft_service import DraftService
from legal_agent.services.job_manager import JobManager
from legal_agent.summary.service import SummaryService
from legal_agent.synopsis.service import SynopsisService
from legal_agent.agents.translation.service import TranslationService

logger = logging.getLogger(__name__)

router = APIRouter()

# Dependency injection placeholders - set by main.py
_draft_service: DraftService | None = None
_summary_service: SummaryService | None = None
_synopsis_service: SynopsisService | None = None
_translation_service: TranslationService | None = None
_job_manager: JobManager | None = None
_s3_client: S3Client | None = None


def set_services(
    draft_service: DraftService,
    summary_service: SummaryService,
    synopsis_service: SynopsisService,
    translation_service: TranslationService,
    job_manager: JobManager,
    s3_client: S3Client,
) -> None:
    global _draft_service, _summary_service, _synopsis_service, _translation_service, _job_manager, _s3_client
    _draft_service = draft_service
    _summary_service = summary_service
    _synopsis_service = synopsis_service
    _translation_service = translation_service
    _job_manager = job_manager
    _s3_client = s3_client


def get_draft_service() -> DraftService:
    if _draft_service is None:
        raise RuntimeError("Draft service not initialized")
    return _draft_service


def get_summary_service() -> SummaryService:
    if _summary_service is None:
        raise RuntimeError("Summary service not initialized")
    return _summary_service


def get_synopsis_service() -> SynopsisService:
    if _synopsis_service is None:
        raise RuntimeError("Synopsis service not initialized")
    return _synopsis_service


def get_translation_service() -> TranslationService:
    if _translation_service is None:
        raise RuntimeError("Translation service not initialized")
    return _translation_service


def get_job_manager() -> JobManager:
    if _job_manager is None:
        raise RuntimeError("Job manager not initialized")
    return _job_manager


def get_s3_client() -> S3Client:
    if _s3_client is None:
        raise RuntimeError("S3 client not initialized")
    return _s3_client


@router.post("/jobs", response_model=CreateJobResponse, status_code=201)
async def create_job(
    request: CreateJobRequest,
    x_user_id: str = Header(..., alias="X-User-Id"),
    draft_service: DraftService = Depends(get_draft_service),
    summary_service: SummaryService = Depends(get_summary_service),
    synopsis_service: SynopsisService = Depends(get_synopsis_service),
    translation_service: TranslationService = Depends(get_translation_service),
    job_manager: JobManager = Depends(get_job_manager),
) -> CreateJobResponse:
    """Create a new job (draft, summary, synopsis, or translation)."""
    if isinstance(request, CreateDraftJobRequest):
        logger.info(
            f"POST /jobs [draft]: type={request.document_type.value}, "
            f"title='{request.title}', user={x_user_id}"
        )
        job_id = await draft_service.create_draft_job(request, user_id=x_user_id)
    elif isinstance(request, CreateTranslationJobRequest):
        logger.info(
            f"POST /jobs [translation]: target={request.target_language.value}, "
            f"case_folder_id={request.case_folder_id}, user={x_user_id}"
        )
        job_id = await translation_service.create_translation_job(request, user_id=x_user_id)
    elif isinstance(request, CreateSummaryJobRequest):
        logger.info(
            f"POST /jobs [summary]: case_folder_id={request.case_folder_id}, user={x_user_id}"
        )
        job_id = await summary_service.create_summary_job(request, user_id=x_user_id)
    elif isinstance(request, CreateSynopsisJobRequest):
        logger.info(
            f"POST /jobs [synopsis]: case_folder_id={request.case_folder_id}, user={x_user_id}"
        )
        job_id = await synopsis_service.create_synopsis_job(request, user_id=x_user_id)
    else:
        raise HTTPException(status_code=400, detail=f"Unknown job type: {request.type}")

    job = await job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=500, detail="Failed to create job")

    return CreateJobResponse(
        job_id=job.job_id,
        type=job.job_type,
        status=job.status,
        created_at=job.created_at,
    )


@router.get("/jobs/{job_id}", response_model=JobResponse)
async def get_job(
    job_id: str,
    job_manager: JobManager = Depends(get_job_manager),
    s3_client: S3Client = Depends(get_s3_client),
) -> JobResponse:
    """Get the status of a job."""
    job = await job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    logger.debug(f"GET /jobs/{job_id}: status={job.status.value}")

    signed_url: str | None = None
    if job.s3_path:
        try:
            signed_url = await s3_client.signed_url(job.s3_path)
        except Exception:
            logger.warning(f"[{job_id}] Failed to generate signed URL", exc_info=True)

    return JobResponse(
        job_id=job.job_id,
        type=job.job_type,
        status=job.status,
        created_at=job.created_at,
        completed_at=job.completed_at,
        updated_at=job.updated_at,
        s3_path=job.s3_path,
        storage_url=job.storage_url,
        signed_url=signed_url,
        metadata=job.metadata,
        error=job.error,
        title=job.title,
        subtype=job.subtype,
        user_id=job.user_id,
        legal_case_id=job.legal_case_id,
        file_name=job.file_name,
        file_type=job.file_type,
        indexing_status=job.indexing_status,
        version=job.version,
        original_filename=job.original_filename,
    )


@router.get("/jobs", response_model=JobListResponse)
async def list_jobs(
    type: JobType | None = Query(None, description="Filter by job type"),
    status: JobStatus | None = Query(None, description="Filter by job status"),
    case_folder_id: str | None = Query(None, description="Filter by case folder ID"),
    limit: int = Query(100, ge=1, le=1000, description="Maximum number of jobs to return"),
    offset: int = Query(0, ge=0, description="Number of jobs to skip"),
    include_signed_url: bool = Query(False, description="Include signed URLs in response"),
    job_manager: JobManager = Depends(get_job_manager),
    s3_client: S3Client = Depends(get_s3_client),
) -> JobListResponse:
    """List jobs with optional filtering."""
    jobs, total = await job_manager.list_jobs(
        job_type=type,
        status=status,
        case_folder_id=case_folder_id,
        limit=limit,
        offset=offset,
    )

    job_responses = []
    for job in jobs:
        signed_url: str | None = None
        if include_signed_url and job.s3_path:
            try:
                signed_url = await s3_client.signed_url(job.s3_path)
            except Exception:
                logger.warning(f"[{job.job_id}] Failed to generate signed URL", exc_info=True)

        job_responses.append(
            JobResponse(
                job_id=job.job_id,
                type=job.job_type,
                status=job.status,
                created_at=job.created_at,
                completed_at=job.completed_at,
                updated_at=job.updated_at,
                s3_path=job.s3_path,
                storage_url=job.storage_url,
                signed_url=signed_url,
                metadata=job.metadata,
                error=job.error,
                title=job.title,
                subtype=job.subtype,
                user_id=job.user_id,
                legal_case_id=job.legal_case_id,
                file_name=job.file_name,
                file_type=job.file_type,
                indexing_status=job.indexing_status,
                version=job.version,
                original_filename=job.original_filename,
            )
        )

    return JobListResponse(jobs=job_responses, total=total)


@router.get("/health")
async def health_check(job_manager: JobManager = Depends(get_job_manager)) -> dict:
    """Health check endpoint."""
    return {
        "status": "healthy",
        "service": "legal-agent-service",
        "jobs_count": len(job_manager._jobs),
    }
