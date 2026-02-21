"""Case agent with ask/edit modes and SSE streaming."""

import asyncio
import json
import logging

from langchain.chat_models import init_chat_model
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.prebuilt import create_react_agent

from legal_agent.case_agent.models import CaseAgentRequest
from legal_agent.case_agent.tools import create_source_query_tool
from legal_agent.clients.rag_client import RAGClient
from legal_agent.config import get_settings
from legal_agent.legal_retrieval import LegalCaseRetriever, create_legal_search_tool

logger = logging.getLogger(__name__)

ASK_SYSTEM_PROMPT = """You are a senior legal research assistant specializing in Indian law.
You help lawyers analyze cases by answering questions using the case's source documents and legal knowledge base.

TONE:
- Write in the third person, present tense of legal exposition.
- Be declarative and definitive. State what the law is.
- Do not adopt a conversational register. Do not greet the user or use first person.

RULES:
1. Use the available tools to find relevant information before answering.
2. Ground every factual claim in tool results. Cite the source when possible.
3. If tools return no results, state that clearly rather than speculating.
4. Do not answer questions unrelated to law or the case at hand.
5. Format responses in markdown for readability."""

EDIT_SYSTEM_PROMPT = """You are an expert legal drafting assistant specializing in Indian law.
You help lawyers create and edit legal documents by modifying the provided draft content.

RULES:
1. You will receive the current draft content along with the user's editing instruction.
2. Use the query_source_documents tool to find relevant information from the case files when needed.
3. Apply the requested changes to the draft while preserving the overall structure and formatting.
4. Return the COMPLETE updated draft as HTML — not just the changed sections.
5. Do not use placeholder text like [Name], [Date], _____, XXXX. Use actual information from context.
6. Maintain proper Indian legal formatting conventions.
7. Your entire response should be the full updated draft HTML. Do not include explanatory text before or after the draft."""


class CaseAgent:
    def __init__(self, retriever: LegalCaseRetriever | None = None, rag_client: RAGClient | None = None):
        self.retriever = retriever
        self.rag_client = rag_client
        self._models: dict[str, object] = {}  # provider_key -> ChatModel
        self._ready = asyncio.Event()

    async def initialize(self):
        """Pre-initialize LLM instances so first request is fast."""
        try:
            settings = get_settings()
            chat_models = settings.get_chat_models()
            for provider_key, (model_name, langchain_provider) in chat_models.items():
                self._models[provider_key] = init_chat_model(model_name, model_provider=langchain_provider)
                logger.info(f"CaseAgent: initialized model {provider_key} ({model_name})")
            self._ready.set()
            logger.info("CaseAgent fully initialized")
        except Exception:
            logger.exception("Failed to initialize CaseAgent")
            self._ready.set()

    def _get_model(self, provider: str):
        if provider not in self._models:
            settings = get_settings()
            logger.warning(f"Model '{provider}' not found, falling back to {settings.chat_llm_default_provider}")
            provider = settings.chat_llm_default_provider
        return self._models[provider]

    async def stream_response(self, case_id: str, request: CaseAgentRequest):
        """Yield SSE event dicts for the case agent response.

        Events:
        - {"event": "tool_call",   "data": '{"name": ..., "args": {...}}'}
        - {"event": "tool_result", "data": "<output>"}
        - {"event": "answer",      "data": "<token>"}
        - {"event": "error",       "data": "<message>"}
        - {"event": "end",         "data": ""}
        """
        await self._ready.wait()

        logger.info(f"[case_agent] case={case_id} | mode={request.mode} | model={request.model} | msg='{request.message[:100]}'")

        model = self._get_model(request.model)

        # Build tools based on mode
        tools = []
        if request.mode == "ask" and self.retriever:
            tools.append(create_legal_search_tool(self.retriever))
        if self.rag_client and request.source_ids:
            tools.append(create_source_query_tool(self.rag_client, request.source_ids))

        # Select system prompt
        system_prompt = ASK_SYSTEM_PROMPT if request.mode == "ask" else EDIT_SYSTEM_PROMPT

        # Build graph per-request (no checkpointer — stateless)
        graph = create_react_agent(model=model, tools=tools, prompt=system_prompt)

        # Build message history
        messages = []
        for turn in request.conversation_history:
            if turn.role == "user":
                messages.append(HumanMessage(content=turn.content))
            else:
                messages.append(AIMessage(content=turn.content))

        # Build the current user message
        if request.mode == "edit":
            user_content = (
                f"## Current Draft\n\n{request.active_draft_content}\n\n"
                f"## Editing Instruction\n\n{request.message}"
            )
        else:
            user_content = request.message

        messages.append(HumanMessage(content=user_content))

        # Stream events
        async for event in graph.astream_events({"messages": messages}, version="v2"):
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
