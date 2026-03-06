"""Draft service that orchestrates the document drafting workflow."""

import logging
import re

from legal_agent.agents.bail_agent import BailApplicationAgent
from legal_agent.agents.base import BaseDraftingAgent, DraftingDependencies
from legal_agent.agents.contract_agent import ContractAgent
from legal_agent.agents.court_filing_agent import CourtFilingAgent
from legal_agent.agents.criminal_appeal_agent import CriminalAppealAgent
from legal_agent.agents.notice_agent import NoticeAgent
from legal_agent.clients.s3_client import S3Client
from legal_agent.clients.rag_client import RAGClient
from legal_agent.config import Settings
from legal_agent.legal_retrieval.retriever import LegalCaseRetriever
from legal_agent.data.examples_loader import (
    format_as_prompt_section,
    get_examples_for_document_type,
)
from legal_agent.models.documents import DocumentType
from legal_agent.models.requests import CreateDraftJobRequest
from legal_agent.models.responses import JobType
from legal_agent.services.content_preprocessor import (
    assemble_config_text,
    preprocess_and_enhance,
    preprocess_title,
)
from legal_agent.services.job_manager import JobManager

logger = logging.getLogger(__name__)


def _slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    return re.sub(r"[\s_]+", "-", text)


class DraftService:
    """Service that orchestrates the document drafting workflow."""

    def __init__(
        self,
        settings: Settings,
        job_manager: JobManager,
        rag_client: RAGClient,
        s3_client: S3Client,
        retriever: LegalCaseRetriever | None = None,
    ):
        self.settings = settings
        self.job_manager = job_manager
        self.rag_client = rag_client
        self.s3_client = s3_client
        self.retriever = retriever

        # Initialize agents based on settings
        model, provider = self._get_model_config()
        self._agents: dict[DocumentType, BaseDraftingAgent] = {
            DocumentType.CONTRACT: ContractAgent(model, provider),
            DocumentType.AGREEMENT: ContractAgent(model, provider),
            DocumentType.LEGAL_NOTICE: NoticeAgent(model, provider),
            DocumentType.DEMAND_NOTICE: NoticeAgent(model, provider),
            DocumentType.PETITION: CourtFilingAgent(model, provider),
            DocumentType.AFFIDAVIT: CourtFilingAgent(model, provider),
            DocumentType.APPLICATION: CourtFilingAgent(model, provider),
            DocumentType.BAIL_APPLICATION: BailApplicationAgent(model, provider),
            DocumentType.CRIMINAL_APPEAL: CriminalAppealAgent(model, provider),
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

    async def create_draft_job(self, request: CreateDraftJobRequest, user_id: str) -> str:
        """Create a new draft job and start processing. Returns job_id."""
        job = await self.job_manager.create_job(
            job_type=JobType.DRAFT,
            metadata={
                "case_folder_id": request.case_folder_id,
                "title": request.title,
                "document_type": request.document_type.value,
                "file_ids": request.file_ids,
                **request.metadata,
            },
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

        # Step 1: Preprocess + enhance content
        cleaned_title = preprocess_title(request.title)

        if request.config:
            raw_instructions = assemble_config_text(
                request.config, request.document_type.value
            )
        else:
            assert request.body is not None  # guaranteed by model validator
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

        agent = self._get_agent(request.document_type)
        logger.debug(f"[{job_id}] Using agent: {agent.__class__.__name__}")

        # Step 3: Create dependencies and call agent
        deps = DraftingDependencies(
            rag_client=self.rag_client,
            file_ids=request.file_ids,
            user_id=user_id,
            title=cleaned_title,
            instructions=cleaned_instructions,
            examples=formatted_examples,
            language=language,
            retriever=self.retriever,
        )

        logger.debug(f"[{job_id}] Calling agent.draft()")
        document = await agent.draft(deps)
        logger.info(f"[{job_id}] Draft completed: {len(document.sections)} sections")

        # Step 4: Upload to S3
        slug = _slugify(document.title)
        s3_path = f"{request.case_folder_id}/drafts/{slug}.md"
        await self.s3_client.upload_text(s3_path, document.draft)

        return s3_path
