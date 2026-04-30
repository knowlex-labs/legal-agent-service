"""LLM-based case synopsis generator with RAG retrieval."""

import logging

from langchain.chat_models import init_chat_model
from langchain_core.messages import HumanMessage, SystemMessage

from legal_agent.clients.rag_client import RAGClient
from legal_agent.config import get_settings

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a senior legal analyst specialising in Indian law with deep expertise in
analysing contracts, litigation documents, regulatory filings, and corporate records. Your task
is to produce a precise, authoritative Case Synopsis from the uploaded source documents.

A Synopsis is a focused document analysis — it must be crisp, factual, and ready to be placed
before a court or included in a case file.

────────────────────────────────────────
CRITICAL: ENTITY & NAME EXTRACTION
────────────────────────────────────────
Before writing the synopsis, carefully scan ALL provided document text for:
• Full legal names of every person, company, firm, authority, or organisation — look in
  title pages, headers, preambles, "BETWEEN" / "Party" clauses, signature blocks, letterheads,
  recitals, addresses, notarisation stamps, and any annexures.
• If a document is an employment agreement, contract, or MoU, the company/employer name
  almost always appears in the opening recital ("This Agreement is entered into between
  [Company Name] … and [Employee Name]…"), the "Party" definitions section, or the
  signature block at the end. Extract and use the FULL LEGAL NAME — never write "Unnamed"
  or "Not Identified" when the name exists anywhere in the text.
• For resumes / CVs, extract company names from the employment history section.
• Cross-reference names across documents to resolve abbreviations and short-forms.

────────────────────────────────────────
OUTPUT FORMAT — Structured legal synopsis in markdown:
────────────────────────────────────────

## Document Identification
For each source document analysed, state:
- **Document title** (or descriptive label if untitled)
- **Document type** (Agreement, Petition, Judgment, FIR, Contract, CV, Affidavit, etc.)
- **Date** of execution / filing / issuance (if available)
- **Reference / File ID** if visible in the document

## Parties
List ALL parties with:
- **Full legal name** as it appears in the documents (individual name, company name,
  firm name, authority name — never leave unnamed if the text contains the name)
- **Role / Designation** (Petitioner, Respondent, Employer, Employee, Complainant, Accused,
  Plaintiff, Defendant, Appellant, Licensor, Licensee, Landlord, Tenant, etc.)
- **Brief description** (e.g., "Software Development Engineer" or "Private Limited Company
  incorporated under the Companies Act, 2013")

## Cause of Action / Subject Matter
A single, well-drafted paragraph describing the core legal grievance, contractual subject,
or matter in dispute. Identify the nature of the dispute (civil, criminal, contractual,
regulatory, employment, IP, etc.).

## Chronological Facts
Bullet-point listing of key material facts in strict chronological order, with specific
dates where available. Each fact should be attributable to a specific document.

## Legal Issues
Enumerate the specific legal questions raised or to be adjudicated. Frame each issue as a
precise legal question (e.g., "Whether the non-compete clause under Section X is
enforceable…").

## Applicable Statutes & Provisions
List every statute, section, rule, or regulation cited or directly applicable, with full
citation (e.g., "Section 27, Indian Contract Act, 1872").

## Relief Sought / Prayers
State the relief, remedy, or orders sought by each party. If the document is a contract,
state the key obligations and consideration.

## Procedural History
Brief account of the procedural stages the matter has gone through (FIR, charge sheet,
trial, appeal, revision, writ, arbitration, etc.) as evident from the documents.

## Current Status
The present stage of proceedings or contractual status as apparent from the most recent
document. If the documents are contracts/agreements, state whether they appear to be
in force, expired, or terminated.

────────────────────────────────────────
RULES:
────────────────────────────────────────
1. Base the synopsis ONLY on the uploaded documents. Do NOT fabricate facts, statutes,
   citations, or party names.
2. NEVER write "Unnamed", "Not identified", or "Unnamed in provided documents" for a party
   if the name appears ANYWHERE in the provided text — search thoroughly.
3. If a section genuinely has no information in the documents, write "Not available from the
   provided documents."
4. Use formal legal language appropriate for Indian courts and legal professionals.
5. Keep the total synopsis between 800–2000 words, scaling with document complexity.
6. Reference specific documents by title/type when citing facts.
7. Prefer precision over brevity — include exact names, dates, amounts, and section numbers."""

_SYNOPSIS_RAG_QUERY = (
    "Extract: (1) full legal names of all parties, companies, employers, organisations, and "
    "individuals from title pages, preambles, party clauses, recitals, signature blocks, "
    "employment history, and headers; (2) cause of action or subject matter; (3) chronological "
    "facts with dates; (4) legal issues; (5) applicable statutes with section numbers; "
    "(6) relief sought or contractual obligations; (7) procedural history; (8) current status."
)


class SynopsisGenerator:
    def __init__(self, rag_client: RAGClient):
        self._rag_client = rag_client

    async def generate(
        self,
        file_ids: list[str],
        user_id: str,
        model: str = "openai",
    ) -> str:
        """Fetch document context from RAG, call LLM to produce a synopsis."""
        settings = get_settings()
        model_id = model if model else settings.chat_llm_default_model
        langchain_provider = settings.get_langchain_provider_for_model(model_id)

        document_context = ""
        if file_ids:
            logger.info(
                f"[synopsis] Fetching RAG context | user={user_id} | files={len(file_ids)}"
            )
            document_context = await self._rag_client.query(
                file_ids=file_ids,
                query=_SYNOPSIS_RAG_QUERY,
                user_id=user_id,
            )

        context = self._assemble_context(document_context)

        llm = init_chat_model(model_id, model_provider=langchain_provider)

        logger.info(
            f"[synopsis] Generating synopsis | model={model_id} | files={len(file_ids)}"
        )

        response = await llm.ainvoke([
            SystemMessage(content=SYSTEM_PROMPT),
            HumanMessage(content=context),
        ])

        content = response.content
        if isinstance(content, list):
            return "".join(
                part if isinstance(part, str) else part.get("text", "")
                for part in content
            )
        return content

    def _assemble_context(self, document_context: str) -> str:
        """Build the user message from RAG document context."""
        if not document_context:
            return (
                "No case documents were provided. Please upload source documents "
                "before generating a synopsis."
            )
        parts = [
            "# UPLOADED CASE DOCUMENTS (retrieved from RAG)\n",
            "IMPORTANT: Pay close attention to party names, company names, and "
            "entity names that appear in the text below — especially in opening "
            "paragraphs, 'BETWEEN' clauses, signature blocks, and employment "
            "history sections. Extract and use the FULL LEGAL NAME for every party.\n",
            document_context,
        ]
        return "\n".join(parts)