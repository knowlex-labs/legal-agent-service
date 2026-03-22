"""LangGraph ReAct agent for research chat with persistent sessions."""

import json
import logging

from langchain.chat_models import init_chat_model
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.prebuilt import create_react_agent
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from legal_agent.chat.web_search import create_web_search_tool
from legal_agent.config import get_settings
from legal_agent.legal_retrieval.langchain_tools import create_legal_search_tool
from legal_agent.legal_retrieval.retriever import LegalCaseRetriever
from legal_agent.research_chat.session_store import ResearchChatSessionStore
from legal_agent.workspace_chat.agent import STYLE_INSTRUCTIONS, TONE_INSTRUCTIONS, parse_web_citations

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are an expert legal research assistant specialising in Indian law.
You help lawyers find relevant case law, statutes, and legal principles.

CAPABILITIES:
1. Search Indian court judgments using legal_case_search.
2. Find verified citations and summaries from legal databases using legal_web_search.
3. Explain legal concepts, doctrines, and procedural requirements.
4. Compare and analyse multiple cases on a legal point.

GROUNDING RULES:
1. Always call legal_case_search to retrieve relevant judgments first.
2. After forming your answer, call legal_web_search EXACTLY ONCE for supporting citations. Do NOT call it more than once.
3. Cite sources using [1], [2], etc. from legal_web_search results. Include URLs at the end.
4. Do NOT fabricate citations not found via tools.

