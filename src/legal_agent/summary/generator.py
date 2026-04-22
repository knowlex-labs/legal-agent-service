"""LLM-based case summary generator with RAG retrieval."""

import logging

from langchain.chat_models import init_chat_model
from langchain_core.messages import HumanMessage, SystemMessage

from legal_agent.clients.rag_client import RAGClient
from legal_agent.config import get_settings
from legal_agent.summary.models import DraftContext

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a senior legal analyst specialising in Indian law. Your task is to
produce a comprehensive case summary from the provided materials: case documents, generated
legal drafts, and key conversation highlights from the legal team.

OUTPUT FORMAT — Structured legal brief in markdown:

## Case Overview
A concise 2–3 paragraph overview of the entire case, its nature, and current status.

## Parties Involved
Identify all parties (petitioners, respondents, complainants, accused, contracting parties, etc.)
from the documents and drafts.

## Key Facts
Chronological listing of material facts extracted from the documents.

## Legal Issues
Enumerate the core legal questions and issues arising from the case.

## Relevant Statutes & Provisions
List all statutes, sections, rules, and regulations referenced or applicable.

## Key Arguments & Positions
Summarise arguments from both/all sides as evident from the documents and drafts.

## Documents & Drafts Summary
Brief description of each document and draft provided, noting its role in the case.

## Observations
Any notable patterns, risks, or strategic considerations evident from the materials.

RULES:
1. Base the summary ONLY on the provided materials. Do NOT fabricate facts or citations.
2. If information for a section is unavailable, write "Not available from provided materials."
3. Use formal legal language appropriate for an Indian legal professional.
4. Keep the total summary under 2000 words unless the materials warrant more detail.
5. Reference specific documents by title when citing facts or arguments."""

# Query used to retrieve broad document context for summarization
_SUMMARY_RAG_QUERY = (
    "Summarize the key facts, parties, legal issues, statutes, arguments, "
    "and conclusions from all documents in this case."
)


class SummaryGenerator:
    def __init__(self, rag_client: RAGClient):
        self._rag_client = rag_client
    
    async def generate(
        self,
        file_ids: list[str],
        user_id: str,
        drafts: list[DraftContext],
        chat_highlights: list[str],
        model: str = "openai",
    ) -> str:
        logger.info(f"[DEBUG] Generator received file_ids: {file_ids}")
        """Fetch document context from RAG, assemble with drafts/highlights, call LLM."""
        settings = get_settings()
        model_id = model if model else settings.chat_llm_default_model
        langchain_provider = settings.get_langchain_provider_for_model(model_id)

        # Step 1: Retrieve document context from RAG engine
        document_context = ""
        if file_ids:
            logger.info(
                f"[DEBUG] Calling RAG | user={user_id} | file_ids={file_ids}"
            )
            document_context = await self._rag_client.query(
                file_ids=file_ids,
                query=_SUMMARY_RAG_QUERY,
                user_id=user_id,
            )
            logger.info(f"[DEBUG] RAG context length: {len(document_context)}")
            logger.info(f"[DEBUG] RAG context preview: {document_context[:500]}")

        # Step 2: Assemble full context
        context = self._assemble_context(document_context, drafts, chat_highlights)

        # Step 3: Call LLM
        llm = init_chat_model(model_id, model_provider=langchain_provider)

        logger.info(
            f"[summary] Generating summary | model={model_id} | "
            f"files={len(file_ids)} drafts={len(drafts)} highlights={len(chat_highlights)}"
        )

        response = await llm.ainvoke([
            SystemMessage(content=SYSTEM_PROMPT),
            HumanMessage(content=context),
        ])

        content = response.content
        if isinstance(content, list):
            return "".join(part if isinstance(part, str) else part.get("text", "") for part in content)
        return content

    def _assemble_context(
        self,
        document_context: str,
        drafts: list[DraftContext],
        chat_highlights: list[str],
    ) -> str:
        """Build the user message with all case context."""
        parts: list[str] = []

        if document_context:
            parts.append("# CASE DOCUMENTS (retrieved from RAG)\n")
            parts.append(document_context)
            parts.append("")

        if drafts:
            parts.append("# GENERATED DRAFTS\n")
            for i, draft in enumerate(drafts, 1):
                parts.append(f"## Draft {i}: {draft.title} (Type: {draft.document_type})")
                if draft.content:
                    parts.append(f"\n{draft.content}\n")
                else:
                    parts.append("\n[Draft content not provided]\n")

        if chat_highlights:
            parts.append("# KEY CONVERSATION HIGHLIGHTS\n")
            for i, highlight in enumerate(chat_highlights, 1):
                parts.append(f"{i}. {highlight}")

        if not parts:
            return (
                "No case materials were provided. Please provide file IDs, drafts, "
                "or conversation highlights to summarise."
            )

        return "\n".join(parts)
