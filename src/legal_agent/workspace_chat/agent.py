"""LangGraph ReAct agent for workspace chat with persistent sessions."""

import json
import logging

from langchain.chat_models import init_chat_model
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.prebuilt import create_react_agent
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from legal_agent.chat.citation_utils import parse_legal_web_search_citations
from legal_agent.chat.web_search import create_web_search_tool
from legal_agent.clients.rag_client import RAGClient
from legal_agent.config import get_settings
from legal_agent.legal_retrieval.langchain_tools import create_legal_search_tool
from legal_agent.legal_retrieval.retriever import LegalCaseRetriever
from legal_agent.prompts.legal_assistant_chat import LEGAL_ASSISTANT_CHAT_SYSTEM_PROMPT
from legal_agent.workspace_chat.session_store import WorkspaceChatSessionStore

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    LEGAL_ASSISTANT_CHAT_SYSTEM_PROMPT
    + "\n\nCONTEXT: You are embedded in a document drafting workspace for a specific matter. "
    "Prioritise indexed case files when the user’s question relates to their uploads; still run "
    "legal_case_search and legal_web_search when the issue needs external authority."
)

TONE_INSTRUCTIONS = {
    "formal": (
        "\n\nTONE: Write in formal legal language. Use third person. "
        "Maintain professional register throughout."
    ),
    "conversational": (
        "\n\nTONE: Write in a clear, approachable style while maintaining accuracy. "
        "You may use first/second person. Keep legal precision but avoid unnecessarily "
        "complex phrasing."
    ),
    "neutral": (
        "\n\nTONE: Write in a neutral, clear style. Balance formality with readability. "
        "Prioritise clarity over stylistic flourish."
    ),
}

STYLE_INSTRUCTIONS = {
    "precise": (
        "\n\nSTYLE — PRECISE:"
        "\n- Keep the main answer concise: 3–5 sentences where possible."
        "\n- State the answer directly without preamble."
        "\n- You must still include ### References with every [D*], [L*], and [n] used; do not omit citations for brevity."
    ),
    "balanced": (
        "\n\nSTYLE — BALANCED:"
        "\n- Provide a clear answer with supporting context."
        "\n- Use structured sections if the answer has multiple parts."
        "\n- Aim for under 400 words."
    ),
    "detailed": (
        "\n\nSTYLE — DETAILED:"
        "\n- Provide comprehensive analysis with full context."
        "\n- Use markdown headings to organise the response."
        "\n- Include all relevant references and considerations."
        "\n- Trace reasoning step by step."
    ),
}


def _create_rag_tool(rag_client: RAGClient, file_ids: list[str], user_id: str):
    @tool
    async def query_case_documents(query: str) -> str:
        """Search case documents for relevant content. Use this when the user asks
        about facts, clauses, or content from their uploaded case files."""
        logger.debug(f"[workspace_chat] RAG query: {query[:80]}... | user={user_id} | files={file_ids or 'all'}")
        context = await rag_client.query(file_ids, query, user_id=user_id)
        if not context:
            return "No relevant content found in the case files."
        return context

    return query_case_documents


