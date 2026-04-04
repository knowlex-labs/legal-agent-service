"""Translation service that orchestrates legal document translation and S3 upload."""

import logging

from legal_agent.clients.rag_client import RAGClient
from legal_agent.clients.s3_client import S3Client
from legal_agent.models.requests import CreateTranslationJobRequest
from legal_agent.models.responses import JobStatus, JobType
from legal_agent.services.job_manager import JobManager
from legal_agent.agents.translation.generator import TranslationGenerator

logger = logging.getLogger(__name__)


class TranslationService:
    def __init__(
        self,
        generator: TranslationGenerator,
        job_manager: JobManager,
        s3_client: S3Client,
        rag_client: RAGClient,
    ):
        self._generator = generator
        self._job_manager = job_manager
        self._s3_client = s3_client
        self._rag_client = rag_client

    async def create_translation_job(
        self, request: CreateTranslationJobRequest, user_id: str
    ) -> str:
        """Create a new translation job and start processing. Returns job_id."""
        job = await self._job_manager.create_job(
            job_type=JobType.TRANSLATION,
            metadata={
                "case_folder_id": request.case_folder_id,
                "target_language": request.target_language.value,
                "source_language": request.source_language.value if request.source_language else None,
                **request.metadata,
            },
            title=f"Translation to {request.target_language.value}",
            user_id=user_id,
            legal_case_id=request.case_folder_id,
        )

        logger.info(
            f"Created translation job {job.job_id}: "
            f"target={request.target_language.value}, user={user_id}"
        )

        async def task() -> None:
            await self._execute_translation(request, job.job_id, user_id)

        await self._job_manager.run_job(job.job_id, task)
        return job.job_id

    async def _execute_translation(
        self, request: CreateTranslationJobRequest, job_id: str, user_id: str
    ) -> None:
        """Execute the translation task."""
        logger.debug(f"[{job_id}] Starting translation execution")

        source_text = await self._resolve_source_text(request, user_id)

        translated_md = await self._generator.generate(
            source_text=source_text,
            target_language=request.target_language,
            source_language=request.source_language,
        )

        lang_slug = request.target_language.value
        s3_path = f"{request.case_folder_id}/translations/{lang_slug}-translation.md"
        await self._s3_client.upload_text(s3_path, translated_md)
        logger.info(f"[{job_id}] Translation uploaded to {s3_path}")

        signed_url = await self._s3_client.signed_url(s3_path)
        await self._job_manager.update_job_status(
            job_id,
            JobStatus.COMPLETED,
            s3_path=s3_path,
            storage_url=signed_url,
            file_name=f"{lang_slug}-translation.md",
            file_type="text/markdown",
            indexing_status="pending",
        )

    async def _resolve_source_text(
        self, request: CreateTranslationJobRequest, user_id: str
    ) -> str:
        """Get source document text from content field or RAG file_id."""
        if request.content:
            return request.content

        if request.file_id:
            text = await self._rag_client.query(
                file_ids=[request.file_id],
                query="Return the full text content of this document",
                user_id=user_id,
            )
            if not text:
                raise ValueError(f"No content retrieved for file_id={request.file_id}")
            return text

        raise ValueError("Either content or file_id must be provided")
