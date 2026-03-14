"""FastAPI routes for workspace chat with SSE streaming."""

import logging

from fastapi import APIRouter, Header, HTTPException
from sse_starlette.sse import EventSourceResponse

from legal_agent.workspace_chat.agent import WorkspaceChatAgent
from legal_agent.workspace_chat.models import (
    ChatHistoryMessage,
    CreateSessionRequest,
    WorkspaceChatConfigUpdate,
    WorkspaceChatHistoryResponse,
    WorkspaceChatMessageRequest,
    WorkspaceChatSessionResponse,
)

logger = logging.getLogger(__name__)
workspace_chat_router = APIRouter(prefix="/workspace-chat", tags=["workspace-chat"])
_agent: WorkspaceChatAgent | None = None


def set_workspace_chat_agent(agent: WorkspaceChatAgent) -> None:
    global _agent
    _agent = agent


def _get_agent() -> WorkspaceChatAgent:
    if _agent is None:
        raise HTTPException(
            status_code=503,
            detail="Workspace chat agent is still initializing, please retry shortly",
        )
    return _agent


# --- Session management ---


@workspace_chat_router.post("/sessions", response_model=WorkspaceChatSessionResponse, status_code=201)
async def create_session(request: CreateSessionRequest) -> WorkspaceChatSessionResponse:
    """Create a new chat session for a case folder."""
    agent = _get_agent()
    row = await agent.session_store.create(request.case_folder_id)
    return WorkspaceChatSessionResponse(**row)


@workspace_chat_router.get("/sessions/{session_id}", response_model=WorkspaceChatSessionResponse)
async def get_session(session_id: str) -> WorkspaceChatSessionResponse:
    """Get session details by session ID."""
    agent = _get_agent()
    row = await agent.session_store.get(session_id)
    if not row:
        raise HTTPException(status_code=404, detail="Session not found")
    return WorkspaceChatSessionResponse(**row)


@workspace_chat_router.get(
    "/case-folders/{case_folder_id}/sessions",
    response_model=list[WorkspaceChatSessionResponse],
)
async def list_sessions(case_folder_id: str) -> list[WorkspaceChatSessionResponse]:
    """List all sessions for a case folder."""
    agent = _get_agent()
    rows = await agent.session_store.list_by_case_folder(case_folder_id)
    return [WorkspaceChatSessionResponse(**r) for r in rows]


@workspace_chat_router.patch("/sessions/{session_id}", response_model=WorkspaceChatSessionResponse)
async def update_config(session_id: str, update: WorkspaceChatConfigUpdate):
    """Update tone/style configuration for a session."""
    agent = _get_agent()
    row = await agent.session_store.update_config(session_id, update.tone, update.style)
    if not row:
        raise HTTPException(status_code=404, detail="Session not found")
    return WorkspaceChatSessionResponse(**row)


@workspace_chat_router.delete("/sessions/{session_id}")
async def delete_session(session_id: str):
    """Delete a session and all its messages permanently."""
    agent = _get_agent()
    existed = await agent.session_store.delete(session_id)
    if not existed:
        raise HTTPException(status_code=404, detail="Session not found")
    await agent.clear_session(session_id)
    return {"status": "deleted"}


# --- Messages ---


@workspace_chat_router.post("/sessions/{session_id}/messages")
async def send_message(
    session_id: str,
    request: WorkspaceChatMessageRequest,
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
                file_ids=request.file_ids,
                user_id=x_user_id,
                model=request.model,
            ):
                yield {"event": event["event"], "data": event["data"]}
        except Exception:
            logger.exception(f"[workspace_chat] Stream error for session {session_id}")
            yield {"event": "error", "data": "An error occurred while generating the response."}

    return EventSourceResponse(event_generator())


@workspace_chat_router.get("/sessions/{session_id}/history", response_model=WorkspaceChatHistoryResponse)
async def get_history(session_id: str) -> WorkspaceChatHistoryResponse:
    """Get full conversation history for a session."""
    agent = _get_agent()
    row = await agent.session_store.get(session_id)
    if not row:
        raise HTTPException(status_code=404, detail="Session not found")
    raw = await agent.get_history(session_id)
    messages = [ChatHistoryMessage(**m) for m in raw]
    return WorkspaceChatHistoryResponse(session_id=session_id, messages=messages)


@workspace_chat_router.post("/sessions/{session_id}/clear")
async def clear_session(session_id: str):
    """Clear all messages from a session but keep the session itself."""
    agent = _get_agent()
    row = await agent.session_store.get(session_id)
    if not row:
        raise HTTPException(status_code=404, detail="Session not found")
    await agent.clear_session(session_id)
    return {"status": "cleared", "session_id": session_id}
