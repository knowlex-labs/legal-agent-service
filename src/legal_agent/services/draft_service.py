"""Draft service that orchestrates the document drafting workflow."""

import asyncio
import logging
import re

from legal_agent.agents.drafts.custom.agent import CustomDraftingAgent
from legal_agent.agents.drafts.custom.service import TemplateService
from legal_agent.agents.drafts.anticipatory_bail_agent import AnticipatoryBailAgent
from legal_agent.agents.drafts.application_agent import ApplicationAgent
from legal_agent.agents.drafts.bail_agent import BailApplicationAgent
from legal_agent.agents.drafts.base import BaseDraftingAgent, DraftingDependencies
from legal_agent.agents.drafts.consumer_complaint_agent import ConsumerComplaintAgent
from legal_agent.agents.drafts.contract_agent import ContractAgent
from legal_agent.agents.drafts.court_filing_agent import CourtFilingAgent
from legal_agent.agents.drafts.criminal_appeal_agent import CriminalAppealAgent
from legal_agent.agents.drafts.execution_petition_agent import ExecutionPetitionAgent
from legal_agent.agents.drafts.notice_agent import NoticeAgent
from legal_agent.agents.drafts.patent_agent import PatentAgent
from legal_agent.agents.drafts.quashing_petition_agent import QuashingPetitionAgent
from legal_agent.agents.drafts.revision_petition_agent import RevisionPetitionAgent
from legal_agent.agents.drafts.slp_agent import SLPAgent
from legal_agent.agents.drafts.written_arguments_agent import WrittenArgumentsAgent
from legal_agent.agents.drafts.written_statement_agent import WrittenStatementAgent
from legal_agent.agents.translation.structure_aware_extractor import (
    extract_for_translation,
)
from legal_agent.clients.decryption import DecryptionService
from legal_agent.clients.s3_client import S3Client
from legal_agent.clients.rag_client import RAGClient
from legal_agent.config import Settings
from legal_agent.legal_retrieval.retriever import LegalCaseRetriever
from legal_agent.data.examples_loader import (
    format_as_prompt_section,
    get_examples_for_document_type,
)
from legal_agent.models.documents import DocumentType, GeneratedDocument
from legal_agent.models.requests import CreateDraftJobRequest
from legal_agent.models.responses import JobStatus, JobType
from legal_agent.services.content_preprocessor import (
    assemble_config_text,
    preprocess_and_enhance,
    preprocess_title,
)
from legal_agent.services.job_manager import ErrorStage, JobManager, StagedError

logger = logging.getLogger(__name__)


def _slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    return re.sub(r"[\s_]+", "-", text)


def _markdown_for_upload(document: GeneratedDocument) -> str:
    """Assemble final markdown and run safety post-processing.

    Steps:
    1. Prefer `document.draft` (the agent's raw markdown). Fall back to
       assembling from `document.sections` if `draft` is empty.
    2. Run `apply_draft_postprocess` — masks Aadhaar, warns on any unfilled
       placeholders (`[Amount]`, `XXXX`, etc.) without failing, and asserts
       minimum length. Length is still a hard failure.
    """
    from legal_agent.utils.legal_postprocess import apply_draft_postprocess

    body = (document.draft or "").strip()
    if body:
        return apply_draft_postprocess(document.draft)
    if document.sections:
        ordered = sorted(document.sections, key=lambda s: s.order)
        parts = [f"## {s.title}\n{s.content.strip()}" for s in ordered if s.content and s.content.strip()]
        # Separate major sections with a horizontal rule so the rendered document
        # shows a clear visual break instead of running together.
        assembled = "\n\n---\n\n".join(parts)
        if assembled.strip():
            logger.warning(
                "Structured output had empty draft field; assembled %s sections into markdown (%s chars)",
                len(document.sections),
                len(assembled),
            )
            header = f"# {document.title}\n\n" if (document.title or "").strip() else ""
            return apply_draft_postprocess(header + assembled)
    raise ValueError(
        "Draft generation produced no content: empty `draft` and no usable sections. "
        "Retry or adjust the model / prompt."
    )


