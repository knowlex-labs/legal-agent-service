"""LangGraph ReAct agent for workspace chat with persistent sessions."""

import asyncio
import json
import logging
import re

from langchain.chat_models import init_chat_model
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.prebuilt import create_react_agent
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from legal_agent.chat.citation_utils import parse_legal_web_search_citations
from legal_agent.chat.firecrawl_verify import ClaimList, ClaimVerification, verify_claims
from legal_agent.chat.query_classifier import classify_query, is_trivial_message
from legal_agent.chat.session_title import generate_session_title
from legal_agent.chat.legal_web_search_firecrawl import create_legal_web_search_tool
from legal_agent.clients.rag_client import RAGClient
from legal_agent.config import get_settings
from legal_agent.legal_retrieval.langchain_tools import create_legal_search_tool
from legal_agent.legal_retrieval.retriever import LegalCaseRetriever
from legal_agent.prompts.fact_dense_draft import FACT_DENSE_DRAFT_SYSTEM_PROMPT
from legal_agent.prompts.legal_assistant_chat import LEGAL_ASSISTANT_CHAT_SYSTEM_PROMPT
from legal_agent.prompts.query_classifier import TRIVIAL_REPLY_SYSTEM_PROMPT
from legal_agent.prompts.verify_rewrite import (
    CLAIM_EXTRACTION_SYSTEM_PROMPT,
    VERIFY_REWRITE_SYSTEM_PROMPT,
)
from legal_agent.workspace_chat.session_store import WorkspaceChatSessionStore

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    LEGAL_ASSISTANT_CHAT_SYSTEM_PROMPT
    + "\n\n═══════════════════════════════════════════════════════════════════"
    "\nWORKSPACE CHAT CONTEXT"
    "\n═══════════════════════════════════════════════════════════════════"
    "\nYou are embedded in a document drafting workspace for a specific matter. "
    "Prioritise indexed case files (query_case_documents) when the user's question "
    "relates to their uploads. Run legal_web_search when the answer needs external "
    "authority from LiveLaw, SCC Online, or Bar and Bench."
    "\n\nQUALITY BAR: the user is a practising Indian advocate. A good answer names "
    "the case, bench strength, leading opinion author, reporter citation, ratio, "
    "and — where known — subsequent treatment. Follow the INDIAN CASE-LAW CITATION "
    "FORM above. Do not strip detail for brevity; strip filler instead."
)

