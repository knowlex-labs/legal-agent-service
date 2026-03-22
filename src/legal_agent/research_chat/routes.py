"""FastAPI routes for research chat with SSE streaming."""

import logging

from fastapi import APIRouter, Header, HTTPException
from sse_starlette.sse import EventSourceResponse

from legal_agent.research_chat.agent import ResearchChatAgent
from legal_agent.research_chat.models import (
    ChatHistoryMessage,
    ResearchChatConfigUpdate,
    ResearchChatHistoryResponse,
    ResearchChatMessageRequest,
    ResearchChatSessionResponse,
)

logger = logging.getLogger(__name__)
research_chat_router = APIRouter(prefix="/research-chat", tags=["research-chat"])
_agent: ResearchChatAgent | None = None


def set_research_chat_agent(agent: ResearchChatAgent) -> None:
    global _agent
    _agent = agent


def _get_agent() -> ResearchChatAgent:
    if _agent is None:
        raise HTTPException(
            status_code=503,
            detail="Research chat agent is still initializing, please retry shortly",
        )
    return _agent


# --- Session management ---


@research_chat_router.post("/sessions", response_model=ResearchChatSessionResponse, status_code=201)
async def create_session(
    x_user_id: str = Header(..., alias="X-User-Id"),
) -> ResearchChatSessionResponse:
    agent = _get_agent()
    row = await agent.session_store.create(x_user_id)
    return ResearchChatSessionResponse(**row)


@research_chat_router.get("/sessions", response_model=list[ResearchChatSessionResponse])
async def list_sessions(
    x_user_id: str = Header(..., alias="X-User-Id"),
) -> list[ResearchChatSessionResponse]:
    agent = _get_agent()
    rows = await agent.session_store.list_by_user(x_user_id)
    return [ResearchChatSessionResponse(**r) for r in rows]


@research_chat_router.get("/sessions/{session_id}", response_model=ResearchChatSessionResponse)
async def get_session(session_id: str) -> ResearchChatSessionResponse:
    agent = _get_agent()
    row = await agent.session_store.get(session_id)
    if not row:
        raise HTTPException(status_code=404, detail="Session not found")
    return ResearchChatSessionResponse(**row)


@research_chat_router.patch("/sessions/{session_id}", response_model=ResearchChatSessionResponse)
async def update_config(session_id: str, update: ResearchChatConfigUpdate):
    agent = _get_agent()
    row = await agent.session_store.update_config(session_id, update.tone, update.style)
    if not row:
        raise HTTPException(status_code=404, detail="Session not found")
    return ResearchChatSessionResponse(**row)


@research_chat_router.delete("/sessions/{session_id}")
async def delete_session(session_id: str):
    agent = _get_agent()
    existed = await agent.session_store.delete(session_id)
    if not existed:
        raise HTTPException(status_code=404, detail="Session not found")
    await agent.clear_session(session_id)
    return {"status": "deleted"}


# --- Messages ---


@research_chat_router.post("/sessions/{session_id}/messages")
async def send_message(
    session_id: str,
    request: ResearchChatMessageRequest,
    x_user_id: str = Header(..., alias="X-User-Id"),
):
    """Send a message and stream the AI response via SSE."""
    agent = _get_agent()
    row = await agent.session_store.get(session_id)
    if not row:
        raise HTTPException(status_code=404, detail="Session not found")

    async def event_generator():
        try:
            async for event in agent.stream_response(
                session_id=session_id,
                message=request.message,
                tone=request.tone,
                style=request.style,
                model=request.model,
            ):
                yield {"event": event["event"], "data": event["data"]}
        except Exception:
            logger.exception(f"[research_chat] Stream error for session {session_id}")
            yield {"event": "error", "data": "An error occurred while generating the response."}

    return EventSourceResponse(event_generator())


@research_chat_router.get("/sessions/{session_id}/history", response_model=ResearchChatHistoryResponse)
async def get_history(session_id: str) -> ResearchChatHistoryResponse:
    agent = _get_agent()
    row = await agent.session_store.get(session_id)
    if not row:
        raise HTTPException(status_code=404, detail="Session not found")
    raw = await agent.get_history(session_id)
    messages = [ChatHistoryMessage(**m) for m in raw]
    return ResearchChatHistoryResponse(session_id=session_id, messages=messages)


@research_chat_router.post("/sessions/{session_id}/clear")
async def clear_session(session_id: str):
    agent = _get_agent()
    row = await agent.session_store.get(session_id)
    if not row:
        raise HTTPException(status_code=404, detail="Session not found")
    await agent.clear_session(session_id)
    return {"status": "cleared", "session_id": session_id}
