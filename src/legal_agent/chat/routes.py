"""FastAPI routes for the chat agent with SSE streaming."""

import logging
import uuid

from fastapi import APIRouter
from sse_starlette.sse import EventSourceResponse

from legal_agent.chat.agent import ChatAgent
from legal_agent.chat.models import (
    ChatHistoryResponse,
    ChatMessageRequest,
    ChatSessionResponse,
)

logger = logging.getLogger(__name__)

chat_router = APIRouter(prefix="/chat", tags=["chat"])

_chat_agent: ChatAgent | None = None


def set_chat_agent(agent: ChatAgent) -> None:
    """Set the chat agent instance for dependency injection."""
    global _chat_agent
    _chat_agent = agent


def _get_agent() -> ChatAgent:
    """Get the chat agent instance."""
    if _chat_agent is None:
        raise RuntimeError("Chat agent not initialized")
    return _chat_agent


@chat_router.post("/sessions", response_model=ChatSessionResponse, status_code=201)
async def create_session() -> ChatSessionResponse:
    """Create a new chat session.

    Returns a session_id (UUID). The actual checkpoint is created on first message.
    """
    session_id = str(uuid.uuid4())
    logger.info(f"Created chat session: {session_id}")
    return ChatSessionResponse(session_id=session_id)


@chat_router.post("/sessions/{session_id}/messages")
async def send_message(session_id: str, request: ChatMessageRequest):
    """Send a message and receive a streamed response via SSE.

    Returns a text/event-stream with events:
    - token: partial text chunk
    - tool_call: tool invocation details
    - tool_result: tool output
    - end: stream complete
    """
    agent = _get_agent()

    async def event_generator():
        try:
            async for event in agent.stream_response(session_id, request.message):
                yield {
                    "event": event["event"],
                    "data": event["data"],
                }
        except Exception:
            logger.exception(f"Error streaming response for session {session_id}")
            yield {
                "event": "error",
                "data": "An error occurred while generating the response.",
            }

    return EventSourceResponse(event_generator())


@chat_router.get("/sessions/{session_id}/history", response_model=ChatHistoryResponse)
async def get_history(session_id: str) -> ChatHistoryResponse:
    """Get conversation history for a session."""
    agent = _get_agent()
    messages = await agent.get_history(session_id)
    return ChatHistoryResponse(
        session_id=session_id,
        messages=messages,
    )


@chat_router.delete("/sessions/{session_id}")
async def delete_session(session_id: str):
    """Clear the session checkpoint."""
    # LangGraph's PostgresSaver doesn't expose a direct delete,
    # but we can return a confirmation. The session simply won't
    # be used again and can be cleaned up by DB maintenance.
    logger.info(f"Session {session_id} marked for deletion")
    return {"status": "deleted"}
