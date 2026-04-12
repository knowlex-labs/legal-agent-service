"""Translation service that orchestrates legal document translation and S3 upload."""

import asyncio
import logging

from legal_agent.agents.drafts.custom.extractor import extract_text_from_bytes
from legal_agent.agents.translation.pdf_builder import markdown_to_pdf
from legal_agent.clients.decryption import DecryptionService
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
        decryption: DecryptionService,
    ):
        self._generator = generator
        self._job_manager = job_manager
        self._s3_client = s3_client
        self._decryption = decryption

    async def create_translation_job(
        self, request: CreateTranslationJobRequest, user_id: str
    ) -> str:
        """Create a new translation job and start processing. Returns job_id."""
        case_folder_id = request.case_folder_id or _case_folder_from_file_id(request.file_id)
        original_name = _strip_extension(request.file_name)
        lang = request.target_language.value
        title = f"{original_name} - ({lang})" if original_name else f"Translation ({lang})"
        job = await self._job_manager.create_job(
            job_type=JobType.TRANSLATION,
            metadata={
                "case_folder_id": case_folder_id,
                "target_language": lang,
                "source_language": request.source_language.value if request.source_language else None,
                "file_id": request.file_id,
                **request.metadata,
            },
            title=title,
            user_id=user_id,
            legal_case_id=case_folder_id,
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
            model=request.model,
        )

        lang_slug = request.target_language.value
        folder = request.case_folder_id or _case_folder_from_file_id(request.file_id)
        original_name = _strip_extension(request.file_name)
        out_name = f"{original_name} - ({lang_slug}).pdf" if original_name else f"{lang_slug}-translation.pdf"

        # Build PDF from the translated markdown
        pdf_bytes = await asyncio.to_thread(markdown_to_pdf, translated_md)
        s3_path = f"{folder}/translations/{out_name}"
        await self._s3_client.upload_bytes(
            s3_path, pdf_bytes,
            content_type="application/pdf",
        )
        logger.info(f"[{job_id}] Translation PDF uploaded to {s3_path}")

        signed_url = await self._s3_client.signed_url(s3_path)
        await self._job_manager.update_job_status(
            job_id,
            JobStatus.COMPLETED,
            s3_path=s3_path,
            storage_url=signed_url,
            file_name=out_name,
            file_type="application/pdf",
            indexing_status="pending",
        )

    async def _resolve_source_text(
        self, request: CreateTranslationJobRequest, user_id: str
    ) -> str:
        """Get source document text from content or by decrypting an S3 file."""
        if request.content and request.content.strip():
            return request.content

        if request.file_id:
            logger.info(f"Downloading encrypted file from S3: {request.file_id}")
            encrypted = await self._s3_client.download_bytes(request.file_id)
            logger.info(f"Downloaded {len(encrypted)} bytes, decrypting...")
            plaintext = await asyncio.to_thread(
                self._decryption.decrypt_file, encrypted, user_id
            )
            logger.info(f"Decrypted to {len(plaintext)} bytes, extracting text...")
            filename = request.file_id.rsplit("/", 1)[-1]
            text = extract_text_from_bytes(plaintext, filename)
            logger.info(f"Extracted {len(text)} chars from {filename}")
            return text

        raise ValueError("Either content or file_id must be provided")


def _strip_extension(file_name: str | None) -> str | None:
    """Remove file extension from a filename. 'doc.pdf' → 'doc'."""
    if not file_name:
        return None
    if "." in file_name:
        return file_name.rsplit(".", 1)[0]
    return file_name


def _case_folder_from_file_id(file_id: str | None) -> str:
    """Extract case folder from file_id path: {user_id}/{case_id}/{doc}.pdf → {user_id}/{case_id}"""
    if not file_id:
        return "translations"
    parts = file_id.split("/")
    if len(parts) >= 2:
        return "/".join(parts[:2])
    return parts[0]
