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

STYLE_INSTRUCTIONS = {
    "precise": (
        "\n\nRESPONSE STYLE — PRECISE:"
        "\n- Give a short, direct answer (3-5 sentences max)."
        "\n- Lead with the ratio decidendi / holding."
        "\n- Cite only the most authoritative 1-2 cases."
        "\n- No background or history — just the answer."
        "\n- Use bullet points for multiple holdings."
    ),
    "balanced": (
        "\n\nRESPONSE STYLE — BALANCED:"
        "\n- Provide a clear answer with supporting reasoning."
        "\n- Cite 2-4 key cases with brief context for each."
        "\n- Explain the legal principle and its application."
        "\n- Mention dissenting or evolving views if relevant."
        "\n- Keep it under 400 words."
    ),
    "detailed": (
        "\n\nRESPONSE STYLE — DETAILED:"
        "\n- Provide comprehensive analysis suitable for a research memo."
        "\n- Cite all relevant cases from search results with paragraph quotes."
        "\n- Trace the evolution of the legal principle across judgments."
        "\n- Discuss conflicting precedents and distinguish on facts."
        "\n- Include obiter dicta that may be persuasive."
        "\n- Structure with headings: Legal Position, Key Cases, Analysis, Conclusion."
    ),
}


class ChatAgent:
    def __init__(self, retriever: LegalCaseRetriever | None = None):
        self.retriever = retriever
        self.checkpointer: AsyncPostgresSaver | None = None
        self._checkpointer_cm = None
        self._graphs: dict[str, object] = {}  # "openai_kb", "openai_general", "gemini_kb", etc.

    async def initialize(self, db_url: str):
        self._checkpointer_cm = AsyncPostgresSaver.from_conn_string(db_url)
        self.checkpointer = await self._checkpointer_cm.__aenter__()
        await self.checkpointer.setup()
        self._compile_graphs()
        logger.info("ChatAgent initialized")

    def _compile_graphs(self):
        settings = get_settings()
        chat_models = settings.get_chat_models()
        tools = [create_legal_search_tool(self.retriever)] if self.retriever else []

        for provider_key, (model_name, langchain_provider) in chat_models.items():
            llm = init_chat_model(model_name, model_provider=langchain_provider)
            self._graphs[f"{provider_key}_kb"] = create_react_agent(
                model=llm, tools=tools, checkpointer=self.checkpointer, prompt=SYSTEM_PROMPT_KB,
            )
            self._graphs[f"{provider_key}_general"] = create_react_agent(
                model=llm, tools=[], checkpointer=self.checkpointer, prompt=SYSTEM_PROMPT_GENERAL,
            )
            logger.info(f"Compiled graphs for {provider_key} ({model_name})")

    def _get_graph(self, model: str, enable_kb: bool):
        suffix = "kb" if enable_kb else "general"
        key = f"{model}_{suffix}"
        if key not in self._graphs:
            settings = get_settings()
            logger.warning(f"Graph '{key}' not found, falling back to {settings.chat_llm_default_provider}")
            key = f"{settings.chat_llm_default_provider}_{suffix}"
        return self._graphs[key]

    async def stream_response(self, session_id: str, message: str, enable_kb: bool = True, model: str = "openai", style: str = "balanced"):
        """Yield SSE event dicts: token, tool_call, tool_result, end."""
        logger.info(f"[chat] session={session_id} | model={model} | kb={enable_kb} | style={style} | message='{message[:100]}'")
        graph = self._get_graph(model, enable_kb)
        config = {"configurable": {"thread_id": session_id}}

        style_suffix = STYLE_INSTRUCTIONS.get(style, STYLE_INSTRUCTIONS["balanced"])
        full_message = f"{message}\n\n[System: {style_suffix}]"

        async for event in graph.astream_events({"messages": [HumanMessage(content=full_message)]}, config=config, version="v2"):
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

    async def get_history(self, session_id: str, model: str = "openai", enable_kb: bool = True) -> list[dict]:
        graph = self._get_graph(model, enable_kb)
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