class WorkspaceChatAgent:
    # Max number of distinct (model_id, file_ids) graph instances kept in memory.
    _GRAPH_CACHE_SIZE = 32

    def __init__(self):
        self._pool: AsyncConnectionPool | None = None
        self.checkpointer: AsyncPostgresSaver | None = None
        self.session_store: WorkspaceChatSessionStore | None = None
        self._rag_client: RAGClient | None = None
        self._legal_search_tool = None
        self._llms: dict = {}
        self._base_graphs: dict = {}  # cached per model_id, no per-request tools
        self._rag_graphs: dict = {}   # LRU cache: (model_id, file_ids_key) -> graph

    async def initialize(self, db_url: str, rag_client: RAGClient | None, retriever: LegalCaseRetriever | None = None):
        self._rag_client = rag_client
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

        self.session_store = WorkspaceChatSessionStore(self._pool)
        await self.session_store.setup()

        self._legal_search_tool = create_legal_search_tool(retriever) if retriever else None
        settings = get_settings()
        self._web_search_tool = create_web_search_tool() if settings.serper_api_key else None
        logger.info("WorkspaceChatAgent initialized")

    def _get_llm(self, model_id: str):
        if model_id not in self._llms:
            provider = get_settings().get_langchain_provider_for_model(model_id)
            self._llms[model_id] = init_chat_model(model_id, model_provider=provider)
        return self._llms[model_id]

    def _get_base_graph(self, model_id: str):
        """Cached graph with static tools only — used for history reads."""
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

    def _get_graph(self, model: str, file_ids: list[str], user_id: str = ""):
        model_id = model or get_settings().chat_llm_default_model
        if not file_ids:
            return self._get_base_graph(model_id)

        # Cache graphs by (model_id, sorted file_ids) — user_id is passed inside the tool
        # closure but doesn't affect the graph structure itself.
        cache_key = (model_id, tuple(sorted(file_ids)))
        if cache_key in self._rag_graphs:
            # Refresh RAG tool closure so it uses the current user_id and file_ids order
            self._rag_graphs[cache_key] = self._rag_graphs.pop(cache_key)
            return self._rag_graphs[cache_key]

        llm = self._get_llm(model_id)
        rag_tool = _create_rag_tool(self._rag_client, file_ids, user_id)
        tools = [rag_tool]
        if self._legal_search_tool:
            tools.append(self._legal_search_tool)
        if self._web_search_tool:
            tools.append(self._web_search_tool)
        graph = create_react_agent(llm, tools=tools, checkpointer=self.checkpointer, prompt=SYSTEM_PROMPT)

        # Evict oldest entry when cache is full
        if len(self._rag_graphs) >= self._GRAPH_CACHE_SIZE:
            oldest = next(iter(self._rag_graphs))
            del self._rag_graphs[oldest]
            logger.debug(f"Evicted cached RAG graph for key {oldest}")

        self._rag_graphs[cache_key] = graph
        return graph

    async def stream_response(
        self,
        session_id: str,
        message: str,
        tone: str = "formal",
        style: str = "balanced",
        file_ids: list[str] | None = None,
        user_id: str = "",
        model: str = "",
    ):
        """Yield SSE event dicts for streaming response."""
        logger.info(
            f"[workspace_chat] session={session_id} | model={model} | tone={tone} | "
            f"style={style} | user={user_id} | "
            f"files={len(file_ids or [])} | msg='{message[:100]}'"
        )
        graph = self._get_graph(model, file_ids or [], user_id)
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
                if tool_name in ("query_case_documents", "legal_case_search"):
                    used_legal_tools = True
                if tool_name == "legal_case_search":
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

        # Force citation search if agent used legal tools but skipped web search
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
                logger.warning(f"[workspace_chat] Forced citation search failed: {e}")

        # Emit structured citations from web search results
        if web_search_output:
            citations = parse_legal_web_search_citations(web_search_output)
            if citations:
                yield {"event": "citations", "data": json.dumps(citations)}

        yield {"event": "end", "data": ""}

    def _normalize_content(self, content) -> str:
        """Normalize message content to a plain string (Gemini returns list of dicts)."""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "".join(
                p.get("text", "") if isinstance(p, dict) else str(p) for p in content
            )
        return str(content)

    async def get_history(self, session_id: str) -> list[dict]:
        """Return conversation history as structured turns."""
        # Use any cached LLM (or default) — the LLM is never called, only the checkpointer is read
        model_id = next(iter(self._llms), None) or get_settings().chat_llm_default_model
        graph = self._get_base_graph(model_id)
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
        """Delete all checkpoint data for a session."""
        if not self._pool:
            return
        async with self._pool.connection() as conn:
            await conn.execute("DELETE FROM checkpoint_writes WHERE thread_id = %s", (session_id,))
            await conn.execute("DELETE FROM checkpoint_blobs WHERE thread_id = %s", (session_id,))
            await conn.execute("DELETE FROM checkpoints WHERE thread_id = %s", (session_id,))
        logger.info(f"[workspace_chat] Session {session_id} checkpoints cleared")

    async def close(self):
        if self._pool:
            await self._pool.close()
