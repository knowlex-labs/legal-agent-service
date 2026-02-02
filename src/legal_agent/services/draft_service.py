"""Draft service that orchestrates the document drafting workflow."""

import logging

from legal_agent.agents.base import BaseDraftingAgent, DraftingDependencies
from legal_agent.agents.contract_agent import ContractAgent
from legal_agent.agents.court_filing_agent import CourtFilingAgent
from legal_agent.agents.notice_agent import NoticeAgent
from legal_agent.clients.rag_client import RAGClient
from legal_agent.config import Settings
from legal_agent.models.documents import DocumentType
from legal_agent.models.requests import CreateDraftRequest
from legal_agent.models.responses import DraftResult
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
        model = self._get_model_string()
        self._agents: dict[DocumentType, BaseDraftingAgent] = {
            DocumentType.CONTRACT: ContractAgent(model),
            DocumentType.AGREEMENT: ContractAgent(model),
            DocumentType.LEGAL_NOTICE: NoticeAgent(model),
            DocumentType.DEMAND_NOTICE: NoticeAgent(model),
            DocumentType.PETITION: CourtFilingAgent(model),
            DocumentType.AFFIDAVIT: CourtFilingAgent(model),
            DocumentType.APPLICATION: CourtFilingAgent(model),
        }

    def _get_model_string(self) -> str:
        """Get the model string for Pydantic AI."""
        provider = self.settings.llm_provider
        model = self.settings.llm_model
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

        agent = self._get_agent(request.document_type)
        logger.debug(f"[{job_id}] Using agent: {agent.__class__.__name__}")

        deps = DraftingDependencies(
            rag_client=self.rag_client,
            file_ids=request.file_ids,
            title=request.title,
            instructions=request.body,
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
