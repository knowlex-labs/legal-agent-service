"""LLM-based case summary generator with RAG retrieval."""

import logging

from langchain.chat_models import init_chat_model
from langchain_core.messages import HumanMessage, SystemMessage

from legal_agent.clients.rag_client import RAGClient
from legal_agent.config import get_settings
from legal_agent.summary.models import DraftContext

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a senior legal analyst specialising in Indian law with deep expertise
in litigation, contracts, regulatory matters, and corporate law. Your task is to produce a
comprehensive, well-structured case summary from the provided materials: case documents,
generated legal drafts, and key conversation highlights from the legal team.

────────────────────────────────────────
CRITICAL: ENTITY & NAME EXTRACTION
────────────────────────────────────────
Before writing the summary, carefully scan ALL provided text for:
• Full legal names of every person, company, firm, authority, or organisation — look in
  title pages, headers, preambles, "BETWEEN" / "Party" clauses, signature blocks, letterheads,
  recitals, addresses, and any annexures.
• For employment agreements or contracts, the company/employer name appears in opening
  recitals, party definition sections, or signature blocks. Extract and use the FULL LEGAL
  NAME — never write "Unnamed" or "Not Identified" when the name exists anywhere in the text.
• For resumes / CVs, extract company names from employment history.
• Cross-reference names across documents to resolve abbreviations and short-forms.

────────────────────────────────────────
OUTPUT FORMAT — Structured legal brief in markdown:
────────────────────────────────────────

## Case Overview
A concise 2–3 paragraph overview of the entire case, its nature (civil, criminal, contractual,
employment, regulatory, etc.), and current status. Include the principal parties by full name.

## Parties Involved
For each party, provide:
- **Full legal name** as it appears in the documents (never "Unnamed" if name is in the text)
- **Role** (Petitioner, Respondent, Employer, Employee, Complainant, etc.)
- **Brief description** where available (designation, company type, etc.)

## Key Facts
Chronological listing of material facts with specific dates where available. Each fact should
be attributable to a specific document.

## Legal Issues
Enumerate the core legal questions, framed precisely (e.g., "Whether the confidentiality
obligations under Clause X survive post-termination…").

## Relevant Statutes & Provisions
List all statutes, sections, rules, and regulations with full citations (e.g., "Section 27,
Indian Contract Act, 1872").

## Key Arguments & Positions
Summarise arguments and positions from both/all sides as evident from the documents and drafts.

## Documents & Drafts Summary
Brief description of each document and draft provided, noting its role, type, and key content.

## Observations
Notable patterns, risks, strategic considerations, or potential issues evident from the
materials. Include any inconsistencies across documents.

────────────────────────────────────────
RULES:
────────────────────────────────────────
1. Base the summary ONLY on the provided materials. Do NOT fabricate facts or citations.
2. NEVER write "Unnamed", "Not identified", or "Unnamed in provided documents" for a party
   if the name appears ANYWHERE in the provided text — search thoroughly.
3. If information for a section is genuinely unavailable, write "Not available from provided
   materials."
4. Use formal legal language appropriate for an Indian legal professional.
5. Keep the total summary between 1000–2500 words, scaling with material complexity.
6. Reference specific documents by title when citing facts or arguments.
7. Prefer precision over brevity — include exact names, dates, amounts, and section numbers."""

# Query used to retrieve broad document context for summarization
_SUMMARY_RAG_QUERY = (
    "Extract: (1) full legal names of all parties, companies, employers, organisations, and "
    "individuals from title pages, preambles, party clauses, recitals, signature blocks, "
    "employment history, and headers; (2) key facts with dates; (3) legal issues; "
    "(4) applicable statutes with section numbers; (5) arguments and positions of each side; "
    "(6) conclusions, orders, or contractual obligations."
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
        model_id = model if model else settings.chat_llm_model
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
            parts.append(
                "IMPORTANT: Pay close attention to party names, company names, and "
                "entity names that appear in the text below — especially in opening "
                "paragraphs, 'BETWEEN' clauses, signature blocks, and employment "
                "history sections. Extract and use the FULL LEGAL NAME for every party.\n"
            )
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
