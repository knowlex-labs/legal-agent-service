"""FastAPI routes for the case agent with SSE streaming."""

import logging

from fastapi import APIRouter
from sse_starlette.sse import EventSourceResponse

from legal_agent.case_agent.agent import CaseAgent
from legal_agent.case_agent.models import CaseAgentRequest

logger = logging.getLogger(__name__)
case_agent_router = APIRouter(prefix="/cases", tags=["case-agent"])
_case_agent: CaseAgent | None = None


def set_case_agent(agent: CaseAgent) -> None:
    global _case_agent
    _case_agent = agent


def _get_agent() -> CaseAgent:
    if _case_agent is None:
        raise RuntimeError("Case agent not initialized")
    return _case_agent


@case_agent_router.post("/{case_id}/agent/stream")
async def stream_case_agent(case_id: str, request: CaseAgentRequest):
    agent = _get_agent()

    async def event_generator():
        try:
            async for event in agent.stream_response(case_id, request):
                yield {"event": event["event"], "data": event["data"]}
        except Exception:
            logger.exception(f"Stream error for case {case_id}")
            yield {"event": "error", "data": "An error occurred while generating the response."}

    return EventSourceResponse(event_generator())
