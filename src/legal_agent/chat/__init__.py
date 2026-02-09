"""Conversational chat agent module with SSE streaming."""

from legal_agent.chat.agent import ChatAgent
from legal_agent.chat.routes import chat_router

__all__ = ["ChatAgent", "chat_router"]
