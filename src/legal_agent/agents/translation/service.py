"""Translation service — dispatches to v1 (PyMuPDF flow HTML) or v2 (Gemini vision)."""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any

from legal_agent.clients.decryption import DecryptionService
from legal_agent.clients.s3_client import S3Client
from legal_agent.config import get_settings
from legal_agent.models.requests import CreateTranslationJobRequest
from legal_agent.models.responses import JobStatus, JobType
from legal_agent.services.job_manager import ErrorStage, JobManager, StagedError

logger = logging.getLogger(__name__)

_IMAGE_EXTS: dict[str, str] = {
    "png": "png",
    "jpg": "jpeg",
    "jpeg": "jpeg",
    "tif": "tiff",
    "tiff": "tiff",
    "webp": "webp",
    "bmp": "bmp",
    "gif": "gif",
}


def _image_bytes_to_pdf(image_bytes: bytes, ext: str) -> bytes:
    import fitz

    ft = _IMAGE_EXTS[ext.lower()]
    img_doc = fitz.open(stream=image_bytes, filetype=ft)
    try:
        return img_doc.convert_to_pdf()
    finally:
        img_doc.close()


def _dump_debug(debug_dir: str | None, job_id: str, name: str, content: str | bytes) -> None:
    if not debug_dir:
        return
    try:
        d = Path(debug_dir) / job_id
        d.mkdir(parents=True, exist_ok=True)
        suffix = ".pdf" if isinstance(content, bytes) else ".html"
        path = d / f"{name}{suffix}"
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8")
        logger.info("[%s] debug → %s", job_id, path)
    except Exception as exc:
        logger.warning("[%s] debug dump failed (%s): %s", job_id, name, exc)


