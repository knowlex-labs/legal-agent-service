"""LangGraph ReAct agent for draft chat with persistent sessions."""

import asyncio
import concurrent.futures
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
from legal_agent.chat.session_title import generate_session_title
from legal_agent.chat.web_search import create_web_search_tool
from legal_agent.clients.rag_client import RAGClient
from legal_agent.config import get_settings
from legal_agent.draft_chat.session_store import DraftChatSessionStore
from legal_agent.legal_retrieval.langchain_tools import create_legal_search_tool
from legal_agent.legal_retrieval.retriever import LegalCaseRetriever
from legal_agent.prompts.legal_assistant_chat import LEGAL_ASSISTANT_CHAT_SYSTEM_PROMPT

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    LEGAL_ASSISTANT_CHAT_SYSTEM_PROMPT
    + "\n\nCONTEXT: You are embedded in a matter workspace (drafting and research). "
    "When file IDs are in scope, use query_case_documents for those uploads; always cite "
    "external law and cases via legal_case_search and legal_web_search when those tools exist."
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
    """Create a RAG tool scoped to the given file IDs and user."""

    @tool
    async def query_case_documents(query: str) -> str:
        """Search case documents for relevant content. Use this when the user asks
        about facts, clauses, or content from their uploaded case files."""
        if not file_ids:
            return "No case files provided for this query."

        logger.debug(f"[draft_chat] RAG query: {query[:80]}... | user={user_id} | files={file_ids}")
        context = await rag_client.query(file_ids, query, user_id=user_id)

        if not context:
            return "No relevant content found in the case files."

        return context

    return query_case_documents


def _static_tools(agent: "DraftChatAgent"):
    tools = []
    if agent._legal_search_tool:
        tools.append(agent._legal_search_tool)
    if agent._web_search_tool:
        tools.append(agent._web_search_tool)
    return tools


def _build_sarvam_llm(settings):
    """Build a LangChain chat model bound to Sarvam's OpenAI-compatible endpoint."""
    if not settings.sarvam_api_key:
        raise RuntimeError(
            "SARVAM_API_KEY is not configured but draft chat requested model='sarvam'. "
            "Set SARVAM_API_KEY in .env or pick a different model."
        )
    return init_chat_model(
        settings.sarvam_chat_model,
        model_provider="openai",
        base_url=settings.sarvam_api_base_url,
        api_key=settings.sarvam_api_key,
    )


