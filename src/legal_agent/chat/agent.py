"""Chat agent using LangGraph ReAct with PostgresSaver for persistent sessions."""

import json
import logging

from langchain.chat_models import init_chat_model
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.prebuilt import create_react_agent

from legal_agent.config import get_settings
from legal_agent.legal_retrieval import LegalCaseRetriever, create_legal_search_tool

logger = logging.getLogger(__name__)

CHAT_SYSTEM_PROMPT = """You are a legal research assistant specializing in Indian law.
You help lawyers and legal professionals find relevant case law, understand legal
principles, and analyze judicial interpretations from the Indian Supreme Court.

When answering questions:
1. Use the legal_case_search tool to find relevant Supreme Court judgments
2. Cite cases properly with their citation (e.g., "2025 INSC 1392")
3. Quote relevant paragraphs from judgments when applicable
4. Explain legal principles in clear, professional language
5. Note any conflicting precedents or evolving interpretations

If the knowledge base is unavailable, provide general legal guidance based on
your training, but clearly state that you could not search the case law database."""


class ChatAgent:
    def __init__(self, retriever: LegalCaseRetriever | None = None):
        self.retriever = retriever
        self.checkpointer: AsyncPostgresSaver | None = None
        self._checkpointer_cm = None  # async context manager handle
        self.graph = None

    async def initialize(self, db_url: str):
        """Set up PostgresSaver and compile the graph. Called once at startup."""
        self._checkpointer_cm = AsyncPostgresSaver.from_conn_string(db_url)
        self.checkpointer = await self._checkpointer_cm.__aenter__()
        await self.checkpointer.setup()
        self._compile_graph()
        logger.info("ChatAgent initialized with PostgresSaver")

    def _compile_graph(self):
        """Build and compile the LangGraph agent."""
        settings = get_settings()
        llm = init_chat_model(
            settings.chat_llm_model,
            model_provider=settings.get_chat_langchain_provider(),
        )
        tools = []
        if self.retriever:
            tools.append(create_legal_search_tool(self.retriever))

        self.graph = create_react_agent(
            model=llm,
            tools=tools,
            checkpointer=self.checkpointer,
            prompt=CHAT_SYSTEM_PROMPT,
        )

    async def stream_response(self, session_id: str, message: str):
        """Stream agent response as async generator of SSE events.

        Yields dicts like:
          {"event": "token", "data": "partial text..."}
          {"event": "tool_call", "data": {"name": "...", "args": {...}}}
          {"event": "tool_result", "data": "..."}
          {"event": "end", "data": ""}
        """
        config = {"configurable": {"thread_id": session_id}}
        input_msg = {"messages": [HumanMessage(content=message)]}

        async for event in self.graph.astream_events(
            input_msg, config=config, version="v2"
        ):
            kind = event["event"]
            if kind == "on_chat_model_stream":
                token = event["data"]["chunk"].content
                if token:
                    yield {"event": "token", "data": token}
            elif kind == "on_tool_start":
                yield {
                    "event": "tool_call",
                    "data": json.dumps({
                        "name": event["name"],
                        "args": event["data"].get("input", {}),
                    }),
                }
            elif kind == "on_tool_end":
                yield {
                    "event": "tool_result",
                    "data": event["data"].get("output", ""),
                }

        yield {"event": "end", "data": ""}

    async def get_history(self, session_id: str) -> list[dict]:
        """Get conversation history for a session from checkpoint."""
        config = {"configurable": {"thread_id": session_id}}
        state = await self.graph.aget_state(config)
        if not state or not state.values:
            return []
        messages = state.values.get("messages", [])
        return [
            {
                "role": (
                    "human" if isinstance(m, HumanMessage) else
                    "ai" if isinstance(m, AIMessage) else
                    "tool" if isinstance(m, ToolMessage) else "system"
                ),
                "content": m.content,
            }
            for m in messages
            if not isinstance(m, SystemMessage)
        ]

    async def close(self):
        """Clean up checkpointer connection."""
        if self._checkpointer_cm:
            await self._checkpointer_cm.__aexit__(None, None, None)
