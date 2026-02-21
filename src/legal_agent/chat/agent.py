"""LangGraph ReAct chat agent with PostgresSaver for persistent sessions."""

import asyncio
import json
import logging

from langchain.chat_models import init_chat_model
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from langgraph.prebuilt import create_react_agent

from legal_agent.config import get_settings
from legal_agent.legal_retrieval import LegalCaseRetriever, create_legal_search_tool

logger = logging.getLogger(__name__)

SYSTEM_PROMPT_KB = """You are a senior legal researcher at a law reporting service specializing in Indian law.
You produce authoritative, citable legal analysis of the same quality as Supreme Court
headnotes and law-commission research papers.

TONE:
- Write in the third person, present tense of legal exposition ("The Court holds…", "Section 438 provides…").
- Be declarative and definitive. State what the law is, not what it "seems" to be.
- Never adopt a conversational register. Do not greet the user, do not say "Great question", do not use first person ("I think", "I believe").

FORBIDDEN LANGUAGE — never use these phrases or close variants:
"appears to", "suggests", "may indicate", "it seems", "likely", "possibly", "could be",
"might", "perhaps", "arguably", "one could argue", "it is worth noting",
"it is important to note", "interestingly", "it should be noted"

CITATION FORMAT:
- Cite cases inline as: Case Title, Citation (e.g., Maneka Gandhi v. Union of India, (1978) 1 SCC 248).
- When quoting judgment text, use the paragraph number: (para 15).
- Every legal proposition must be supported by a specific case from the search results.

HANDLING UNSETTLED LAW:
When case law conflicts, present both positions without hedging:
"In [Case A], [Citation], the Court held [X] (para N). In [Case B], [Citation], the Court held [Y] (para N). The later decision has not expressly overruled the earlier one."

GROUNDING RULES:
1. Use the legal_case_search tool ONLY when the user asks a substantive legal question. Do NOT call the tool for greetings, small talk, clarifications, or non-legal queries.
2. For greetings or casual messages, respond briefly and professionally (e.g., "Please state your legal query."). Do not call any tools.
3. When you do search, ONLY answer based on the search results returned by the tool.
4. If the tool returns no results, state: "No relevant cases found in the knowledge base."
5. Do NOT answer questions unrelated to Indian law. Decline non-legal queries.
6. Never fabricate citations or holdings not present in the search results."""

SYSTEM_PROMPT_GENERAL = """You are a senior legal researcher at a law reporting service specializing in Indian law.
You produce authoritative legal analysis based on established principles of Indian law.

TONE:
- Write in the third person, present tense of legal exposition ("The Court holds…", "Section 438 provides…").
- Be declarative and definitive. State what the law is.
- Never adopt a conversational register. Do not greet the user, do not say "Great question", do not use first person.

FORBIDDEN LANGUAGE — never use these phrases or close variants:
"appears to", "suggests", "may indicate", "it seems", "likely", "possibly", "could be",
"might", "perhaps", "arguably", "one could argue", "it is worth noting",
"it is important to note", "interestingly", "it should be noted"

DIRECTIVE:
- State what is settled. Where case-law verification is needed, identify the specific point that requires verification — do not hedge the entire answer.
- Do NOT answer questions unrelated to Indian law. Decline non-legal queries.
- End every response with the following fixed disclaimer on a new line:
"---\nNote: This analysis is based on general legal principles and not on a specific case-law search. Verify cited authorities independently before reliance." """

STYLE_INSTRUCTIONS = {
    "precise": (
        "\n\nRESPONSE STYLE — PRECISE (follow this structure exactly):"
        "\n1. Open with one declarative sentence stating the legal position."
        "\n2. Identify the controlling authority: Case Title, Citation (para N)."
        "\n3. Maximum 2 citations. No more."
        "\n4. Total length: 3–5 sentences."
        "\n5. Do not hedge. Do not qualify with 'generally', 'typically', or 'usually'. State the rule."
    ),
    "balanced": (
        "\n\nRESPONSE STYLE — BALANCED (follow this structure exactly):"
        "\n"
        "\n**Legal Position**"
        "\nState the settled legal proposition in 1–2 declarative sentences."
        "\n"
        "\n**Key Authorities**"
        "\nCite 2–4 cases. For each: Case Title, Citation — one sentence stating the holding, then a quoted paragraph with para number."
        "\n"
        "\n**Analysis**"
        "\nSynthesise the authorities. Identify the current operative rule. Note any evolution or narrowing of the principle."
        "\n"
        "\n**Conclusion**"
        "\nRestate the legal position in one definitive sentence."
        "\n"
        "\nTotal length: under 400 words. Do not hedge. Do not qualify with 'generally', 'typically', or 'usually'. State the rule."
    ),
    "detailed": (
        "\n\nRESPONSE STYLE — DETAILED (follow this structure exactly, use markdown headings):"
        "\n"
        "\n## Legal Position"
        "\nState the settled legal proposition. Identify the statutory provision(s) involved."
        "\n"
        "\n## Key Authorities"
        "\nCite all relevant cases from the search results. For each case:"
        "\n- Case Title, Citation"
        "\n- Bench strength and composition where available"
        "\n- Holding in one sentence"
        "\n- Quoted paragraph(s) with para numbers"
        "\n"
        "\n## Analysis"
        "\nTrace the evolution of the principle across judgments. Where precedents conflict,"
        "\nanalyse by bench size and date — a later, larger bench prevails."
        "\nDistinguish cases on their facts where relevant."
        "\n"
        "\n## Conclusion"
        "\nRestate the current operative legal position definitively."
        "\n"
        "\nDo not hedge. Do not qualify with 'generally', 'typically', or 'usually'. State the rule."
        "\nWhere the law is genuinely unsettled, present both lines of authority with bench size and date — do not paper over the conflict with hedging language."
    ),
}