class DraftService:
    """Service that orchestrates the document drafting workflow."""

    # Per-job total cap on parsed-upload text injected into the prompt.
    # Sized so a typical Tier 1 LLM context window has plenty of room for
    # the system prompt, examples, and the generated draft itself.
    _UPLOAD_TEXT_CHAR_CAP = 80_000

    def __init__(
        self,
        settings: Settings,
        job_manager: JobManager,
        rag_client: RAGClient,
        s3_client: S3Client,
        retriever: LegalCaseRetriever | None = None,
        template_service: TemplateService | None = None,
        decryption: DecryptionService | None = None,
    ):
        self.settings = settings
        self.job_manager = job_manager
        self.rag_client = rag_client
        self.s3_client = s3_client
        self.retriever = retriever
        self.template_service = template_service
        self.decryption = decryption

        # Mapping of document type -> agent class. Used to instantiate one-off
        # agents when the request overrides the default model.
        self._agent_classes: dict[DocumentType, type[BaseDraftingAgent]] = {
            DocumentType.CONTRACT: ContractAgent,
            DocumentType.AGREEMENT: ContractAgent,
            DocumentType.LEGAL_NOTICE: NoticeAgent,
            DocumentType.DEMAND_NOTICE: NoticeAgent,
            DocumentType.PETITION: CourtFilingAgent,
            DocumentType.AFFIDAVIT: CourtFilingAgent,
            DocumentType.APPLICATION: CourtFilingAgent,
            DocumentType.BAIL_APPLICATION: BailApplicationAgent,
            DocumentType.CRIMINAL_APPEAL: CriminalAppealAgent,
            DocumentType.SLP: SLPAgent,
            DocumentType.QUASHING_PETITION: QuashingPetitionAgent,
            DocumentType.ANTICIPATORY_BAIL: AnticipatoryBailAgent,
            DocumentType.REVISION_PETITION: RevisionPetitionAgent,
            DocumentType.EXECUTION_PETITION: ExecutionPetitionAgent,
            DocumentType.CONSUMER_COMPLAINT: ConsumerComplaintAgent,
            DocumentType.PATENT: PatentAgent,
            DocumentType.WRITTEN_STATEMENT: WrittenStatementAgent,
            DocumentType.WRITTEN_ARGUMENTS: WrittenArgumentsAgent,
            DocumentType.APPLICATION_DRAFT: ApplicationAgent,
        }

        # Default agents, initialized once with the configured model.
        model, provider = self._get_model_config()
        self._agents: dict[DocumentType, BaseDraftingAgent] = {
            doc_type: cls(model, provider)
            for doc_type, cls in self._agent_classes.items()
        }

    def _get_model_config(self) -> tuple[str, str]:
        return self.settings.llm_model, self.settings.get_langchain_provider()

    def _get_enhance_model_string(self) -> str:
        provider = self.settings.llm_provider
        fast_models = {
            "openai": "gpt-4o-mini",
            "anthropic": "claude-3-5-haiku-latest",
            "gemini": "gemini-2.0-flash",
        }
        model = fast_models.get(provider, "gpt-4o-mini")
        return f"{provider}:{model}"

    def _get_agent(self, document_type: DocumentType) -> BaseDraftingAgent:
        if document_type not in self._agents:
            raise ValueError(f"Unsupported document type: {document_type}. Supported: {[d.value for d in self._agents.keys()]}")
        return self._agents[document_type]

    def _resolve_provider(self, model: str) -> str:
        """Infer the LangChain provider from a model ID."""
        lower = model.lower()
        if lower.startswith("gemini"):
            return "google-genai"
        if lower.startswith("claude"):
            return "anthropic"
        if lower.startswith("gpt") or lower.startswith("o"):
            return "openai"
        # Fallback to configured provider.
        return self.settings.get_langchain_provider()

    def _build_agent(self, document_type: DocumentType, model: str) -> BaseDraftingAgent:
        """Instantiate a one-off agent with the given model (for per-request overrides)."""
        if document_type not in self._agent_classes:
            raise ValueError(
                f"Unsupported document type: {document_type}. "
                f"Supported: {[d.value for d in self._agent_classes.keys()]}"
            )
        agent_cls = self._agent_classes[document_type]
        provider = self._resolve_provider(model)
        return agent_cls(model, provider)

    async def _fetch_and_parse_uploads(
        self, file_ids: list[str], user_id: str, job_id: str
    ) -> str | None:
        """Download + decrypt + parse user-uploaded source PDFs for prompt context.

        Replaces the semantic-RAG path for drafting: top_k retrieval on a
        generic query loses content from short uploaded drafts, and frequently
        the upload hasn't finished indexing when the job fires. Direct
        text extraction sidesteps both problems.
        """
        if not file_ids:
            return None
        if not self.decryption:
            logger.warning(
                f"[{job_id}] Cannot fetch uploaded docs — DecryptionService not configured "
                f"(DOCUMENT_ENCRYPTION_MASTER_KEY missing). Falling back to RAG."
            )
            return None

        parts: list[str] = []
        total = 0
        cap = self._UPLOAD_TEXT_CHAR_CAP
        truncated = False
        for file_id in file_ids:
            try:
                encrypted = await self.s3_client.download_bytes(file_id)
                plaintext = await asyncio.to_thread(
                    self.decryption.decrypt_file, encrypted, user_id
                )
                filename = file_id.rsplit("/", 1)[-1]
                text, _ledger = await asyncio.to_thread(
                    extract_for_translation, plaintext, filename, None
                )
            except Exception as exc:
                logger.warning(
                    f"[{job_id}] Failed to fetch/parse upload {file_id}: {exc}"
                )
                continue

            if not text or not text.strip():
                continue

            header = f"\n\n--- {filename} ---\n"
            if total + len(header) + len(text) > cap:
                remaining = cap - total - len(header)
                if remaining > 200:  # only worth including if a usable slice fits
                    parts.append(header)
                    parts.append(text[:remaining])
                    total = cap
                truncated = True
                break

            parts.append(header)
            parts.append(text)
            total += len(header) + len(text)

        if truncated:
            logger.warning(
                f"[{job_id}] Truncated upload context to {cap} chars "
                f"(across {len(file_ids)} file(s))"
            )

        merged = "".join(parts).strip()
        return merged or None

    async def create_draft_job(self, request: CreateDraftJobRequest, user_id: str) -> str:
        """Create a new draft job and start processing. Returns job_id."""
        job = await self.job_manager.create_job(
            job_type=JobType.DRAFT,
            metadata={
                "case_folder_id": request.case_folder_id,
                "document_type": request.document_type.value,
                "file_ids": request.file_ids,
                **request.metadata,
            },
            title=request.title,
            user_id=user_id,
            legal_case_id=request.case_folder_id,
            subtype=request.metadata.get("subtype"),
        )

        logger.info(
            f"Created draft job {job.job_id}: "
            f"type={request.document_type.value}, "
            f"title='{request.title}', "
            f"user={user_id}, "
            f"case_folder_id={request.case_folder_id}"
        )

        async def draft_task() -> str:
            return await self._execute_draft(request, job.job_id, user_id)

        await self.job_manager.run_job(job.job_id, draft_task)
        return job.job_id

    async def _execute_draft(
        self, request: CreateDraftJobRequest, job_id: str, user_id: str
    ) -> str:
        """Execute the drafting task. Returns s3_path."""
        logger.debug(f"[{job_id}] Starting draft execution")

        # Step 0: Pre-fetch uploaded docs as full text so the agent can inject
        # them deterministically (Bug 1 — semantic RAG was losing content).
        uploaded_doc_text: str | None = None
        if request.file_ids:
            uploaded_doc_text = await self._fetch_and_parse_uploads(
                request.file_ids, user_id, job_id
            )
            if uploaded_doc_text:
                logger.info(
                    f"[{job_id}] Injected uploaded doc text: "
                    f"{len(uploaded_doc_text)} chars from {len(request.file_ids)} file(s)"
                )

        # Step 1: Preprocess + enhance content
        cleaned_title = preprocess_title(request.title)

        if request.config:
            raw_instructions = assemble_config_text(
                request.config, request.document_type.value
            )
        else:
            if request.body is None:
                raise ValueError("body is required when config is not provided")
            raw_instructions = request.body

        language = request.language.value
        enhance_model = self._get_enhance_model_string()
        cleaned_instructions = await preprocess_and_enhance(
            raw_instructions,
            document_type=request.document_type.value,
            model=enhance_model,
            language=language,
        )
        logger.debug(f"[{job_id}] Content preprocessed and enhanced")

        # Step 2: Load few-shot examples
        examples_data = get_examples_for_document_type(
            request.document_type.value,
            subtype=request.metadata.get("subtype"),
            language=language,
        )
        formatted_examples = format_as_prompt_section(examples_data)
        logger.debug(f"[{job_id}] Examples loaded: {len(formatted_examples)} chars")

        if request.template_id and self.template_service:
            template = await self.template_service.get_template(request.template_id, user_id)
            if request.model:
                model, provider = request.model, self._resolve_provider(request.model)
            else:
                model, provider = self._get_model_config()
            agent = CustomDraftingAgent(model, provider, template.generated_prompt)
        elif request.model:
            agent = self._build_agent(request.document_type, request.model)
        else:
            agent = self._get_agent(request.document_type)
        logger.debug(
            f"[{job_id}] Using agent: {agent.__class__.__name__} "
            f"(model={agent.model_name}, provider={agent.provider})"
        )

        # Step 3: Create dependencies and call agent
        sub_type = request.metadata.get("subtype")
        deps = DraftingDependencies(
            rag_client=self.rag_client,
            file_ids=request.file_ids,
            user_id=user_id,
            title=cleaned_title,
            instructions=cleaned_instructions,
            document_type=request.document_type,
            examples=formatted_examples,
            language=language,
            retriever=self.retriever,
            sub_type=sub_type,
            uploaded_doc_text=uploaded_doc_text,
        )

        logger.debug(f"[{job_id}] Calling agent.draft()")
        try:
            document = await agent.draft(deps)
        except Exception as exc:
            raise StagedError(ErrorStage.DRAFTING, exc) from exc

        try:
            markdown_body = _markdown_for_upload(document)
        except Exception as exc:
            # Placeholder / Aadhaar / length checks live here — surface them
            # as POSTPROCESS so a failure says `[POSTPROCESS] unfilled placeholder`
            # rather than a generic drafting error.
            raise StagedError(ErrorStage.POSTPROCESS, exc) from exc
        logger.info(
            f"[{job_id}] Draft completed: {len(document.sections)} sections, markdown_len={len(markdown_body)}"
        )

        # Step 4: Upload to S3
        # Use request title for proper naming (user-provided title)
        slug = _slugify(request.title)
        s3_path = f"{request.case_folder_id}/drafts/{slug}.md"
        try:
            await self.s3_client.upload_text(s3_path, markdown_body)
        except Exception as exc:
            raise StagedError(ErrorStage.UPLOAD, exc) from exc

        # Get signed URL and update job with file details
        signed_url = await self.s3_client.signed_url(s3_path)
        file_name = f"{slug}.md"
        await self.job_manager.update_job_status(
            job_id,
            JobStatus.COMPLETED,
            s3_path=s3_path,
            storage_url=signed_url,
            file_name=file_name,
            file_type="text/markdown",
            indexing_status="pending",
        )

        return s3_path
