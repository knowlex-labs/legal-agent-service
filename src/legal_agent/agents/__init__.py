"""Legal document drafting agents."""

from legal_agent.agents.anticipatory_bail_agent import AnticipatoryBailAgent
from legal_agent.agents.application_agent import ApplicationAgent
from legal_agent.agents.bail_agent import BailApplicationAgent
from legal_agent.agents.base import BaseDraftingAgent, DraftingDependencies
from legal_agent.agents.consumer_complaint_agent import ConsumerComplaintAgent
from legal_agent.agents.contract_agent import ContractAgent
from legal_agent.agents.court_filing_agent import CourtFilingAgent
from legal_agent.agents.criminal_appeal_agent import CriminalAppealAgent
from legal_agent.agents.execution_petition_agent import ExecutionPetitionAgent
from legal_agent.agents.notice_agent import NoticeAgent
from legal_agent.agents.patent_agent import PatentAgent
from legal_agent.agents.quashing_petition_agent import QuashingPetitionAgent
from legal_agent.agents.revision_petition_agent import RevisionPetitionAgent
from legal_agent.agents.slp_agent import SLPAgent
from legal_agent.agents.written_arguments_agent import WrittenArgumentsAgent
from legal_agent.agents.written_statement_agent import WrittenStatementAgent

__all__ = [
    "BaseDraftingAgent",
    "DraftingDependencies",
    "ContractAgent",
    "NoticeAgent",
    "CourtFilingAgent",
    "BailApplicationAgent",
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
