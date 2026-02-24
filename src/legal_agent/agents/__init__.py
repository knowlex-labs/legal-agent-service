"""Legal document drafting agents."""

from legal_agent.agents.bail_agent import BailApplicationAgent
from legal_agent.agents.base import BaseDraftingAgent, DraftingDependencies
from legal_agent.agents.contract_agent import ContractAgent
from legal_agent.agents.court_filing_agent import CourtFilingAgent
from legal_agent.agents.criminal_appeal_agent import CriminalAppealAgent
from legal_agent.agents.notice_agent import NoticeAgent

__all__ = [
    "BaseDraftingAgent",
    "DraftingDependencies",
    "ContractAgent",
    "NoticeAgent",
    "CourtFilingAgent",
    "BailApplicationAgent",
    "CriminalAppealAgent",
]