OUTPUT FORMAT:
- Use markdown. Use headings for multi-part answers.
- State the applicable legal principle, then supporting cases."""


class ResearchChatAgent:
    def __init__(self):
        self._pool: AsyncConnectionPool | None = None
        self.checkpointer: AsyncPostgresSaver | None = None
        self.session_store: ResearchChatSessionStore | None = None
        self._legal_search_tool = None
        self._web_search_tool = None
        self._llms: dict = {}
        self._base_graphs: dict = {}

    async def initialize(self, db_url: str, retriever: LegalCaseRetriever | None = None):
        self._pool = AsyncConnectionPool(
            conninfo=db_url,
            min_size=1,
            max_size=5,
            open=False,
            kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
        )
        await self._pool.open()

        self.checkpointer = AsyncPostgresSaver(conn=self._pool)
        await self.checkpointer.setup()

        self.session_store = ResearchChatSessionStore(self._pool)
        await self.session_store.setup()

        self._legal_search_tool = create_legal_search_tool(retriever) if retriever else None
        settings = get_settings()
        self._web_search_tool = create_web_search_tool() if settings.serper_api_key else None
        logger.info("ResearchChatAgent initialized")

    def _get_llm(self, model_id: str):
        if model_id not in self._llms:
            provider = get_settings().get_langchain_provider_for_model(model_id)
            self._llms[model_id] = init_chat_model(model_id, model_provider=provider)
        return self._llms[model_id]

    def _get_graph(self, model: str):
        model_id = model or get_settings().chat_llm_default_model
        if model_id not in self._base_graphs:
            llm = self._get_llm(model_id)
            tools = []
            if self._legal_search_tool:
                tools.append(self._legal_search_tool)
            if self._web_search_tool:
                tools.append(self._web_search_tool)
            self._base_graphs[model_id] = create_react_agent(
                llm, tools=tools, checkpointer=self.checkpointer, prompt=SYSTEM_PROMPT
            )
        return self._base_graphs[model_id]

    async def stream_response(
        self,
        session_id: str,
        message: str,
        tone: str = "formal",
        style: str = "balanced",
        model: str = "",
    ):
        """Yield SSE event dicts for streaming response."""
        logger.info(
            f"[research_chat] session={session_id} | model={model} | tone={tone} | "
            f"style={style} | msg='{message[:100]}'"
        )
        graph = self._get_graph(model)
        config = {"configurable": {"thread_id": session_id}}

        tone_suffix = TONE_INSTRUCTIONS.get(tone, TONE_INSTRUCTIONS["formal"])
        style_suffix = STYLE_INSTRUCTIONS.get(style, STYLE_INSTRUCTIONS["balanced"])
        full_message = f"{message}\n\n---\nRESPONSE INSTRUCTIONS:{tone_suffix}{style_suffix}"

        web_search_output: str | None = None
        used_legal_tools = False
        legal_search_queries: list[str] = []

        async for event in graph.astream_events(
            {"messages": [HumanMessage(content=full_message)]}, config=config, version="v2"
        ):
            kind = event["event"]
            if kind == "on_chat_model_stream":
                token = event["data"]["chunk"].content
                if isinstance(token, list):
                    token = "".join(p.get("text", "") if isinstance(p, dict) else str(p) for p in token)
                if token:
                    yield {"event": "answer", "data": token.replace("\n", "\\n")}
            elif kind == "on_tool_start":
                tool_name = event["name"]
                if tool_name == "legal_case_search":
                    used_legal_tools = True
                    q = event["data"].get("input", {}).get("query", "")
                    if q:
                        legal_search_queries.append(q)
                yield {
                    "event": "tool_call",
                    "data": json.dumps({"name": tool_name, "args": event["data"].get("input", {})}),
                }
            elif kind == "on_tool_end":
                output = event["data"].get("output", "")
                if event.get("name") == "legal_web_search":
                    web_search_output = str(output)
                yield {"event": "tool_result", "data": output}

        # Force citation search if agent used legal_case_search but skipped web search
        if not web_search_output and used_legal_tools and self._web_search_tool:
            try:
                search_query = legal_search_queries[-1] if legal_search_queries else message[:200]
                yield {
                    "event": "tool_call",
                    "data": json.dumps({"name": "legal_web_search", "args": {"query": search_query}}),
                }
                web_search_output = await self._web_search_tool.ainvoke({"query": search_query})
                yield {"event": "tool_result", "data": web_search_output}
            except Exception as e:
                logger.warning(f"[research_chat] Forced citation search failed: {e}")

        if web_search_output:
            citations = parse_web_citations(web_search_output)
            if citations:
                yield {"event": "citations", "data": json.dumps(citations)}

        yield {"event": "end", "data": ""}

    def _normalize_content(self, content) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "".join(
                p.get("text", "") if isinstance(p, dict) else str(p) for p in content
            )
        return str(content)

    async def get_history(self, session_id: str) -> list[dict]:
        model_id = next(iter(self._llms), None) or get_settings().chat_llm_default_model
        graph = self._get_graph(model_id)
        state = await graph.aget_state({"configurable": {"thread_id": session_id}})
        if not state or not state.values:
            return []

        turns: list[dict] = []
        for m in state.values.get("messages", []):
            if isinstance(m, SystemMessage):
                continue
            if isinstance(m, HumanMessage):
                turns.append({"role": "human", "content": self._normalize_content(m.content)})
            elif isinstance(m, AIMessage):
                turn = {"role": "ai", "content": self._normalize_content(m.content)}
                if m.tool_calls:
                    turn["tool_calls"] = [
                        {"name": tc["name"], "args": tc["args"]} for tc in m.tool_calls
                    ]
                turns.append(turn)
            elif isinstance(m, ToolMessage):
                if turns and turns[-1].get("tool_calls"):
                    for tc in turns[-1]["tool_calls"]:
                        if tc.get("result") is None:
                            tc["result"] = self._normalize_content(m.content)
                            break
        return turns

    async def clear_session(self, session_id: str):
        if not self._pool:
            return
        async with self._pool.connection() as conn:
            await conn.execute("DELETE FROM checkpoint_writes WHERE thread_id = %s", (session_id,))
            await conn.execute("DELETE FROM checkpoint_blobs WHERE thread_id = %s", (session_id,))
            await conn.execute("DELETE FROM checkpoints WHERE thread_id = %s", (session_id,))
        logger.info(f"[research_chat] Session {session_id} checkpoints cleared")

    async def close(self):
        if self._pool:
            await self._pool.close()