class ChatAgent:
    def __init__(self, retriever: LegalCaseRetriever | None = None):
        self.retriever = retriever
        self.checkpointer: AsyncPostgresSaver | None = None
        self._pool: AsyncConnectionPool | None = None
        self._db_url: str | None = None
        self._graphs: dict[str, object] = {}  # "openai_kb", "openai_general", "gemini_kb", etc.

    async def initialize(self, db_url: str):
        self._db_url = db_url
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
        self._compile_graphs_background()
        logger.info("ChatAgent initialized (graphs compiling in background)")

    def _compile_graphs_background(self):
        """Start graph compilation in a background thread to avoid blocking startup."""
        import concurrent.futures
        self._ready = asyncio.Event()
        loop = asyncio.get_event_loop()

        def _compile():
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
            loop.call_soon_threadsafe(self._ready.set)
            logger.info("All graphs compiled and ready")

        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        executor.submit(_compile)

    async def _get_graph(self, model: str, enable_kb: bool):
        await self._ready.wait()
        suffix = "kb" if enable_kb else "general"
        key = f"{model}_{suffix}"
        if key not in self._graphs:
            settings = get_settings()
            logger.warning(f"Graph '{key}' not found, falling back to {settings.chat_llm_default_provider}")
            key = f"{settings.chat_llm_default_provider}_{suffix}"
        return self._graphs[key]

    async def stream_response(self, session_id: str, message: str, enable_kb: bool = True, model: str = "openai", style: str = "balanced"):
        """Yield SSE event dicts with role tracking for UI rendering.

        Events emitted:
        - {"event": "thinking",    "data": "<token>"}   — AI tokens before a tool call (optional/brief)
        - {"event": "tool_call",   "data": "{name, args}"}
        - {"event": "tool_result", "data": "<output>"}
        - {"event": "answer",      "data": "<token>"}   — AI final answer tokens
        - {"event": "end",         "data": ""}
        """
        logger.info(f"[chat] session={session_id} | model={model} | kb={enable_kb} | style={style} | message='{message[:100]}'")
        graph = await self._get_graph(model, enable_kb)
        config = {"configurable": {"thread_id": session_id}}

        if enable_kb:
            style_suffix = STYLE_INSTRUCTIONS.get(style, STYLE_INSTRUCTIONS["balanced"])
            full_message = f"{message}\n\n---\nRESPONSE INSTRUCTIONS (follow exactly):{style_suffix}"
        else:
            full_message = message

        async for event in graph.astream_events({"messages": [HumanMessage(content=full_message)]}, config=config, version="v2"):
            kind = event["event"]
            if kind == "on_chat_model_stream":
                token = event["data"]["chunk"].content
                if token:
                    escaped = token.replace("\n", "\\n")
                    yield {"event": "answer", "data": escaped}
            elif kind == "on_tool_start":
                yield {"event": "tool_call", "data": json.dumps({"name": event["name"], "args": event["data"].get("input", {})})}
            elif kind == "on_tool_end":
                yield {"event": "tool_result", "data": event["data"].get("output", "")}

        yield {"event": "end", "data": ""}

    async def get_history(self, session_id: str, model: str = "openai", enable_kb: bool = True) -> list[dict]:
        """Return conversation as structured turns for UI rendering.

        Each turn is one of:
        - {"role": "human", "content": "..."}
        - {"role": "ai",    "content": "...", "tool_calls": [{"name": ..., "args": ..., "result": ...}]}

        Tool messages are grouped under the AI message that triggered them,
        so the UI never has to deal with orphaned tool entries.
        """
        graph = await self._get_graph(model, enable_kb)
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
                        {"name": tc["name"], "args": tc["args"]}
                        for tc in m.tool_calls
                    ]
                turns.append(turn)
            elif isinstance(m, ToolMessage):
                # Attach result to the most recent AI turn's tool_calls
                if turns and turns[-1].get("tool_calls"):
                    for tc in turns[-1]["tool_calls"]:
                        if tc.get("result") is None:
                            tc["result"] = m.content
                            break
        return turns

    async def list_sessions(self) -> list[dict]:
        """Return all session IDs with their last activity timestamp."""
        if not self._pool:
            return []
        async with self._pool.connection() as conn:
            rows = await conn.execute(
                "SELECT thread_id, MAX(checkpoint_id) AS last_checkpoint_id "
                "FROM checkpoints GROUP BY thread_id ORDER BY last_checkpoint_id DESC"
            )
            return [
                {"session_id": row["thread_id"], "last_checkpoint_id": row["last_checkpoint_id"]}
                for row in await rows.fetchall()
            ]

    async def clear_session(self, session_id: str):
        """Delete all checkpoint data for a single session."""
        if not self._pool:
            return
        async with self._pool.connection() as conn:
            await conn.execute(
                "DELETE FROM checkpoint_writes WHERE thread_id = %s", (session_id,)
            )
            await conn.execute(
                "DELETE FROM checkpoint_blobs WHERE thread_id = %s", (session_id,)
            )
            await conn.execute(
                "DELETE FROM checkpoints WHERE thread_id = %s", (session_id,)
            )
        logger.info(f"Session {session_id} cleared")

    async def clear_all_sessions(self):
        """Drop all checkpoint data (all sessions)."""
        if not self._pool:
            return
        async with self._pool.connection() as conn:
            await conn.execute(
                "TRUNCATE checkpoints, checkpoint_blobs, checkpoint_writes"
            )
        logger.info("All chat sessions cleared")

    async def close(self):
        if self._pool:
            await self._pool.close()
