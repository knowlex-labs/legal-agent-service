"""Translation service that orchestrates legal document translation and S3 upload."""

import asyncio
import logging

from legal_agent.agents.translation.doc_profiles import (
    classify_document,
    resolve_profile,
)
from legal_agent.agents.translation.pdf_builder import markdown_to_pdf
from legal_agent.agents.translation.render_guard import (
    has_critical,
    summarize,
    validate_rendered_pdf,
)
from legal_agent.agents.translation.structure_aware_extractor import (
    LedgerEntry,
    enhance_with_llm_structure,
    extract_for_translation,
)
from legal_agent.clients.decryption import DecryptionService
from legal_agent.clients.s3_client import S3Client
from legal_agent.models.documents import DocumentType
from legal_agent.models.requests import CreateTranslationJobRequest
from legal_agent.models.responses import JobStatus, JobType
from legal_agent.services.job_manager import ErrorStage, JobManager, StagedError
from legal_agent.agents.translation.generator import TranslationGenerator

logger = logging.getLogger(__name__)


class TranslationService:
    def __init__(
        self,
        generator: TranslationGenerator,
        job_manager: JobManager,
        s3_client: S3Client,
        decryption: DecryptionService | None,
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
        """Execute the translation task.

        Each stage is wrapped so failures are labelled with the specific
        pipeline step that broke. The job manager reads `StagedError` and
        produces `[STAGE] reason` in the job record — frontend can key off
        stage for user-friendly error messages.
        """
        logger.debug(f"[{job_id}] Starting translation execution")

        try:
            source_text, ledger = await self._resolve_source_text(request, user_id)
        except StagedError:
            raise  # already tagged by _resolve_source_text
        except Exception as exc:
            raise StagedError(ErrorStage.EXTRACTION, exc) from exc

        # Doc-type pivot: caller override > auto-detect > default profile.
        document_type: DocumentType | None = request.document_type
        if document_type is None and request.auto_detect_document_type:
            try:
                document_type = await classify_document(source_text)
            except Exception as exc:
                raise StagedError(ErrorStage.CLASSIFICATION, exc) from exc
            if document_type is not None:
                logger.info(f"[{job_id}] Auto-detected document_type={document_type.value}")
        profile = resolve_profile(document_type)
        await self._job_manager.update_job_metadata(
            job_id,
            detected_document_type=document_type.value if document_type else None,
            layout_family=profile.layout_family,
        )

        # Optional LLM structure pass — court_filing family + short docs only.
        try:
            source_text, structure_ledger = await enhance_with_llm_structure(
                source_text, document_type
            )
        except Exception as exc:
            raise StagedError(ErrorStage.STRUCTURE, exc) from exc
        if structure_ledger:
            ledger.extend(structure_ledger)

        try:
            translated_md = await self._generator.generate(
                source_text=source_text,
                target_language=request.target_language,
                source_language=request.source_language,
                model=request.model,
                profile=profile,
            )
        except Exception as exc:
            raise StagedError(ErrorStage.TRANSLATION, exc) from exc

        lang_slug = request.target_language.value
        folder = request.case_folder_id or _case_folder_from_file_id(request.file_id)
        original_name = _strip_extension(request.file_name)
        out_name = f"{original_name} - ({lang_slug}).pdf" if original_name else f"{lang_slug}-translation.pdf"

        try:
            pdf_bytes = await asyncio.to_thread(
                markdown_to_pdf, translated_md, lang_slug, profile
            )
        except Exception as exc:
            raise StagedError(ErrorStage.PDF_RENDER, exc) from exc

        # Render guard — catch tofu, ledger drops, page-count surprises.
        try:
            guard_warnings = await asyncio.to_thread(
                validate_rendered_pdf,
                pdf_bytes,
                lang_slug,
                len(source_text),
                ledger,
            )
        except Exception as exc:
            raise StagedError(ErrorStage.RENDER_GUARD, exc) from exc

        await self._job_manager.update_job_metadata(
            job_id,
            ledger_entry_count=len(ledger) or None,
            render_warnings=summarize(guard_warnings) or None,
        )

        if has_critical(guard_warnings):
            critical_msgs = "; ".join(
                f"[{w.code}] {w.message}" for w in guard_warnings if w.severity == "critical"
            )
            raise StagedError(
                ErrorStage.RENDER_GUARD,
                RuntimeError(critical_msgs or "Render guard found critical issues"),
            )

        s3_path = f"{folder}/translations/{out_name}"
        try:
            await self._s3_client.upload_bytes(
                s3_path, pdf_bytes,
                content_type="application/pdf",
            )
        except Exception as exc:
            raise StagedError(ErrorStage.UPLOAD, exc) from exc
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
    ) -> tuple[str, list[LedgerEntry]]:
        """Get source document text + ledger from content or by decrypting an S3 file.

        Stage-tags errors so the job manager produces `[STAGE] reason` output.
        Ledger is empty for raw `content` payloads and for the conservative
        extraction path; only the optional LLM structure pass populates it.
        """
        if request.content and request.content.strip():
            return request.content, []

        if request.file_id:
            if not self._decryption:
                raise ValueError("Document decryption is not configured (DOCUMENT_ENCRYPTION_MASTER_KEY missing)")
            decryption = self._decryption
            logger.info(f"Downloading encrypted file from S3: {request.file_id}")
            try:
                encrypted = await self._s3_client.download_bytes(request.file_id)
            except Exception as exc:
                raise StagedError(ErrorStage.EXTRACTION, exc) from exc
            logger.info(f"Downloaded {len(encrypted)} bytes, decrypting...")
            try:
                plaintext = await asyncio.to_thread(
                    decryption.decrypt_file, encrypted, user_id
                )
            except Exception as exc:
                raise StagedError(ErrorStage.EXTRACTION, exc) from exc
            logger.info(f"Decrypted to {len(plaintext)} bytes, extracting text...")
            filename = request.file_id.rsplit("/", 1)[-1]
            try:
                text, ledger = await asyncio.to_thread(
                    extract_for_translation, plaintext, filename, request.document_type
                )
            except Exception as exc:
                stage = ErrorStage.OCR if "ocr" in str(exc).lower() or "gemini" in str(exc).lower() or "sarvam" in str(exc).lower() else ErrorStage.EXTRACTION
                raise StagedError(stage, exc) from exc
            logger.info(f"Extracted {len(text)} chars from {filename}")
            return text, ledger

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
