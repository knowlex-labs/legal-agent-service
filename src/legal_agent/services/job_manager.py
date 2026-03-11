"""In-memory job manager for tracking jobs."""

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Callable, Coroutine

from legal_agent.config import get_settings
from legal_agent.models.responses import JobStatus, JobType

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

    async def run_job(
        self,
        job_id: str,
        task_fn: Callable[[], Coroutine[Any, Any, str]],
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
                await self.update_job_status(job_id, JobStatus.FAILED, error=f"Job timed out after {timeout_seconds}s")
            except asyncio.CancelledError:
                logger.warning(f"[{job_id}] Job cancelled")
            except Exception as e:
                logger.error(f"[{job_id}] Job failed: {e}", exc_info=True)
                await self.update_job_status(job_id, JobStatus.FAILED, error=str(e))
            finally:
                async with self._lock:
                    self._tasks.pop(job_id, None)

        task = asyncio.create_task(_execute())
        async with self._lock:
            self._tasks[job_id] = task

    async def cleanup(self) -> None:
        """Cancel all running tasks and clean up."""
        async with self._lock:
            for task in self._tasks.values():
                if not task.done():
                    task.cancel()
            self._tasks.clear()
