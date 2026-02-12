"""FastAPI routes for the chat agent with SSE streaming."""

import logging
import uuid

from fastapi import APIRouter
from sse_starlette.sse import EventSourceResponse

from legal_agent.chat.agent import ChatAgent
from legal_agent.chat.models import ChatHistoryResponse, ChatMessageRequest, ChatSessionResponse

logger = logging.getLogger(__name__)
chat_router = APIRouter(prefix="/chat", tags=["chat"])
_chat_agent: ChatAgent | None = None


def set_chat_agent(agent: ChatAgent) -> None:
    global _chat_agent
    _chat_agent = agent


def _get_agent() -> ChatAgent:
    if _chat_agent is None:
        raise RuntimeError("Chat agent not initialized")
    return _chat_agent


@chat_router.post("/sessions", response_model=ChatSessionResponse, status_code=201)
async def create_session() -> ChatSessionResponse:
    session_id = str(uuid.uuid4())
    logger.info(f"New chat session: {session_id}")
    return ChatSessionResponse(session_id=session_id)


@chat_router.post("/sessions/{session_id}/messages")
async def send_message(session_id: str, request: ChatMessageRequest):
    agent = _get_agent()

    if request.enable_web:
        logger.warning("enable_web requested but web search is not yet implemented")

    async def event_generator():
        try:
            async for event in agent.stream_response(session_id, request.message, enable_kb=request.enable_kb, model=request.model, style=request.style):
                yield {"event": event["event"], "data": event["data"]}
        except Exception:
            logger.exception(f"Stream error for session {session_id}")
            yield {"event": "error", "data": "An error occurred while generating the response."}

    return EventSourceResponse(event_generator())


@chat_router.get("/sessions/{session_id}/history", response_model=ChatHistoryResponse)
async def get_history(session_id: str) -> ChatHistoryResponse:
    messages = await _get_agent().get_history(session_id)
    return ChatHistoryResponse(session_id=session_id, messages=messages)


@chat_router.delete("/sessions/{session_id}")
async def delete_session(session_id: str):
    logger.info(f"Session deleted: {session_id}")
    return {"status": "deleted"}
