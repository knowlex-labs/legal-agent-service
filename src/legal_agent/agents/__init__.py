"""Legal agent package — drafts, translation, and future agent types."""

from legal_agent.agents.drafts.anticipatory_bail_agent import AnticipatoryBailAgent
from legal_agent.agents.drafts.application_agent import ApplicationAgent
from legal_agent.agents.drafts.first_bail_agent import FirstBailApplicationAgent
from legal_agent.agents.drafts.second_bail_agent import SecondBailApplicationAgent
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

__all__ = [
    "BaseDraftingAgent",
    "DraftingDependencies",
    "ContractAgent",
    "NoticeAgent",
    "CourtFilingAgent",
    "FirstBailApplicationAgent",
    "SecondBailApplicationAgent",
    "CriminalAppealAgent",
    "SLPAgent",
    "QuashingPetitionAgent",
    "AnticipatoryBailAgent",
    "RevisionPetitionAgent",
    "ExecutionPetitionAgent",
    "ConsumerComplaintAgent",
    "PatentAgent",
    "WrittenStatementAgent",
    "WrittenArgumentsAgent",
    "ApplicationAgent",
]