class DraftChatAgent:
    def __init__(self):
        self._pool: AsyncConnectionPool | None = None
        self.checkpointer: AsyncPostgresSaver | None = None
        self.session_store: DraftChatSessionStore | None = None
        self._rag_client: RAGClient | None = None
        self._legal_search_tool = None
        self._web_search_tool = None
        self._graphs: dict[str, object] = {}
        self._ready = asyncio.Event()

    async def initialize(
        self,
        db_url: str,
        rag_client: RAGClient,
        retriever: LegalCaseRetriever | None = None,
    ):
        self._rag_client = rag_client
        settings = get_settings()
        self._legal_search_tool = create_legal_search_tool(retriever) if retriever else None
        self._web_search_tool = create_web_search_tool() if settings.serper_api_key else None

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

        self.session_store = DraftChatSessionStore(self._pool)
        await self.session_store.setup()

        self._compile_graphs_background()
        logger.info("DraftChatAgent initialized (graphs compiling in background)")

    def _compile_graphs_background(self):
        """Compile LangGraph graphs in a background thread."""

        loop = asyncio.get_event_loop()

        def _compile():
            settings = get_settings()
            chat_models = settings.get_chat_models()
            for provider_key, (model_name, langchain_provider) in chat_models.items():
                llm = init_chat_model(model_name, model_provider=langchain_provider)
                tools = _static_tools(self)
                self._graphs[provider_key] = create_react_agent(
                    model=llm,
                    tools=tools,
                    checkpointer=self.checkpointer,
                    prompt=SYSTEM_PROMPT,
                )
                logger.info(f"[draft_chat] Compiled graph for {provider_key} ({model_name})")
            loop.call_soon_threadsafe(self._ready.set)
            logger.info("[draft_chat] All graphs compiled and ready")

        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        executor.submit(_compile)

    def _tools_for_request(self, file_ids: list[str], user_id: str) -> list:
        tools = []
        if file_ids and self._rag_client:
            tools.append(_create_rag_tool(self._rag_client, file_ids, user_id))
        tools.extend(_static_tools(self))
        return tools

    async def _get_graph(self, model: str, file_ids: list[str], user_id: str = ""):
        """Get or create a graph for the given model and file_ids."""
        await self._ready.wait()

        settings = get_settings()

        # Sarvam uses an OpenAI-compatible endpoint. It isn't pre-compiled in
        # _graphs (tool-calling support on sarvam-* models is less battle-tested,
        # so we always build fresh per request to keep the request-scoped tool
        # set explicit).
        if model == "sarvam":
            llm = _build_sarvam_llm(settings)
            req_tools = self._tools_for_request(file_ids, user_id)
            return create_react_agent(
                model=llm,
                tools=req_tools,
                checkpointer=self.checkpointer,
                prompt=SYSTEM_PROMPT,
            )

        chat_models = settings.get_chat_models()
        provider_key = model if model in chat_models else settings.chat_llm_default_provider
        model_name, langchain_provider = chat_models[provider_key]
        llm = init_chat_model(model_name, model_provider=langchain_provider)
        req_tools = self._tools_for_request(file_ids, user_id)

        if not file_ids:
            if provider_key not in self._graphs:
                logger.warning(
                    f"[draft_chat] Graph '{model}' not found, falling back to {settings.chat_llm_default_provider}"
                )
                provider_key = settings.chat_llm_default_provider
            if req_tools == _static_tools(self):
                return self._graphs[provider_key]

        return create_react_agent(
            model=llm,
            tools=req_tools,
            checkpointer=self.checkpointer,
            prompt=SYSTEM_PROMPT,
        )

    async def stream_response(
        self,
        session_id: str,
        message: str,
        tone: str = "formal",
        style: str = "balanced",
        file_ids: list[str] | None = None,
        user_id: str = "",
        model: str = "openai",
    ):
        """Yield SSE event dicts for streaming response."""
        logger.info(
            f"[draft_chat] session={session_id} | model={model} | tone={tone} | "
            f"style={style} | user={user_id} | files={len(file_ids or [])} | msg='{message[:100]}'"
        )
        graph = await self._get_graph(model, file_ids or [], user_id)
        config = {"configurable": {"thread_id": session_id}}

        tone_suffix = TONE_INSTRUCTIONS.get(tone, TONE_INSTRUCTIONS["formal"])
        style_suffix = STYLE_INSTRUCTIONS.get(style, STYLE_INSTRUCTIONS["balanced"])
        full_message = f"{message}\n\n---\nRESPONSE INSTRUCTIONS:{tone_suffix}{style_suffix}"

        # Kick off auto-title generation in parallel if this is the session's
        # first message. The task runs alongside the main stream; the
        # session_title event is emitted as soon as the title is ready.
        title_task: asyncio.Task[str | None] | None = None
        try:
            prior_state = await graph.aget_state(config)
            is_first_message = not prior_state or not getattr(prior_state, "values", None) \
                or not prior_state.values.get("messages")
        except Exception:
            logger.exception("[draft_chat] aget_state failed; skipping session_title")
            is_first_message = False
        if is_first_message and message and message.strip():
            resolved_model = model or get_settings().chat_llm_default_model
            title_task = asyncio.create_task(
                generate_session_title(message, model=resolved_model)
            )

        web_search_output: str | None = None
        used_legal_tools = False
        legal_search_queries: list[str] = []

        async def _maybe_flush_title():
            nonlocal title_task
            if title_task is None or not title_task.done():
                return None
            try:
                t = title_task.result()
            except Exception:
                logger.exception("[draft_chat] session_title task raised")
                t = None
            title_task = None
            return t

        async for event in graph.astream_events(
            {"messages": [HumanMessage(content=full_message)]}, config=config, version="v2"
        ):
            kind = event["event"]
            if kind == "on_chat_model_stream":
                token = event["data"]["chunk"].content
                if isinstance(token, list):
                    token = "".join(
                        p.get("text", "") if isinstance(p, dict) else str(p) for p in token
                    )
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

            early_title = await _maybe_flush_title()
            if early_title:
                yield {"event": "session_title", "data": early_title}

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
                logger.warning(f"[draft_chat] Forced citation search failed: {e}")

        if web_search_output:
            citations = parse_legal_web_search_citations(web_search_output)
            if citations:
                yield {"event": "citations", "data": json.dumps(citations)}

        # Drain the title task one last time before closing the stream.
        if title_task is not None:
            try:
                title = await title_task
            except Exception:
                logger.exception("[draft_chat] awaiting session_title task failed")
                title = None
            if title:
                yield {"event": "session_title", "data": title}

        yield {"event": "end", "data": ""}

    def _normalize_content(self, content) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "".join(
                p.get("text", "") if isinstance(p, dict) else str(p) for p in content
            )
        return str(content)

    async def get_history(self, session_id: str, model: str = "openai") -> list[dict]:
        """Return conversation history as structured turns."""
        graph = await self._get_graph(model, [])
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
        logger.info(f"[draft_chat] Session {session_id} checkpoints cleared")

    async def close(self):
        if self._pool:
            await self._pool.close()
