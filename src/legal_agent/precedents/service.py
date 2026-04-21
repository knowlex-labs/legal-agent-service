"""Precedent service: async job orchestration, mirrors summary/synopsis."""

import logging

from legal_agent.clients.s3_client import S3Client
from legal_agent.models.requests import CreatePrecedentJobRequest
from legal_agent.models.responses import JobStatus, JobType
from legal_agent.precedents.generator import PrecedentGenerator
from legal_agent.services.job_manager import JobManager

logger = logging.getLogger(__name__)


class PrecedentService:
    def __init__(
        self,
        generator: PrecedentGenerator,
        job_manager: JobManager,
        s3_client: S3Client,
    ):
        self._generator = generator
        self._job_manager = job_manager
        self._s3_client = s3_client

    async def create_precedent_job(
        self, request: CreatePrecedentJobRequest, user_id: str
    ) -> str:
        """Create a new precedent-finder job and start processing. Returns job_id."""
        job = await self._job_manager.create_job(
            job_type=JobType.PRECEDENT,
            metadata={"case_folder_id": request.case_folder_id, **request.metadata},
            title="Relevant Precedents",
            user_id=user_id,
            legal_case_id=request.case_folder_id,
        )

        logger.info(
            f"Created precedent job {job.job_id}: "
            f"case_folder_id={request.case_folder_id}, user={user_id}"
        )

        async def task() -> None:
            await self._execute(request, job.job_id, user_id)

        await self._job_manager.run_job(job.job_id, task)
        return job.job_id

    async def _execute(
        self, request: CreatePrecedentJobRequest, job_id: str, user_id: str
    ) -> None:
        logger.debug(f"[{job_id}] Starting precedent-finder execution")

        precedents_md = await self._generator.generate(
            file_ids=request.file_ids,
            user_id=user_id,
            top_k=request.top_k,
            model=request.model,
        )

        s3_path = f"{request.case_folder_id}/precedents.md"
        await self._s3_client.upload_text(s3_path, precedents_md)
        logger.info(f"[{job_id}] Precedents uploaded to {s3_path}")

        signed_url = await self._s3_client.signed_url(s3_path)
        await self._job_manager.update_job_status(
            job_id,
            JobStatus.COMPLETED,
            s3_path=s3_path,
            storage_url=signed_url,
            file_name="precedents.md",
            file_type="text/markdown",
            indexing_status="pending",
        )
