"""LangGraph ReAct agent for workspace chat with persistent sessions."""

import json
import logging
import re

from langchain.chat_models import init_chat_model
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.prebuilt import create_react_agent
from pydantic import BaseModel, Field
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from legal_agent.chat.web_search import create_web_search_tool
from legal_agent.clients.rag_client import RAGClient
from legal_agent.config import get_settings
from legal_agent.legal_retrieval.langchain_tools import create_legal_search_tool
from legal_agent.legal_retrieval.retriever import LegalCaseRetriever
from legal_agent.workspace_chat.session_store import WorkspaceChatSessionStore

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are an expert legal assistant specializing in Indian law, embedded in a
document drafting workspace. You help lawyers edit drafts, understand case files, and answer
legal questions in the context of the case they are working on.

CAPABILITIES:
1. Read and analyse case files using query_case_documents.
2. Suggest edits, improvements, and additions to legal drafts.
3. Answer questions about legal provisions, case law, and procedures relevant to the case.
4. Explain legal concepts in context of the documents at hand.

GROUNDING RULES:
1. Always call query_case_documents to search case files first.
2. For legal citations, use legal_case_search for verified case law.
3. After drafting your answer, call legal_web_search EXACTLY ONCE to find supporting citations from SCC Online, Manupatra, Indian Kanoon, and other legal databases. Do NOT call it more than once.
4. When citing web search results, use [1], [2], etc. matching the source numbers returned by legal_web_search. Include the source URLs at the end.
5. Never tell the user "no documents found" — draft or answer using available context.
6. Do NOT fabricate citations not found via tools.

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


class CitationDecision(BaseModel):
    """Whether the response needs external legal citations."""
    needs_citations: bool = Field(
        description="True if the answer makes legal claims, references statutes/case law, "
        "or discusses legal principles that should be backed by authoritative sources. "
        "False for document edits, summaries, formatting, greetings, or factual answers "
        "derived purely from uploaded case files."
    )
    citation_query: str = Field(
        default="",
        description="If needs_citations is true, a concise search query to find "
        "supporting legal sources. Empty string if needs_citations is false."
    )


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


_WEB_CITATION_RE = re.compile(
    r'\[(\d+)\]\s*(.+?)\n'
    r'Source:\s*(.+?)\n'
    r'URL:\s*(.+?)\n'
    r'Snippet:\s*(.+?)\n',
)


def _parse_web_citations(tool_output: str) -> list[dict]:
    """Extract structured citations from legal_web_search tool output."""
    results = []
    for m in _WEB_CITATION_RE.finditer(tool_output):
        results.append({
            "id": int(m.group(1)),
            "case_name": m.group(2).strip(),
            "source": m.group(3).strip(),
            "url": m.group(4).strip(),
            "snippet": m.group(5).strip(),
        })
    return results


class WorkspaceChatAgent:
    def __init__(self):
        self._pool: AsyncConnectionPool | None = None
        self.checkpointer: AsyncPostgresSaver | None = None
        self.session_store: WorkspaceChatSessionStore | None = None
        self._rag_client: RAGClient | None = None
        self._legal_search_tool = None
        self._llms: dict = {}
        self._base_graphs: dict = {}  # cached per model_id, no per-request tools

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
        llm = self._get_llm(model_id)
        rag_tool = _create_rag_tool(self._rag_client, file_ids, user_id)
        tools = [rag_tool]
        if self._legal_search_tool:
            tools.append(self._legal_search_tool)
        if self._web_search_tool:
            tools.append(self._web_search_tool)
        return create_react_agent(llm, tools=tools, checkpointer=self.checkpointer, prompt=SYSTEM_PROMPT)

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
        final_answer_parts: list[str] = []

        async for event in graph.astream_events(
            {"messages": [HumanMessage(content=full_message)]}, config=config, version="v2"
        ):
            kind = event["event"]
            if kind == "on_chat_model_stream":
                token = event["data"]["chunk"].content
                if isinstance(token, list):
                    token = "".join(p.get("text", "") if isinstance(p, dict) else str(p) for p in token)
                if token:
                    final_answer_parts.append(token)
                    yield {"event": "answer", "data": token.replace("\n", "\\n")}
            elif kind == "on_tool_start":
                yield {
                    "event": "tool_call",
                    "data": json.dumps({"name": event["name"], "args": event["data"].get("input", {})}),
                }
            elif kind == "on_tool_end":
                output = event["data"].get("output", "")
                if event.get("name") == "legal_web_search":
                    web_search_output = str(output)
                yield {"event": "tool_result", "data": output}

        # If agent didn't call web search, classify whether citations are needed
        if not web_search_output and self._web_search_tool:
            final_answer = "".join(final_answer_parts)
            if len(final_answer) > 50:
                try:
                    model_id = model or get_settings().chat_llm_default_model
                    classifier = self._get_llm(model_id).with_structured_output(CitationDecision)
                    decision = await classifier.ainvoke(
                        f"User question: {message[:300]}\n\nAssistant answer (truncated): {final_answer[:500]}"
                    )
                    if decision and decision.needs_citations and decision.citation_query:
                        search_query = decision.citation_query
                        yield {
                            "event": "tool_call",
                            "data": json.dumps({"name": "legal_web_search", "args": {"query": search_query}}),
                        }
                        web_search_output = await self._web_search_tool.ainvoke({"query": search_query})
                        yield {"event": "tool_result", "data": web_search_output}
                except Exception as e:
                    logger.warning(f"[workspace_chat] Citation classifier failed: {e}")

        # Emit structured citations from web search results
        if web_search_output:
            citations = _parse_web_citations(web_search_output)
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
