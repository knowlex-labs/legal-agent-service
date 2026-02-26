"""LangGraph ReAct agent for draft chat with persistent sessions."""

import asyncio
import json
import logging

from langchain.chat_models import init_chat_model
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.prebuilt import create_react_agent
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from legal_agent.clients.rag_client import RAGClient
from legal_agent.config import get_settings
from legal_agent.draft_chat.session_store import DraftChatSessionStore

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are an expert legal assistant specializing in Indian law, embedded in a
document drafting workspace. You help lawyers edit drafts, understand case files, and answer
legal questions in the context of the case they are working on.

CAPABILITIES:
1. Read and analyse case files using the search tool when file IDs are provided.
2. Suggest edits, improvements, and additions to legal drafts.
3. Answer questions about legal provisions, case law, and procedures relevant to the case.
4. Explain legal concepts in context of the documents at hand.

GROUNDING RULES:
1. Use the query_case_documents tool to search case files when the user asks about document
   content, facts, or needs information from uploaded files.
2. Only answer based on information from search results when referencing case files.
3. For general legal questions (not about specific documents), answer from your legal knowledge.
4. Do NOT fabricate case citations or document content not present in search results.
5. If the tool returns no results, say so clearly.

OUTPUT FORMAT:
- Use markdown for formatting.
- When suggesting draft edits, clearly mark additions and deletions.
- Cite specific sections or paragraphs when referencing documents."""

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
        "\n- Keep responses concise: 3–5 sentences maximum."
        "\n- State the answer directly without preamble."
        "\n- Maximum 2 citations or references."
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


def _create_rag_tool(rag_client: RAGClient, file_ids: list[str]):
    """Create a RAG tool scoped to the given file IDs."""

    @tool
    async def query_case_documents(query: str) -> str:
        """Search case documents for relevant content. Use this when the user asks
        about facts, clauses, or content from their uploaded case files."""
        if not file_ids:
            return "No case files provided for this query."

        logger.debug(f"[draft_chat] RAG query: {query[:80]}... | files={file_ids}")
        context = await rag_client.query(file_ids, query)

        if not context:
            return "No relevant content found in the case files."

        return context

    return query_case_documents


class DraftChatAgent:
    def __init__(self):
        self._pool: AsyncConnectionPool | None = None
        self.checkpointer: AsyncPostgresSaver | None = None
        self.session_store: DraftChatSessionStore | None = None
        self._rag_client: RAGClient | None = None
        self._graphs: dict[str, object] = {}
        self._ready = asyncio.Event()

    async def initialize(self, db_url: str, rag_client: RAGClient):
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

        self.session_store = DraftChatSessionStore(self._pool)
        await self.session_store.setup()

        self._compile_graphs_background()
        logger.info("DraftChatAgent initialized (graphs compiling in background)")

    def _compile_graphs_background(self):
        """Compile LangGraph graphs in a background thread."""
        import concurrent.futures

        loop = asyncio.get_event_loop()

        def _compile():
            settings = get_settings()
            chat_models = settings.get_chat_models()
            # We compile graphs without tools — tools are added per-request
            # because file_ids differ per message. We use a no-tool graph as base
            # and create tool-equipped graphs on demand.
            for provider_key, (model_name, langchain_provider) in chat_models.items():
                llm = init_chat_model(model_name, model_provider=langchain_provider)
                # Base graph (no tools) for general questions
                self._graphs[provider_key] = create_react_agent(
                    model=llm,
                    tools=[],
                    checkpointer=self.checkpointer,
                    prompt=SYSTEM_PROMPT,
                )
                logger.info(f"[draft_chat] Compiled graph for {provider_key} ({model_name})")
            loop.call_soon_threadsafe(self._ready.set)
            logger.info("[draft_chat] All graphs compiled and ready")

        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        executor.submit(_compile)

    async def _get_graph(self, model: str, file_ids: list[str]):
        """Get or create a graph for the given model and file_ids."""
        await self._ready.wait()

        if file_ids and self._rag_client:
            # Create a tool-equipped graph on demand for this request
            settings = get_settings()
            chat_models = settings.get_chat_models()
            provider_key = model if model in chat_models else settings.chat_llm_default_provider
            model_name, langchain_provider = chat_models[provider_key]
            llm = init_chat_model(model_name, model_provider=langchain_provider)
            rag_tool = _create_rag_tool(self._rag_client, file_ids)
            return create_react_agent(
                model=llm,
                tools=[rag_tool],
                checkpointer=self.checkpointer,
                prompt=SYSTEM_PROMPT,
            )

        # No files — use pre-compiled graph
        key = model
        if key not in self._graphs:
            settings = get_settings()
            logger.warning(f"[draft_chat] Graph '{key}' not found, falling back to {settings.chat_llm_default_provider}")
            key = settings.chat_llm_default_provider
        return self._graphs[key]

    async def stream_response(
        self,
        session_id: str,
        message: str,
        tone: str = "formal",
        style: str = "balanced",
        file_ids: list[str] | None = None,
        model: str = "openai",
    ):
        """Yield SSE event dicts for streaming response."""
        logger.info(
            f"[draft_chat] session={session_id} | model={model} | tone={tone} | "
            f"style={style} | files={len(file_ids or [])} | msg='{message[:100]}'"
        )
        graph = await self._get_graph(model, file_ids or [])
        config = {"configurable": {"thread_id": session_id}}

        tone_suffix = TONE_INSTRUCTIONS.get(tone, TONE_INSTRUCTIONS["formal"])
        style_suffix = STYLE_INSTRUCTIONS.get(style, STYLE_INSTRUCTIONS["balanced"])
        full_message = f"{message}\n\n---\nRESPONSE INSTRUCTIONS:{tone_suffix}{style_suffix}"

        async for event in graph.astream_events(
            {"messages": [HumanMessage(content=full_message)]}, config=config, version="v2"
        ):
            kind = event["event"]
            if kind == "on_chat_model_stream":
                token = event["data"]["chunk"].content
                if token:
                    escaped = token.replace("\n", "\\n")
                    yield {"event": "answer", "data": escaped}
            elif kind == "on_tool_start":
                yield {
                    "event": "tool_call",
                    "data": json.dumps({"name": event["name"], "args": event["data"].get("input", {})}),
                }
            elif kind == "on_tool_end":
                yield {"event": "tool_result", "data": event["data"].get("output", "")}

        yield {"event": "end", "data": ""}

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
                turns.append({"role": "human", "content": m.content})
            elif isinstance(m, AIMessage):
                turn = {"role": "ai", "content": m.content}
                if m.tool_calls:
                    turn["tool_calls"] = [
                        {"name": tc["name"], "args": tc["args"]} for tc in m.tool_calls
                    ]
                turns.append(turn)
            elif isinstance(m, ToolMessage):
                if turns and turns[-1].get("tool_calls"):
                    for tc in turns[-1]["tool_calls"]:
                        if tc.get("result") is None:
                            tc["result"] = m.content
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
