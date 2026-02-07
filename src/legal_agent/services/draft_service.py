"""Draft service that orchestrates the document drafting workflow."""

import logging

from legal_agent.agents.base import BaseDraftingAgent, DraftingDependencies
from legal_agent.agents.contract_agent import ContractAgent
from legal_agent.agents.court_filing_agent import CourtFilingAgent
from legal_agent.agents.notice_agent import NoticeAgent
from legal_agent.clients.rag_client import RAGClient
from legal_agent.config import Settings
from legal_agent.data.examples_loader import (
    format_as_prompt_section,
    get_examples_for_document_type,
)
from legal_agent.models.documents import DocumentType
from legal_agent.models.requests import CreateDraftRequest
from legal_agent.models.responses import DraftResult
from legal_agent.services.content_preprocessor import (
    assemble_config_text,
    preprocess_and_enhance,
    preprocess_title,
)
from legal_agent.services.job_manager import JobManager

logger = logging.getLogger(__name__)


class DraftService:
    """Service that orchestrates the document drafting workflow."""

    def __init__(
        self,
        settings: Settings,
        job_manager: JobManager,
        rag_client: RAGClient,
    ):
        self.settings = settings
        self.job_manager = job_manager
        self.rag_client = rag_client

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
        }

    def _get_model_config(self) -> tuple[str, str]:
        """Get the model name and LangChain provider."""
        return self.settings.llm_model, self.settings.get_langchain_provider()

    def _get_enhance_model_string(self) -> str:
        """Get a fast/cheap model string for content enhancement."""
        provider = self.settings.llm_provider
        # Use lighter models for preprocessing to keep it fast
        fast_models = {
            "openai": "gpt-4o-mini",
            "anthropic": "claude-3-5-haiku-latest",
            "gemini": "gemini-2.0-flash",
        }
        model = fast_models.get(provider, "gpt-4o-mini")
        return f"{provider}:{model}"

    def _get_agent(self, document_type: DocumentType) -> BaseDraftingAgent:
        """Get the appropriate agent for the document type."""
        return self._agents.get(document_type, self._agents[DocumentType.CONTRACT])

    async def create_draft_job(self, request: CreateDraftRequest) -> str:
        """Create a new draft job and start processing."""
        job = await self.job_manager.create_job(
            metadata={
                "title": request.title,
                "document_type": request.document_type.value,
                "file_ids": request.file_ids,
            }
        )

        logger.info(
            f"Created draft job {job.job_id}: "
            f"type={request.document_type.value}, "
            f"title='{request.title}', "
            f"file_ids={request.file_ids}"
        )

        async def draft_task() -> DraftResult:
            return await self._execute_draft(request, job.job_id)

        await self.job_manager.run_job(job.job_id, draft_task)
        return job.job_id

    async def _execute_draft(
        self, request: CreateDraftRequest, job_id: str
    ) -> DraftResult:
        """Execute the drafting task."""
        logger.debug(f"[{job_id}] Starting draft execution")

        # Step 1: Preprocess + enhance content
        # - Rule-based: fix spelling, standardize legal terms (instant)
        # - LLM-based: rewrite casual input into formal legal instructions
        cleaned_title = preprocess_title(request.title)

        if request.config:
            raw_instructions = assemble_config_text(
                request.config, request.document_type.value
            )
        else:
            assert request.body is not None  # guaranteed by model validator
            raw_instructions = request.body

        enhance_model = self._get_enhance_model_string()
        cleaned_instructions = await preprocess_and_enhance(
            raw_instructions,
            document_type=request.document_type.value,
            model=enhance_model,
        )
        logger.debug(f"[{job_id}] Content preprocessed and enhanced")

        # Step 2: Load few-shot examples for this document type
        examples_data = get_examples_for_document_type(
            request.document_type.value,
            subtype=request.metadata.get("subtype"),
        )
        formatted_examples = format_as_prompt_section(examples_data)
        logger.debug(f"[{job_id}] Examples loaded: {len(formatted_examples)} chars")

        agent = self._get_agent(request.document_type)
        logger.debug(f"[{job_id}] Using agent: {agent.__class__.__name__}")

        # Step 3: Create dependencies with cleaned content + examples
        deps = DraftingDependencies(
            rag_client=self.rag_client,
            file_ids=request.file_ids,
            title=cleaned_title,
            instructions=cleaned_instructions,
            examples=formatted_examples,
        )

        logger.debug(f"[{job_id}] Calling agent.draft()")
        document = await agent.draft(deps)
        logger.info(f"[{job_id}] Draft completed: {len(document.sections)} sections")

        return DraftResult(
            draft=document.draft,
            sections=[
                {"title": s.title, "content": s.content, "order": s.order}
                for s in document.sections
            ],
            metadata={
                "document_type": document.document_type.value,
                "title": document.title,
                "summary": document.summary,
                **request.metadata,
            },
        )
