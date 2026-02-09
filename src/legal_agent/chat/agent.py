"""LangGraph ReAct chat agent with PostgresSaver for persistent sessions."""

import json
import logging

from langchain.chat_models import init_chat_model
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.prebuilt import create_react_agent

from legal_agent.config import get_settings
from legal_agent.legal_retrieval import LegalCaseRetriever, create_legal_search_tool

logger = logging.getLogger(__name__)

SYSTEM_PROMPT_KB = """You are a legal research assistant specializing in Indian law.
You help lawyers and legal professionals find relevant case law, understand legal
principles, and analyze judicial interpretations from the Indian Supreme Court.

IMPORTANT RULES:
1. You MUST use the legal_case_search tool to answer every question.
2. ONLY answer based on the search results returned by the tool.
3. If the tool returns no results, say "No relevant cases found in the knowledge base."
4. Do NOT answer questions unrelated to Indian law. Politely decline non-legal queries.
5. Cite cases properly with their citation (e.g., "2025 INSC 1392").
6. Quote relevant paragraphs from judgments when applicable."""

SYSTEM_PROMPT_GENERAL = """You are a legal research assistant specializing in Indian law.
You help lawyers and legal professionals with general legal questions about Indian law.
Answer based on your training knowledge. Be clear that your answers are general guidance
and not based on specific case law searches."""


class ChatAgent:
    def __init__(self, retriever: LegalCaseRetriever | None = None):
        self.retriever = retriever
        self.checkpointer: AsyncPostgresSaver | None = None
        self._checkpointer_cm = None
        self._graph_kb = None
        self._graph_general = None

    async def initialize(self, db_url: str):
        self._checkpointer_cm = AsyncPostgresSaver.from_conn_string(db_url)
        self.checkpointer = await self._checkpointer_cm.__aenter__()
        await self.checkpointer.setup()
        self._compile_graphs()
        logger.info("ChatAgent initialized")

    def _compile_graphs(self):
        settings = get_settings()
        llm = init_chat_model(settings.chat_llm_model, model_provider=settings.get_chat_langchain_provider())

        tools = [create_legal_search_tool(self.retriever)] if self.retriever else []
        self._graph_kb = create_react_agent(
            model=llm, tools=tools, checkpointer=self.checkpointer, prompt=SYSTEM_PROMPT_KB,
        )
        self._graph_general = create_react_agent(
            model=llm, tools=[], checkpointer=self.checkpointer, prompt=SYSTEM_PROMPT_GENERAL,
        )

    def _get_graph(self, enable_kb: bool):
        return self._graph_kb if enable_kb else self._graph_general

    async def stream_response(self, session_id: str, message: str, enable_kb: bool = True):
        """Yield SSE event dicts: token, tool_call, tool_result, end."""
        logger.info(f"[chat] session={session_id} | kb={enable_kb} | message='{message[:100]}'")
        graph = self._get_graph(enable_kb)
        config = {"configurable": {"thread_id": session_id}}

        async for event in graph.astream_events({"messages": [HumanMessage(content=message)]}, config=config, version="v2"):
            kind = event["event"]
            if kind == "on_chat_model_stream":
                token = event["data"]["chunk"].content
                if token:
                    yield {"event": "token", "data": token}
            elif kind == "on_tool_start":
                yield {"event": "tool_call", "data": json.dumps({"name": event["name"], "args": event["data"].get("input", {})})}
            elif kind == "on_tool_end":
                yield {"event": "tool_result", "data": event["data"].get("output", "")}

        yield {"event": "end", "data": ""}

    async def get_history(self, session_id: str, enable_kb: bool = True) -> list[dict]:
        graph = self._get_graph(enable_kb)
        state = await graph.aget_state({"configurable": {"thread_id": session_id}})
        if not state or not state.values:
            return []

        role_map = {HumanMessage: "human", AIMessage: "ai", ToolMessage: "tool"}
        return [
            {"role": role_map.get(type(m), "system"), "content": m.content}
            for m in state.values.get("messages", [])
            if not isinstance(m, SystemMessage)
        ]

    async def close(self):
        if self._checkpointer_cm:
            await self._checkpointer_cm.__aexit__(None, None, None)
