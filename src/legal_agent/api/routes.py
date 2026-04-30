"""API routes for the Legal Agent Service."""

import asyncio
import logging
import pathlib
import tempfile

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field

from legal_agent.clients.s3_client import S3Client
from legal_agent.config import get_settings
from legal_agent.models.requests import (
    CreateDraftJobRequest,
    CreateJobRequest,
    CreatePrecedentJobRequest,
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
from legal_agent.precedents.service import PrecedentService
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
_precedent_service: PrecedentService | None = None
_job_manager: JobManager | None = None
_s3_client: S3Client | None = None


def set_services(
    draft_service: DraftService,
    summary_service: SummaryService,
    synopsis_service: SynopsisService,
    translation_service: TranslationService,
    precedent_service: PrecedentService,
    job_manager: JobManager,
    s3_client: S3Client,
) -> None:
    global _draft_service, _summary_service, _synopsis_service, _translation_service, _precedent_service, _job_manager, _s3_client
    _draft_service = draft_service
    _summary_service = summary_service
    _synopsis_service = synopsis_service
    _translation_service = translation_service
    _precedent_service = precedent_service
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


def get_precedent_service() -> PrecedentService:
    if _precedent_service is None:
        raise RuntimeError("Precedent service not initialized")
    return _precedent_service


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
    precedent_service: PrecedentService = Depends(get_precedent_service),
    job_manager: JobManager = Depends(get_job_manager),
) -> CreateJobResponse:
    """Create a new job (draft, summary, synopsis, translation, or precedent)."""
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
    elif isinstance(request, CreatePrecedentJobRequest):
        logger.info(
            f"POST /jobs [precedent]: case_folder_id={request.case_folder_id}, user={x_user_id}"
        )
        job_id = await precedent_service.create_precedent_job(request, user_id=x_user_id)
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


def _pdf2docx_convert(in_path: str, out_path: str) -> None:
    # Imported lazily so the (PyMuPDF-backed) module is only loaded on first use.
    from pdf2docx import Converter

    cv = Converter(in_path)
    try:
        cv.convert(out_path)
    finally:
        cv.close()


_MAX_CONVERT_BODY_BYTES = 50 * 1024 * 1024  # 50 MB


async def _read_capped_body(request: Request, max_bytes: int) -> bytes:
    """Read the request body with a hard size cap.

    Reject early on Content-Length when the client declares the size, and
    re-check after read in case the header was stripped or lied.
    """
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"body exceeds {max_bytes // (1024 * 1024)} MB limit",
        )
    body = await request.body()
    if len(body) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"body exceeds {max_bytes // (1024 * 1024)} MB limit",
        )
    return body


@router.post("/documents/convert/pdf-to-docx")
async def convert_pdf_to_docx(request: Request) -> Response:
    """Sync PDF→DOCX via pdf2docx.

    Body: raw application/pdf bytes.
    Response: application/vnd.openxmlformats-officedocument.wordprocessingml.document.

    Used by the Java platform API as a fallback when LibreOffice can't handle the
    input (Microsoft Print-to-PDF, certain scans, etc.). pdf2docx is CPU-bound and
    sync, so we run it in a thread to avoid blocking the event loop.
    """
    pdf_bytes = await _read_capped_body(request, _MAX_CONVERT_BODY_BYTES)
    if not pdf_bytes:
        raise HTTPException(status_code=400, detail="empty body")

    with tempfile.TemporaryDirectory(prefix="pdf2docx-") as tmp:
        in_path = pathlib.Path(tmp) / "input.pdf"
        out_path = pathlib.Path(tmp) / "output.docx"
        in_path.write_bytes(pdf_bytes)

        try:
            await asyncio.to_thread(_pdf2docx_convert, str(in_path), str(out_path))
        except Exception as e:
            logger.warning("pdf2docx conversion failed: %s", e, exc_info=True)
            raise HTTPException(status_code=422, detail="pdf-to-docx conversion failed")

        if not out_path.exists() or out_path.stat().st_size == 0:
            raise HTTPException(status_code=422, detail="pdf2docx produced no output")

        return Response(
            content=out_path.read_bytes(),
            media_type=(
                "application/vnd.openxmlformats-officedocument."
                "wordprocessingml.document"
            ),
        )


