"""In-memory job manager for tracking jobs."""

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Callable, Coroutine

from legal_agent.config import get_settings
from legal_agent.models.responses import JobStatus, JobType


class ErrorStage(str, Enum):
    """Which pipeline stage produced an error. Used to tag job failures so
    the frontend can surface actionable messages (and so oncall can triage
    quickly)."""
    EXTRACTION = "extraction"           # PDF/image text extraction
    OCR = "ocr"                         # Vision OCR (Gemini or Sarvam)
    RAG = "rag"                         # Workspace RAG retrieval
    TRANSLATION = "translation"         # LLM translation
    DRAFTING = "drafting"               # LLM drafting agent
    PDF_RENDER = "pdf_render"           # Markdown → PDF
    UPLOAD = "upload"                   # S3 upload of final artifact
    POSTPROCESS = "postprocess"         # Placeholder / Aadhaar / length checks
    STRUCTURE = "structure"             # LLM structure-inference pass
    CLASSIFICATION = "classification"   # Document-type auto-detect
    RENDER_GUARD = "render_guard"       # Post-render validation (tofu / ledger / size)
    UNKNOWN = "unknown"


class StagedError(Exception):
    """Wraps an underlying exception with the pipeline stage that raised it.

    Caught by the job manager's exception handler; the stored error string
    uses the `[STAGE] reason` format for frontend parsing.
    """

    def __init__(self, stage: ErrorStage, cause: BaseException):
        self.stage = stage
        self.cause = cause
        super().__init__(f"[{stage.value.upper()}] {type(cause).__name__}: {cause}")


def raise_staged(stage: ErrorStage, cause: BaseException) -> None:
    """Helper: raise StagedError(stage, cause) from cause — preserves chain."""
    raise StagedError(stage, cause) from cause

# Maximum number of terminal (completed/failed) jobs kept in memory per restart.
# Active (pending/processing) jobs are never evicted.
_MAX_TERMINAL_JOBS = 200

logger = logging.getLogger(__name__)


@dataclass
class Job:
    """Represents a job."""

    job_id: str
    job_type: JobType
    status: JobStatus
    created_at: datetime
    completed_at: datetime | None = None
    updated_at: datetime | None = None
    s3_path: str | None = None
    storage_url: str | None = None
    error: str | None = None

    # Extended fields for DB persistence
    subtype: str | None = None
    title: str | None = None
    user_id: str | None = None
    legal_case_id: str | None = None
    file_name: str | None = None
    indexing_status: str | None = None
    version: int = 1
    original_filename: str | None = None
    file_type: str | None = None

    metadata: dict[str, Any] = field(default_factory=dict)


