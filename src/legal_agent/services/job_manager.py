"""In-memory job manager for tracking draft jobs."""

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Callable, Coroutine

from legal_agent.models.responses import DraftResult, JobStatus

logger = logging.getLogger(__name__)


@dataclass
class Job:
    """Represents a drafting job."""

    job_id: str
    status: JobStatus
    created_at: datetime
    completed_at: datetime | None = None
    result: DraftResult | None = None
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class JobManager:
    """In-memory job manager for tracking and executing draft jobs."""

    def __init__(self):
        self._jobs: dict[str, Job] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._lock = asyncio.Lock()

    async def create_job(self, metadata: dict[str, Any] | None = None) -> Job:
        """Create a new pending job."""
        job_id = str(uuid.uuid4())
        job = Job(
            job_id=job_id,
            status=JobStatus.PENDING,
            created_at=datetime.now(UTC),
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
        status: JobStatus | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[Job], int]:
        """List jobs with optional filtering."""
        jobs = list(self._jobs.values())

        if status:
            jobs = [j for j in jobs if j.status == status]

        jobs.sort(key=lambda j: j.created_at, reverse=True)

        total = len(jobs)
        jobs = jobs[offset : offset + limit]

        return jobs, total

    async def update_job_status(
        self,
        job_id: str,
        status: JobStatus,
        result: DraftResult | None = None,
        error: str | None = None,
    ) -> Job | None:
        """Update a job's status."""
        async with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return None

            old_status = job.status
            job.status = status

            if status in (JobStatus.COMPLETED, JobStatus.FAILED):
                job.completed_at = datetime.now(UTC)
            if result:
                job.result = result
            if error:
                job.error = error

            logger.debug(f"[{job_id}] Status: {old_status.value} -> {status.value}")
            return job

    async def run_job(
        self,
        job_id: str,
        task_fn: Callable[[], Coroutine[Any, Any, DraftResult]],
    ) -> None:
        """Run a job's task in the background."""
        await self.update_job_status(job_id, JobStatus.PROCESSING)

        async def _execute():
            try:
                result = await task_fn()
                await self.update_job_status(job_id, JobStatus.COMPLETED, result=result)
                logger.info(f"[{job_id}] Job completed successfully")
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