class TranslationService:
    def __init__(
        self,
        job_manager: JobManager,
        s3_client: S3Client,
        decryption: DecryptionService | None,
    ):
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
                "source_language": request.source_language.value
                if request.source_language
                else None,
                "file_id": request.file_id,
                **request.metadata,
            },
            title=title,
            user_id=user_id,
            legal_case_id=case_folder_id,
        )

        logger.info(
            "Created translation job %s: target=%s, user=%s",
            job.job_id,
            request.target_language.value,
            user_id,
        )

        async def task() -> None:
            await self._execute_translation(request, job.job_id, user_id)

        await self._job_manager.run_job(job.job_id, task)
        return job.job_id

    async def _execute_translation(
        self, request: CreateTranslationJobRequest, job_id: str, user_id: str
    ) -> None:
        settings = get_settings()
        debug_dir = getattr(settings, "translation_debug_dir", None)

        if not request.file_id:
            raise StagedError(
                ErrorStage.EXTRACTION,
                RuntimeError("file_id is required for translation"),
            )

        try:
            t_resolve = time.perf_counter()
            source_bytes, filename = await self._resolve_source_bytes(request, user_id)
            logger.info(
                "[%s] source resolve/decrypt took %.2fs", job_id, time.perf_counter() - t_resolve
            )
        except StagedError:
            raise
        except Exception as exc:
            raise StagedError(ErrorStage.EXTRACTION, exc) from exc

        fl = filename.lower()
        ext = fl.rsplit(".", 1)[-1] if "." in fl else ""
        if ext in _IMAGE_EXTS:
            try:
                t_convert = time.perf_counter()
                source_bytes = await asyncio.to_thread(_image_bytes_to_pdf, source_bytes, ext)
                logger.info(
                    "[%s] image→PDF conversion took %.2fs", job_id, time.perf_counter() - t_convert
                )
            except Exception as exc:
                raise StagedError(ErrorStage.EXTRACTION, exc) from exc
            filename = filename.rsplit(".", 1)[0] + ".pdf"
            logger.info("[%s] converted %s image → PDF (%d bytes)", job_id, ext, len(source_bytes))
        elif ext != "pdf":
            raise StagedError(
                ErrorStage.EXTRACTION,
                RuntimeError(
                    f"Unsupported file type: {filename} (supported: pdf, {', '.join(_IMAGE_EXTS)})"
                ),
            )

        # Per-request override wins over the settings default.
        pipeline = getattr(request, "translation_pipeline", None) or get_settings().translation_pipeline
        if pipeline == "v3":
            await self._execute_html_translation_v3(
                request, job_id, source_bytes, filename, debug_dir
            )
        elif pipeline == "v2":
            await self._execute_html_translation_v2(
                request, job_id, source_bytes, filename, debug_dir
            )
        else:
            await self._execute_html_translation(request, job_id, source_bytes, filename, debug_dir)

    async def _execute_html_translation(
        self,
        request: CreateTranslationJobRequest,
        job_id: str,
        source_bytes: bytes,
        filename: str,
        debug_dir: str | None,
    ) -> None:
        """PyMuPDF positioned HTML -> Sarvam -> Playwright PDF -> S3 upload."""
        from legal_agent.agents.translation.html_pdf_translator import translate_pdf_via_html

        try:
            t_translate = time.perf_counter()
            pdf_bytes, html_meta = await translate_pdf_via_html(
                source_bytes, filename, request, job_id, debug_dir
            )
            logger.info(
                "[%s] translation pipeline took %.2fs", job_id, time.perf_counter() - t_translate
            )
        except Exception as exc:
            raise StagedError(ErrorStage.TRANSLATION, exc) from exc

        meta = {
            "extraction_route": html_meta.get("translation_pipeline", "pymupdf_html"),
            **html_meta,
        }
        await self._upload_translated_pdf(request, job_id, pdf_bytes, meta)

    async def _execute_html_translation_v2(
        self,
        request: CreateTranslationJobRequest,
        job_id: str,
        source_bytes: bytes,
        filename: str,
        debug_dir: str | None,
    ) -> None:
        """Gemini 2.5 Pro vision → per-page HTML → Playwright PDF → S3 upload."""
        from legal_agent.agents.translation_v2.pipeline import translate_pdf_v2

        try:
            t_translate = time.perf_counter()
            pdf_bytes, v2_meta = await translate_pdf_v2(
                source_bytes, filename, request, job_id, debug_dir
            )
            logger.info(
                "[%s] translation v2 pipeline took %.2fs", job_id, time.perf_counter() - t_translate
            )
        except StagedError:
            raise
        except Exception as exc:
            raise StagedError(ErrorStage.TRANSLATION, exc) from exc

        meta = {
            "extraction_route": v2_meta.get("extraction_route", "v2_gemini_html"),
            **v2_meta,
        }
        await self._upload_translated_pdf(request, job_id, pdf_bytes, meta)

    async def _execute_html_translation_v3(
        self,
        request: CreateTranslationJobRequest,
        job_id: str,
        source_bytes: bytes,
        filename: str,
        debug_dir: str | None,
    ) -> None:
        """Azure Document Intelligence → Haiku/Sarvam → per-page HTML → PDF → S3."""
        from legal_agent.agents.translation_v3.pipeline import translate_pdf_v3

        try:
            t_translate = time.perf_counter()
            pdf_bytes, v3_meta = await translate_pdf_v3(
                source_bytes, filename, request, job_id, debug_dir
            )
            logger.info(
                "[%s] translation v3 pipeline took %.2fs", job_id, time.perf_counter() - t_translate
            )
        except StagedError:
            raise
        except Exception as exc:
            raise StagedError(ErrorStage.TRANSLATION, exc) from exc

        meta = {
            "extraction_route": v3_meta.get("extraction_route", "v3_azure_html"),
            **v3_meta,
        }
        await self._upload_translated_pdf(request, job_id, pdf_bytes, meta)

    async def _upload_translated_pdf(
        self,
        request: CreateTranslationJobRequest,
        job_id: str,
        pdf_bytes: bytes,
        meta: dict[str, Any],
    ) -> None:
        """Common upload + status update path shared by v1 and v2."""
        lang_slug = request.target_language.value
        folder = request.case_folder_id or _case_folder_from_file_id(request.file_id)
        original_name = _strip_extension(request.file_name)
        lang_suffix = lang_slug.replace("_", " ").replace("-", " ").title().replace(" ", "_")
        out_name = (
            f"{original_name}_{lang_suffix}.pdf"
            if original_name
            else f"{lang_suffix}_translation.pdf"
        )

        meta = {
            "detected_document_type": request.document_type.value
            if request.document_type
            else None,
            **meta,
        }
        await self._job_manager.update_job_metadata(job_id, **meta)

        s3_path = f"{folder}/translations/{out_name}"
        try:
            t_upload = time.perf_counter()
            await self._s3_client.upload_bytes(s3_path, pdf_bytes, content_type="application/pdf")
            logger.info("[%s] upload took %.2fs", job_id, time.perf_counter() - t_upload)
        except Exception as exc:
            raise StagedError(ErrorStage.UPLOAD, exc) from exc
        logger.info("[%s] Translation PDF uploaded to %s", job_id, s3_path)

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

    async def _resolve_source_bytes(
        self, request: CreateTranslationJobRequest, user_id: str
    ) -> tuple[bytes, str]:
        if not request.file_id:
            raise ValueError("file_id required")
        if not self._decryption:
            raise ValueError(
                "Document decryption is not configured (DOCUMENT_ENCRYPTION_MASTER_KEY missing)"
            )

        logger.info("Downloading encrypted file from S3: %s", request.file_id)
        try:
            encrypted = await self._s3_client.download_bytes(request.file_id)
        except Exception as exc:
            raise StagedError(ErrorStage.EXTRACTION, exc) from exc

        try:
            plaintext = await asyncio.to_thread(self._decryption.decrypt_file, encrypted, user_id)
        except Exception as exc:
            raise StagedError(ErrorStage.EXTRACTION, exc) from exc

        filename = request.file_name or request.file_id.rsplit("/", 1)[-1] or "document.pdf"
        logger.info("Decrypted %d bytes from %s", len(plaintext), filename)
        return plaintext, filename


def _strip_extension(file_name: str | None) -> str | None:
    if not file_name:
        return None
    if "." in file_name:
        return file_name.rsplit(".", 1)[0]
    return file_name


def _case_folder_from_file_id(file_id: str | None) -> str:
    if not file_id:
        return "translations"
    parts = file_id.split("/")
    if len(parts) >= 2:
        return "/".join(parts[:2])
    return parts[0]
