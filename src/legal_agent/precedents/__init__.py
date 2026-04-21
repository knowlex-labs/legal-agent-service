"""Precedent finder — returns precedents relevant to a case folder."""

from legal_agent.precedents.generator import PrecedentGenerator
from legal_agent.precedents.service import PrecedentService

__all__ = ["PrecedentGenerator", "PrecedentService"]