DOCUMENT_ONLY_SYSTEM_PROMPT = """You are an expert legal assistant specializing in Indian law.

GREETING RULE: If the user sends a greeting, pleasantry, or purely social message (e.g. "Hi", "Hello", "Thanks"), respond naturally and briefly. Do NOT call any tools.

STRICT RULE (for all legal questions): You MUST answer ONLY from the documents provided via the query_case_documents tool.
- Call query_case_documents first for every legal question about the case.
- Do NOT use your general knowledge or legal_web_search to answer (web search is disabled for this turn).
- If the answer is not found in the documents, respond: "I could not find information about this in the provided documents."
- Do NOT speculate, infer, or supplement with outside knowledge.

CITATION DISCIPLINE (non-negotiable):
- Every material proposition must cite the source chunk from query_case_documents.
- Cite inline as [D1], [D2], … in the order chunks appear in the tool result.
- In ### References, each [Dn] must include a short quoted phrase (≤25 words) from the source so the reader can locate it. If multiple documents are referenced, include the file name; do not include page numbers.
- Do not invent citations.

OUTPUT FORMAT:
- Lead with a direct 1–2 sentence answer to the question.
- For multi-part answers, use bullet points or a numbered list — one point per issue.
- Use ## headings only if the answer covers more than two clearly distinct topics.
- Where the document text directly supports your answer, quote the relevant passage (≤30 words) in the body, then cite it with [Dn].
- Re-use the same [Dn] marker whenever you refer to the same chunk again — do not create a new marker for the same source.
- If query_case_documents returns nothing relevant, respond: "This information does not appear in the indexed documents. You may want to check if the relevant document has been uploaded, or rephrase the question."
- ### References always last. Never answer from general principles or external sources — only from the provided documents."""

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

        # legal_case_search (internal SC/HC judgment DB via LegalCaseRetriever) is
        # temporarily disabled from the workspace chat toolset pending pipeline
        # readiness. When it returns, it should be always-on (no request flag) —
        # searching our own indexed judgment DB is default-allowed. Keep the
        # constructor wiring here so re-enabling is a one-line change.
        self._legal_search_tool = create_legal_search_tool(retriever) if retriever else None
        settings = get_settings()
        # Firecrawl is primary (scrapes full article text); Serper is the fallback
        # inside create_legal_web_search_tool itself. Tool is enabled if either key
        # is configured — its fallback chain is internal.
        self._web_search_tool = (
            create_legal_web_search_tool()
            if (settings.firecrawl_api_key or settings.serper_api_key)
            else None
        )
        logger.info("WorkspaceChatAgent initialized")

    def _get_llm(self, model_id: str):
        if model_id not in self._llms:
            provider = get_settings().get_langchain_provider_for_model(model_id)
            # Low temperature for legal chat — discourages the model from
            # "helpfully" filling gaps with training-memory detail when the
            # tool output is thin. Critical for Flash-class models that
            # otherwise default to ~0.7.
            self._llms[model_id] = init_chat_model(
                model_id, model_provider=provider, temperature=0.2
            )
        return self._llms[model_id]

    def _get_base_graph(self, model_id: str, web_search: bool = False):
        """Cached graph with no per-request RAG tool — used when file_ids are empty.

        `web_search=True` binds `legal_web_search` (Firecrawl with Serper fallback).
        `web_search=False` binds no tools; the LLM answers from conversation state
        and will politely decline legal research per DOCUMENT_ONLY_SYSTEM_PROMPT.
        """
        cache_key = (model_id, web_search)
        if cache_key not in self._base_graphs:
            llm = self._get_llm(model_id)
            tools = []
            if web_search and self._web_search_tool:
                tools.append(self._web_search_tool)
            prompt = SYSTEM_PROMPT if web_search else DOCUMENT_ONLY_SYSTEM_PROMPT
            self._base_graphs[cache_key] = create_react_agent(
                llm, tools=tools, checkpointer=self.checkpointer, prompt=prompt
            )
        return self._base_graphs[cache_key]

    def _get_graph(
        self,
        model: str,
        file_ids: list[str],
        user_id: str = "",
        web_search: bool = False,
    ):
        """Pick a graph whose tools match the request.

        Tool matrix (legal_case_search is currently disabled pipeline-wide):
        - web_search=False, no files  → no tools (DOCUMENT_ONLY_SYSTEM_PROMPT)
        - web_search=False, files      → RAG only (DOCUMENT_ONLY_SYSTEM_PROMPT)
        - web_search=True,  no files  → legal_web_search only (SYSTEM_PROMPT)
        - web_search=True,  files      → RAG + legal_web_search (SYSTEM_PROMPT)
        """
        model_id = model or get_settings().chat_llm_default_model
        if not file_ids:
            return self._get_base_graph(model_id, web_search=web_search)

        # Cache graphs by (model_id, web_search, sorted file_ids). user_id is
        # passed inside the tool closure but doesn't affect graph structure.
        cache_key = (model_id, web_search, tuple(sorted(file_ids)))
        if cache_key in self._rag_graphs:
            # Refresh to LRU-tail
            self._rag_graphs[cache_key] = self._rag_graphs.pop(cache_key)
            return self._rag_graphs[cache_key]

        llm = self._get_llm(model_id)
        tools = [_create_rag_tool(self._rag_client, file_ids, user_id)]
        if web_search and self._web_search_tool:
            tools.append(self._web_search_tool)
        prompt = SYSTEM_PROMPT if web_search else DOCUMENT_ONLY_SYSTEM_PROMPT
        graph = create_react_agent(
            llm, tools=tools, checkpointer=self.checkpointer, prompt=prompt
        )

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
        web_search: bool = False,
    ):
        """Yield SSE event dicts for streaming response."""
        logger.info(
            f"[workspace_chat] session={session_id} | model={model} | tone={tone} | "
            f"style={style} | user={user_id} | "
            f"files={len(file_ids or [])} | web_search={web_search} | msg='{message[:100]}'"
        )

        # No documents selected and web search disabled — there's no source the
        # agent can legitimately answer from. Reply with an explicit, actionable
        # message instead of letting the LLM hallucinate or stay silent.
        # Web-search-only queries (file_ids=[], web_search=True) are valid and
        # fall through to the verify pipeline below.
        if not file_ids and not web_search:
            model_id = model or get_settings().chat_llm_default_model
            base_graph = self._get_base_graph(model_id, web_search=False)
            config = {"configurable": {"thread_id": session_id}}
            reply = (
                "No documents are selected. Please select at least one document "
                "from the case workspace to chat about, or enable web search to "
                "ask general legal questions."
            )
            yield {"event": "answer", "data": reply.replace("\n", "\\n")}
            try:
                await base_graph.aupdate_state(
                    config,
                    {"messages": [HumanMessage(content=message), AIMessage(content=reply)]},
                )
            except Exception:
                logger.exception("[workspace_chat] failed to persist no-docs reply")
            yield {"event": "end", "data": ""}
            return

        # web_search=True routes through the draft → extract → verify → rewrite
        # pipeline so answers are factually dense but each claim is verified via
        # Firecrawl search-only (1 credit per claim, no scrape).
        if web_search:
            async for sse in self._run_verify_pipeline(
                session_id=session_id,
                message=message,
                tone=tone,
                style=style,
                file_ids=file_ids or [],
                user_id=user_id,
                model=model,
            ):
                yield sse
            return

        graph = self._get_graph(model, file_ids or [], user_id, web_search=web_search)
        config = {"configurable": {"thread_id": session_id}}

        tone_suffix = TONE_INSTRUCTIONS.get(tone, TONE_INSTRUCTIONS["formal"])
        style_suffix = STYLE_INSTRUCTIONS.get(style, STYLE_INSTRUCTIONS["balanced"])
        instruction_message = SystemMessage(content=f"RESPONSE INSTRUCTIONS:{tone_suffix}{style_suffix}")

        # Kick off auto-title generation in parallel if this is the session's
        # first message. The task runs alongside the main stream; the
        # session_title event is emitted as soon as the title is ready.
        title_task: asyncio.Task[str | None] | None = None
        try:
            prior_state = await graph.aget_state(config)
            is_first_message = not prior_state or not getattr(prior_state, "values", None) \
                or not prior_state.values.get("messages")
        except Exception:
            logger.exception("[workspace_chat] aget_state failed; skipping session_title")
            is_first_message = False
        if is_first_message and message and message.strip():
            resolved_model = model or get_settings().chat_llm_default_model
            title_task = asyncio.create_task(
                generate_session_title(message, model=resolved_model)
            )

        web_search_output: str | None = None
        rag_output: str | None = None
        answer_chunks: list[str] = []
        streamed_cleanly = False

        async def _maybe_flush_title():
            nonlocal title_task
            if title_task is None or not title_task.done():
                return None
            try:
                t = title_task.result()
            except Exception:
                logger.exception("[workspace_chat] session_title task raised")
                t = None
            title_task = None
            return t

        try:
            async for event in graph.astream_events(
                {"messages": [instruction_message, HumanMessage(content=message)]}, config=config, version="v2"
            ):
                kind = event["event"]
                if kind == "on_chat_model_stream":
                    token = event["data"]["chunk"].content
                    if isinstance(token, list):
                        token = "".join(p.get("text", "") if isinstance(p, dict) else str(p) for p in token)
                    if token:
                        answer_chunks.append(token)
                        yield {"event": "answer", "data": token.replace("\n", "\\n")}
                elif kind == "on_tool_start":
                    tool_name = event["name"]
                    yield {
                        "event": "tool_call",
                        "data": json.dumps({"name": tool_name, "args": event["data"].get("input", {})}),
                    }
                elif kind == "on_tool_end":
                    output = event["data"].get("output", "")
                    tool_name = event.get("name")
                    if tool_name == "legal_web_search":
                        web_search_output = str(output)
                    elif tool_name == "query_case_documents":
                        rag_output = str(output)
                    yield {"event": "tool_result", "data": output}

                early_title = await _maybe_flush_title()
                if early_title:
                    yield {"event": "session_title", "data": early_title}
            streamed_cleanly = True
        finally:
            # Bug-9 safeguard: when the SSE consumer disconnects mid-stream
            # (page reload, navigation, network drop), astream_events is
            # cancelled and LangGraph's auto-checkpoint may not have committed
            # the final AIMessage. Idempotently flush whatever was generated so
            # session history isn't lost.
            if not streamed_cleanly and answer_chunks:
                final_answer = "".join(answer_chunks).strip()
                if final_answer:
                    try:
                        state = await graph.aget_state(config)
                        messages = (
                            state.values.get("messages", [])
                            if state and state.values else []
                        )
                        last_msg = messages[-1] if messages else None
                        already_persisted = (
                            isinstance(last_msg, AIMessage)
                            and last_msg.content == final_answer
                        )
                        if not already_persisted:
                            msgs_to_add: list = []
                            human_already_in_state = any(
                                isinstance(m, HumanMessage) and m.content == message
                                for m in messages
                            )
                            if not human_already_in_state:
                                msgs_to_add.append(HumanMessage(content=message))
                            msgs_to_add.append(AIMessage(content=final_answer))
                            await graph.aupdate_state(config, {"messages": msgs_to_add})
                    except Exception:
                        logger.exception(
                            "[workspace_chat] cancellation-path persist failed"
                        )

        # Emit structured citations from web search results if the LLM
        # invoked the tool on its own. No forced fallback call — tool use
        # is opt-in per the tool's docstring.
        if web_search_output:
            citations = parse_legal_web_search_citations(web_search_output)
            if citations:
                yield {"event": "citations", "data": json.dumps(citations)}

        # Emit structured document citations from RAG results
        if rag_output:
            document_citations = self._parse_rag_citations(rag_output)
            if document_citations:
                yield {"event": "document_citations", "data": json.dumps(document_citations)}

        # Drain the title task one last time before closing the stream.
        if title_task is not None:
            try:
                title = await title_task
            except Exception:
                logger.exception("[workspace_chat] awaiting session_title task failed")
                title = None
            if title:
                yield {"event": "session_title", "data": title}

        yield {"event": "end", "data": ""}

    async def _run_verify_pipeline(
        self,
        session_id: str,
        message: str,
        tone: str,
        style: str,
        file_ids: list[str],
        user_id: str,
        model: str,
    ):
        """Draft from training memory, verify each claim via Firecrawl search-only, rewrite.

        Stages emit ``status`` SSE events; stage 4 streams the rewritten answer
        as ``answer`` events. The final rewritten answer is persisted to the
        session's checkpointer so conversation history reflects what the user
        actually saw (not the draft).
        """
        settings = get_settings()
        model_id = model or settings.chat_llm_default_model
        llm = self._get_llm(model_id)
        config = {"configurable": {"thread_id": session_id}}

        tone_suffix = TONE_INSTRUCTIONS.get(tone, TONE_INSTRUCTIONS["formal"])
        style_suffix = STYLE_INSTRUCTIONS.get(style, STYLE_INSTRUCTIONS["balanced"])

        # Session title: matches the behaviour of the normal pipeline so the
        # UI doesn't regress on first-message rename.
        base_graph = self._get_base_graph(model_id, web_search=False)
        title_task: asyncio.Task[str | None] | None = None
        try:
            prior_state = await base_graph.aget_state(config)
            is_first_message = not prior_state or not getattr(prior_state, "values", None) \
                or not prior_state.values.get("messages")
        except Exception:
            logger.exception("[verify-pipeline] aget_state failed; skipping session_title")
            is_first_message = False
        if is_first_message and message and message.strip():
            title_task = asyncio.create_task(
                generate_session_title(message, model=model_id)
            )

        # ── Fast path for trivial / greeting messages ───────────────────────
        # The verify pipeline (draft → extract → Firecrawl verify → rewrite)
        # is wasted work for "Hi"/"Thanks"/etc — observed ~54s and 4 unwanted
        # Firecrawl credits. Layer 1 is a regex/length heuristic (zero added
        # latency); layer 2 is a tiny structured-output classifier for short
        # ambiguous messages. On match we stream a brief reply without tools
        # or citations and skip the rest of the pipeline.
        trivial = is_trivial_message(message)
        classifier_used = False
        if not trivial and message and len(message.split()) <= 8:
            try:
                classification = await classify_query(message, llm)
                trivial = classification.intent == "trivial"
                classifier_used = True
            except Exception:
                logger.exception("[verify-pipeline] classifier failed; falling through")

        if trivial:
            logger.info(
                "[verify-pipeline] trivial fast-path (%s) | session=%s | msg=%r",
                "classifier" if classifier_used else "regex",
                session_id,
                message[:80],
            )
            yield {"event": "status", "data": "responding"}
            chunks: list[str] = []
            try:
                async for chunk in llm.astream([
                    SystemMessage(
                        content=TRIVIAL_REPLY_SYSTEM_PROMPT + tone_suffix + style_suffix
                    ),
                    HumanMessage(content=message or "Hi"),
                ]):
                    token = self._normalize_content(chunk.content)
                    if token:
                        chunks.append(token)
                        yield {"event": "answer", "data": token.replace("\n", "\\n")}
            except Exception:
                logger.exception("[verify-pipeline] trivial reply stream failed")

            final_answer = "".join(chunks)
            if not final_answer.strip():
                final_answer = "Hi — how can I help with your legal research today?"
                yield {"event": "answer", "data": final_answer.replace("\n", "\\n")}

            try:
                await base_graph.aupdate_state(
                    config,
                    {
                        "messages": [
                            HumanMessage(content=message),
                            AIMessage(content=final_answer),
                        ]
                    },
                )
            except Exception:
                logger.exception("[verify-pipeline] failed to persist trivial-reply history")

            if title_task is not None:
                try:
                    title = await title_task
                except Exception:
                    logger.exception("[verify-pipeline] awaiting session_title failed")
                    title = None
                if title:
                    yield {"event": "session_title", "data": title}

            yield {"event": "end", "data": ""}
            return
        # ── End fast path ───────────────────────────────────────────────────

        document_context = ""
        if file_ids and self._rag_client:
            try:
                document_context = await self._rag_client.query(
                    file_ids, message, user_id=user_id
                )
            except Exception:
                logger.exception("[verify-pipeline] RAG query failed; proceeding without it")

        # Stage 1: draft from training knowledge
        yield {"event": "status", "data": "drafting"}
        draft_user_content = message
        if document_context:
            draft_user_content = (
                f"{message}\n\n---\n"
                f"Relevant passages from the user's case files (for context):\n{document_context}"
            )
        try:
            draft_response = await llm.ainvoke([
                SystemMessage(
                    content=FACT_DENSE_DRAFT_SYSTEM_PROMPT + tone_suffix + style_suffix
                ),
                HumanMessage(content=draft_user_content),
            ])
            draft = self._normalize_content(draft_response.content)
        except Exception:
            logger.exception("[verify-pipeline] draft stage failed")
            yield {"event": "answer", "data": "An error occurred while drafting the answer."}
            yield {"event": "end", "data": ""}
            return

        if not draft or not draft.strip():
            yield {"event": "answer", "data": "I could not generate an answer for this query."}
            yield {"event": "end", "data": ""}
            return

        # Stage 2: extract claims
        yield {"event": "status", "data": "extracting claims"}
        claims: list = []
        try:
            extraction_llm = llm.with_structured_output(ClaimList)
            extracted = await extraction_llm.ainvoke([
                SystemMessage(content=CLAIM_EXTRACTION_SYSTEM_PROMPT),
                HumanMessage(content=f"DRAFT TO EXTRACT CLAIMS FROM:\n\n{draft}"),
            ])
            claims = list(extracted.claims) if extracted and getattr(extracted, "claims", None) else []
        except Exception:
            logger.exception("[verify-pipeline] claim extraction failed; emitting draft verbatim")
            claims = []

        # Stage 3: verify each claim in parallel
        verifications: list[ClaimVerification] = []
        if claims:
            yield {"event": "status", "data": f"verifying {len(claims)} claim(s)"}
            try:
                verifications = await verify_claims(claims)
            except Exception:
                logger.exception("[verify-pipeline] verification failed; emitting draft verbatim")
                verifications = []
        else:
            logger.info("[verify-pipeline] no verifiable claims found in draft")

        # Stage 4: rewrite (stream tokens) or emit draft verbatim
        yield {"event": "status", "data": "finalizing"}

        final_answer = ""
        if not verifications:
            final_answer = draft
            yield {"event": "answer", "data": final_answer.replace("\n", "\\n")}
        else:
            rewrite_input = self._format_rewrite_input(draft, verifications)
            chunks: list[str] = []
            try:
                async for chunk in llm.astream([
                    SystemMessage(
                        content=VERIFY_REWRITE_SYSTEM_PROMPT + tone_suffix + style_suffix
                    ),
                    HumanMessage(content=rewrite_input),
                ]):
                    token = self._normalize_content(chunk.content)
                    if token:
                        chunks.append(token)
                        yield {"event": "answer", "data": token.replace("\n", "\\n")}
            except Exception:
                logger.exception("[verify-pipeline] rewrite stream failed; falling back to draft")
                if not chunks:
                    yield {"event": "answer", "data": draft.replace("\n", "\\n")}
                    chunks = [draft]
            final_answer = "".join(chunks) or draft

            if not final_answer.strip():
                # Rewrite produced nothing usable — surface the draft so the user
                # isn't left with an empty response.
                final_answer = draft
                yield {"event": "answer", "data": draft.replace("\n", "\\n")}

        # Emit citations for any supporting URLs the verifier returned.
        citations_out: list[dict] = []
        seen_urls: set[str] = set()
        for v in verifications:
            if v.supported and v.supporting_url and v.supporting_url not in seen_urls:
                seen_urls.add(v.supporting_url)
                citations_out.append(
                    {
                        "url": v.supporting_url,
                        "snippet": (v.supporting_snippet or "")[:300],
                        "claim_type": v.claim.type,
                    }
                )
        if citations_out:
            yield {"event": "citations", "data": json.dumps(citations_out)}

        # Persist human message + final (rewritten) answer to checkpointer.
        try:
            await base_graph.aupdate_state(
                config,
                {
                    "messages": [
                        HumanMessage(content=message),
                        AIMessage(content=final_answer),
                    ]
                },
            )
        except Exception:
            logger.exception("[verify-pipeline] failed to persist session history")

        # Drain title task and emit if ready
        if title_task is not None:
            try:
                title = await title_task
            except Exception:
                logger.exception("[verify-pipeline] awaiting session_title failed")
                title = None
            if title:
                yield {"event": "session_title", "data": title}

        yield {"event": "end", "data": ""}

    @staticmethod
    def _format_rewrite_input(draft: str, verifications: list[ClaimVerification]) -> str:
        lines: list[str] = ["# DRAFT", "", draft, "", "# VERIFICATION REPORT", ""]
        for i, v in enumerate(verifications, 1):
            status = "SUPPORTED" if v.supported else "NOT SUPPORTED"
            lines.append(f"## Claim {i} — {status}")
            lines.append(f"- Type: {v.claim.type}")
            lines.append(f"- Claim text: {v.claim.text}")
            if v.supporting_url:
                lines.append(f"- Supporting URL: {v.supporting_url}")
            if v.supporting_snippet:
                snippet = v.supporting_snippet[:500]
                lines.append(f"- Supporting snippet: {snippet}")
            lines.append("")
        return "\n".join(lines)

    def _parse_rag_citations(self, rag_output: str) -> list[dict]:
        """Parse RAG tool output to extract citation metadata from [Indexed chunk N] format.
        
        Parses output like:
        [Indexed chunk 1] [Relevance: 0.85] [File id: abc] [Page: 5] [Concepts: contract, liability]
        This is the chunk text content...
        
        Returns structured data like:
        [{"id": 1, "file_id": "abc", "page": 5, "score": 0.85, "text_preview": "This is...", "key_terms": ["contract", "liability"]}]
        """
        if not rag_output or not rag_output.strip():
            return []
        
        citations = []
        
        # Split by chunk separators (---) to get individual chunks
        chunks = rag_output.split("\n\n---\n\n")
        
        for chunk in chunks:
            if not chunk.strip():
                continue
                
            lines = chunk.strip().split('\n')
            if not lines:
                continue
                
            header_line = lines[0]
            
            # Parse the header using regex to extract metadata
            # Pattern: [Indexed chunk N] [Relevance: X.XX] [File id: abc] [Page: N] [Concepts: term1, term2]
            chunk_match = re.search(r'\[Indexed chunk (\d+)\]', header_line)
            relevance_match = re.search(r'\[Relevance: ([\d.]+)\]', header_line)
            file_id_match = re.search(r'\[File id: ([^\]]+)\]', header_line)
            page_match = re.search(r'\[Page: (\d+)\]', header_line)
            concepts_match = re.search(r'\[Concepts: ([^\]]+)\]', header_line)
            
            if not chunk_match:
                continue  # Skip if we can't find the chunk number
                
            chunk_id = int(chunk_match.group(1))
            score = float(relevance_match.group(1)) if relevance_match else 0.0
            file_id = file_id_match.group(1) if file_id_match else ""
            page = int(page_match.group(1)) if page_match else None
            
            # Parse key terms from concepts
            key_terms = []
            if concepts_match:
                concepts_str = concepts_match.group(1)
                key_terms = [term.strip() for term in concepts_str.split(',')]
            
            # Extract text content (everything after the header line)
            text_lines = lines[1:] if len(lines) > 1 else []
            text_content = '\n'.join(text_lines).strip()
            
            # Create preview (first 200 characters)
            text_preview = text_content[:200]
            if len(text_content) > 200:
                text_preview += "..."
            
            citation = {
                "id": chunk_id,
                "file_id": file_id,
                "score": score,
                "text_preview": text_preview,
            }
            
            # Add optional fields only if they exist
            if page is not None:
                citation["page"] = page
            if key_terms:
                citation["key_terms"] = key_terms
                
            citations.append(citation)
        
        return citations

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
        try:
            async with self._pool.connection() as conn:
                async with conn.transaction():
                    await conn.execute("DELETE FROM checkpoint_writes WHERE thread_id = %s", (session_id,))
                    await conn.execute("DELETE FROM checkpoint_blobs WHERE thread_id = %s", (session_id,))
                    await conn.execute("DELETE FROM checkpoints WHERE thread_id = %s", (session_id,))
            logger.info(f"[workspace_chat] Session {session_id} checkpoints cleared")
        except Exception:
            logger.exception(f"[workspace_chat] Failed to clear checkpoints for session {session_id}")

    async def close(self):
        if self._pool:
            await self._pool.close()
