"""Synopsis service that orchestrates case synopsis generation and S3 upload."""

import logging

from legal_agent.clients.s3_client import S3Client
from legal_agent.models.requests import CreateSynopsisJobRequest
from legal_agent.models.responses import JobStatus, JobType
from legal_agent.services.job_manager import JobManager
from legal_agent.synopsis.generator import SynopsisGenerator

logger = logging.getLogger(__name__)


class SynopsisService:
    def __init__(
        self,
        generator: SynopsisGenerator,
        job_manager: JobManager,
        s3_client: S3Client,
    ):
        self._generator = generator
        self._job_manager = job_manager
        self._s3_client = s3_client

    async def create_synopsis_job(
        self, request: CreateSynopsisJobRequest, user_id: str
    ) -> str:
        """Create a new synopsis job and start processing. Returns job_id."""
        job = await self._job_manager.create_job(
            job_type=JobType.SYNOPSIS,
            metadata={"case_folder_id": request.case_folder_id, **request.metadata},
            title="Case Synopsis",
            user_id=user_id,
            legal_case_id=request.case_folder_id,
        )

        logger.info(
            f"Created synopsis job {job.job_id}: "
            f"case_folder_id={request.case_folder_id}, user={user_id}"
        )

        async def task() -> None:
            await self._execute_synopsis(request, job.job_id, user_id)

        await self._job_manager.run_job(job.job_id, task)
        return job.job_id

    async def _execute_synopsis(
        self, request: CreateSynopsisJobRequest, job_id: str, user_id: str
    ) -> None:
        """Execute the synopsis generation task."""
        logger.debug(f"[{job_id}] Starting synopsis execution")

        synopsis_md = await self._generator.generate(
            file_ids=request.file_ids,
            user_id=user_id,
            model=request.model,
        )

        s3_path = f"{request.case_folder_id}/synopsis.md"
        await self._s3_client.upload_text(s3_path, synopsis_md)
        logger.info(f"[{job_id}] Synopsis uploaded to {s3_path}")

        signed_url = await self._s3_client.signed_url(s3_path)
        await self._job_manager.update_job_status(
            job_id,
            JobStatus.COMPLETED,
            s3_path=s3_path,
            storage_url=signed_url,
            file_name="synopsis.md",
            file_type="text/markdown",
            indexing_status="pending",
        )