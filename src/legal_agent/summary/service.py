"""Summary service that orchestrates case summary generation and S3 upload."""

import logging

from legal_agent.clients.s3_client import S3Client
from legal_agent.models.requests import CreateSummaryJobRequest
from legal_agent.models.responses import JobType
from legal_agent.services.job_manager import JobManager
from legal_agent.summary.generator import SummaryGenerator

logger = logging.getLogger(__name__)


class SummaryService:
    def __init__(
        self,
        generator: SummaryGenerator,
        job_manager: JobManager,
        s3_client: S3Client,
    ):
        self._generator = generator
        self._job_manager = job_manager
        self._s3_client = s3_client

    async def create_summary_job(
        self, request: CreateSummaryJobRequest, user_id: str
    ) -> str:
        """Create a new summary job and start processing. Returns job_id."""
        job = await self._job_manager.create_job(
            job_type=JobType.SUMMARY,
            metadata={"case_folder_id": request.case_folder_id, **request.metadata},
        )

        logger.info(
            f"Created summary job {job.job_id}: "
            f"case_folder_id={request.case_folder_id}, user={user_id}"
        )

        async def task() -> str:
            return await self._execute_summary(request, job.job_id, user_id)

        await self._job_manager.run_job(job.job_id, task)
        return job.job_id

    async def _execute_summary(
        self, request: CreateSummaryJobRequest, job_id: str, user_id: str
    ) -> str:
        """Execute the summary generation task. Returns s3_path."""
        logger.debug(f"[{job_id}] Starting summary execution")

        summary_md = await self._generator.generate(
            file_ids=request.file_ids,
            user_id=user_id,
            drafts=request.drafts,
            chat_highlights=request.chat_highlights,
            model=request.model,
        )

        s3_path = f"{request.case_folder_id}/summary.md"
        await self._s3_client.upload_text(s3_path, summary_md)
        logger.info(f"[{job_id}] Summary uploaded to {s3_path}")

        return s3_path