class JobManager:
    """In-memory job manager for tracking and executing jobs."""

    def __init__(self):
        self._jobs: dict[str, Job] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._lock = asyncio.Lock()

    async def create_job(
        self,
        job_type: JobType,
        metadata: dict[str, Any] | None = None,
        title: str | None = None,
        user_id: str | None = None,
        legal_case_id: str | None = None,
        subtype: str | None = None,
    ) -> Job:
        """Create a new pending job."""
        job_id = str(uuid.uuid4())
        job = Job(
            job_id=job_id,
            job_type=job_type,
            status=JobStatus.PENDING,
            created_at=datetime.now(UTC),
            title=title,
            user_id=user_id,
            legal_case_id=legal_case_id,
            subtype=subtype,
            metadata=metadata or {},
        )
        async with self._lock:
            self._jobs[job_id] = job
            self._evict_terminal_jobs()
        return job

    async def get_job(self, job_id: str) -> Job | None:
        """Get a job by ID."""
        return self._jobs.get(job_id)

    async def list_jobs(
        self,
        job_type: JobType | None = None,
        status: JobStatus | None = None,
        case_folder_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[Job], int]:
        """List jobs with optional filtering."""
        jobs = list(self._jobs.values())

        if job_type:
            jobs = [j for j in jobs if j.job_type == job_type]
        if status:
            jobs = [j for j in jobs if j.status == status]
        if case_folder_id:
            jobs = [j for j in jobs if j.metadata.get("case_folder_id") == case_folder_id]

        jobs.sort(key=lambda j: j.created_at, reverse=True)

        total = len(jobs)
        jobs = jobs[offset : offset + limit]

        return jobs, total

    async def update_job_status(
        self,
        job_id: str,
        status: JobStatus,
        s3_path: str | None = None,
        storage_url: str | None = None,
        error: str | None = None,
        file_name: str | None = None,
        file_type: str | None = None,
        indexing_status: str | None = None,
    ) -> Job | None:
        """Update a job's status."""
        async with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return None

            old_status = job.status
            job.status = status
            job.updated_at = datetime.now(UTC)

            if status in (JobStatus.COMPLETED, JobStatus.FAILED):
                job.completed_at = datetime.now(UTC)
            if s3_path:
                job.s3_path = s3_path
            if storage_url:
                job.storage_url = storage_url
            if error:
                job.error = error
            if file_name:
                job.file_name = file_name
            if file_type:
                job.file_type = file_type
            if indexing_status:
                job.indexing_status = indexing_status

            logger.debug(f"[{job_id}] Status: {old_status.value} -> {status.value}")
            return job

    async def update_job_metadata(self, job_id: str, **kv: Any) -> None:
        """Merge keyword args into the job's metadata dict.

        None values are skipped so callers can pass optional fields without
        clobbering existing entries. Used by the translation pipeline to
        attach `detected_document_type`, `layout_family`, `ledger_entry_count`,
        and `render_warnings` for observability.
        """
        async with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            for k, v in kv.items():
                if v is None:
                    continue
                job.metadata[k] = v

    async def run_job(
        self,
        job_id: str,
        task_fn: Callable[[], Coroutine[Any, Any, Any]],
        timeout_seconds: int | None = None,
    ) -> None:
        """Run a job's task in the background. task_fn must handle its own completion."""
        if timeout_seconds is None:
            timeout_seconds = get_settings().job_timeout_seconds

        await self.update_job_status(job_id, JobStatus.PROCESSING)

        async def _execute():
            try:
                await asyncio.wait_for(task_fn(), timeout=timeout_seconds)
                logger.info(f"[{job_id}] Job completed successfully")
            except asyncio.TimeoutError:
                logger.error(f"[{job_id}] Job timed out after {timeout_seconds}s")
                await self.update_job_status(
                    job_id,
                    JobStatus.FAILED,
                    error=f"[{ErrorStage.UNKNOWN.value.upper()}] Job timed out after {timeout_seconds}s",
                )
            except asyncio.CancelledError:
                logger.warning(f"[{job_id}] Job cancelled")
            except StagedError as e:
                # Already tagged with stage — preserve the message shape.
                logger.error(f"[{job_id}] Job failed at stage={e.stage.value}: {e.cause}", exc_info=True)
                await self.update_job_status(job_id, JobStatus.FAILED, error=str(e))
            except Exception as e:
                # Untagged — wrap with UNKNOWN so the frontend can still
                # parse the `[STAGE] reason` format.
                logger.error(f"[{job_id}] Job failed (untagged): {e}", exc_info=True)
                await self.update_job_status(
                    job_id,
                    JobStatus.FAILED,
                    error=f"[{ErrorStage.UNKNOWN.value.upper()}] {type(e).__name__}: {e}",
                )
            finally:
                async with self._lock:
                    self._tasks.pop(job_id, None)

        task = asyncio.create_task(_execute())
        async with self._lock:
            self._tasks[job_id] = task

    def _evict_terminal_jobs(self) -> None:
        """Drop oldest completed/failed jobs once the cap is exceeded. Must be called under self._lock."""
        terminal = [
            j for j in self._jobs.values()
            if j.status in (JobStatus.COMPLETED, JobStatus.FAILED)
        ]
        if len(terminal) <= _MAX_TERMINAL_JOBS:
            return
        terminal.sort(key=lambda j: j.completed_at or j.created_at)
        to_drop = len(terminal) - _MAX_TERMINAL_JOBS
        for job in terminal[:to_drop]:
            del self._jobs[job.job_id]
        logger.debug(f"Evicted {to_drop} old terminal jobs (cap={_MAX_TERMINAL_JOBS})")

    async def cleanup(self) -> None:
        """Cancel all running tasks and clean up."""
        async with self._lock:
            for task in self._tasks.values():
                if not task.done():
                    task.cancel()
            self._tasks.clear()