def _mammoth_to_html(body: bytes) -> str:
    import re
    from io import BytesIO

    import mammoth

    # v1 is text-only. The no-op image converter avoids reading image bytes
    # (mammoth's default data_uri converter inlines them as base64, bloating
    # the response); we then strip the resulting empty <img> tags.
    convert_image = mammoth.images.inline(lambda _image: {"src": ""})
    result = mammoth.convert_to_html(BytesIO(body), convert_image=convert_image)
    if result.messages:
        logger.info(
            "mammoth extraction notes: %s",
            [m.message for m in result.messages[:5]],
        )
    return re.sub(r"<img\b[^>]*/?>", "", result.value)


@router.post("/documents/convert/pdf-to-html")
async def convert_pdf_to_html(request: Request) -> Response:
    """Convert a document to clean structured HTML for in-place editing.

    Body: raw application/pdf or DOCX bytes.
    Response: text/html (UTF-8) — a fragment (no <html>/<body> wrapper),
    suitable for Tiptap setContent() on the frontend.

    PDFs go through the multimodal OCR pipeline with output_format='html'
    (Gemini 3.1 Flash primary; Mistral Pixtral as configurable fallback).
    DOCX goes through mammoth's native HTML converter.
    """
    body = await _read_capped_body(request, _MAX_CONVERT_BODY_BYTES)
    if not body:
        raise HTTPException(status_code=400, detail="empty body")

    ctype = (request.headers.get("content-type") or "").lower()

    try:
        if ctype.startswith("application/pdf"):
            from legal_agent.utils.ocr import ocr_pdf
            html = await asyncio.to_thread(ocr_pdf, body, "html")
        elif "wordprocessingml" in ctype:
            html = await asyncio.to_thread(_mammoth_to_html, body)
        else:
            raise HTTPException(
                status_code=415,
                detail=f"unsupported content type: {ctype or '(missing)'}",
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.warning("html conversion failed (ctype=%s): %s", ctype, e, exc_info=True)
        raise HTTPException(status_code=422, detail="document conversion failed")

    if not html or not html.strip():
        raise HTTPException(status_code=422, detail="conversion produced no content")

    return Response(content=html.encode("utf-8"), media_type="text/html; charset=utf-8")


class TranslateRequest(BaseModel):
    """Inline-translation request used by the in-place document editor."""

    text: str = Field(..., min_length=1, max_length=8000)
    source_language: str = Field(
        ...,
        description="BCP-47 / Sarvam language code, e.g. 'en-IN', 'hi-IN', 'ta-IN', 'auto'",
        max_length=16,
    )
    target_language: str = Field(..., max_length=16)


class TranslateResponse(BaseModel):
    translated_text: str
    source_language: str
    target_language: str


def _sarvam_translate(text: str, source_lang: str, target_lang: str) -> str:
    """Call Sarvam's text translation API. Synchronous SDK; runs in a thread."""
    from sarvamai import SarvamAI  # type: ignore

    settings = get_settings()
    if not settings.sarvam_api_key:
        raise RuntimeError(
            "SARVAM_API_KEY is not configured; cannot serve /translate."
        )
    client = SarvamAI(api_subscription_key=settings.sarvam_api_key)
    response = client.text.translate(
        input=text,
        source_language_code=source_lang,
        target_language_code=target_lang,
    )
    out = getattr(response, "translated_text", None) or ""
    if not out.strip():
        raise RuntimeError("Sarvam translate returned empty output")
    return out


@router.post("/translate", response_model=TranslateResponse)
async def translate_text(payload: TranslateRequest) -> TranslateResponse:
    """Translate a paragraph between English and an Indic language via Sarvam.

    Used by the document editor's "Translate selection" action. Single short call
    per request (paragraph-sized, ≤8000 chars), so we run it inline in a thread
    rather than going through JobManager.
    """
    if payload.source_language == payload.target_language:
        return TranslateResponse(
            translated_text=payload.text,
            source_language=payload.source_language,
            target_language=payload.target_language,
        )

    try:
        translated = await asyncio.to_thread(
            _sarvam_translate,
            payload.text,
            payload.source_language,
            payload.target_language,
        )
    except Exception as e:
        logger.warning("sarvam translate failed: %s", e, exc_info=True)
        raise HTTPException(status_code=502, detail="translation failed")

    return TranslateResponse(
        translated_text=translated,
        source_language=payload.source_language,
        target_language=payload.target_language,
    )
