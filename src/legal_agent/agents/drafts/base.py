"""Base drafting agent with common functionality."""

import asyncio
import logging
from dataclasses import dataclass
from datetime import date
from typing import Annotated, cast

from langchain.chat_models import init_chat_model
from langchain_core.messages import AnyMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition
from typing_extensions import TypedDict

from legal_agent.agents.drafts.cause_title import (
    CauseTitleData,
    extract_cause_title,
    prepend_cause_title_to_draft,
)
from legal_agent.agents.drafts.templates.loader import load_template_reference
from legal_agent.clients.rag_client import RAGClient
from legal_agent.legal_retrieval.langchain_tools import create_legal_search_tool
from legal_agent.legal_retrieval.retriever import LegalCaseRetriever
from legal_agent.models.documents import DocumentType, GeneratedDocument
from legal_agent.utils.legal_postprocess import check_citation_grounding

logger = logging.getLogger(__name__)


@dataclass
class DraftingDependencies:
    """Dependencies injected into drafting agents."""

    rag_client: RAGClient
    file_ids: list[str]
    user_id: str
    title: str
    instructions: str
    document_type: DocumentType
    examples: str = ""
    language: str = "english"
    retriever: LegalCaseRetriever | None = None
    sub_type: str | None = None
    template_reference: str | None = None
    # When set, injected verbatim and the semantic-RAG fallback is skipped:
    # top_k retrieval drops content for short uploaded source drafts.
    uploaded_doc_text: str | None = None


# Base system prompt for all legal drafting agents.
# Output goal: a standard, professional Indian-law document in the style
# of a practising courtroom advocate - clean, formal, complete, ready for
# execution.
BASE_SYSTEM_PROMPT = """=== ROLE ===
You are an expert Indian legal drafting assistant. You produce standard, professional Indian-law documents in the style of a practising courtroom advocate - contracts and filings that are clean, formal, complete, and ready for execution.
=== END ROLE ===

=== DRAFTING PRINCIPLES ===
1. Surface every detail the user provided. If the input gives CIN, PAN, GSTIN, salary breakup, leave entitlements, addresses, dates, or any other detail, each must appear naturally in the document - no silent omissions.
2. When a required detail is missing from the input, prefer the sensible standard Indian-advocate default for **commercial / contract clauses** (e.g., a twelve (12) month non-compete, thirty (30) days' notice during probation, ninety (90) days' notice after confirmation, Mumbai-seated arbitration for a Maharashtra-incorporated employer, stamp duty borne by the first party). For **identity / cause-title fields** in court filings (court name, party names, ages, addresses, mobile numbers, case numbers, FIR particulars, dates of incident), do NOT invent values - leave a clearly-named bracket like `[Applicant Mobile]`, `[Court Name]`, `[FIR Number]` so the advocate can edit it. Never emit anonymous placeholders like `_____`, `XXXX`, `[NOT PROVIDED]`, `[Amount]`, `[Name]`, `[DD]`, `[Month, Year]`. Named brackets are permitted **only** when the value is absent from both STRUCTURED INPUT and REFERENCE DOCUMENTS - they signal an advocate-editable gap, not a drafting shortcut.
3. Mask Aadhaar numbers to the last four digits: `XXXX-XXXX-1234`. This is standard Indian practice aligned with UIDAI guidelines. Never print a full Aadhaar number.
4. Use formal Indian legal phrasing: "hereinafter referred to as", "WHEREAS", "NOW, THEREFORE", "IN WITNESS WHEREOF", "which expression shall, unless repugnant to the context or meaning thereof, include the successors-in-interest and permitted assigns of the said party".
5. Use Indian numbering and currency: `Rs. 24,00,000/- (Rupees Twenty-Four Lakh Only)`. Never the international comma style (`1,250,000`).
6. Reference applicable Indian statutes by name and year, with the section where relevant: Indian Contract Act, 1872; Employees' Provident Funds Act, 1952; Employees' State Insurance Act, 1948; Payment of Gratuity Act, 1972; Maternity Benefit Act, 1961 (as amended 2017); Sexual Harassment of Women at Workplace Act, 2013 (PoSH); Digital Personal Data Protection Act, 2023; Arbitration and Conciliation Act, 1996; Registration Act, 1908; Information Technology Act, 2000. For employment / commercial **data protection** clauses use the DPDP Act, 2023 - do NOT cite IT Act §43A or the SPDI Rules; they are superseded for most personal-data processing.
7. Choose a single defined term per party and use it consistently throughout the draft. If you define the first party as **"Employer"**, do NOT later call it "Company"; if the second party is **"Employee"**, do NOT later call her "Executive". Drift between defined terms is a drafting defect.
8. When the user's input is silent on the execution date, use TODAY'S DATE (supplied in the user prompt under "Today's date:"). When silent on the commencement / start date, default to the execution date. Never emit `[Date]`, `[Month]`, `[Year]`, `[Commencement Date]`, `[Details]`, or any other bracketed fill-in marker.
9. NEVER emit em-dashes (Unicode U+2014) or en-dashes (Unicode U+2013) anywhere in the generated draft. Use a hyphen-minus (`-`, U+002D) instead. This applies to citations, addresses, category labels, prose, titles, signatures - everywhere. ASCII hyphen only.
=== END DRAFTING PRINCIPLES ===

=== TEMPLATE FIDELITY ===
When a `<template_reference>` block is supplied in the user prompt:
1. Your draft MUST include every clause, sub-clause, and schedule from the template - in the same order, with the same numbering structure (1.1, 1.2, 10.1, 10.2, …).
2. Do NOT drop clauses. Do NOT summarise or merge clauses. Do NOT reorder them.
3. If a clause heading in the template is compound (e.g., "NON-SOLICITATION AND NON-COMPETE"), the draft MUST contain both parts as separate sub-clauses. Dropping one half while keeping the compound heading is a defect.
4. Fill each clause using the user's actual parties, amounts, dates, and terms. Where the user is silent, use the template's default phrasing (it is a gold-standard example drafted by a practising Indian advocate).
5. Schedules (A, B, C, …) are mandatory - each must appear at the end of the contract with populated content. Never emit `Schedule A - [Details]`.
6. Preserve statute references exactly as they appear in the template (e.g., `§27 Indian Contract Act, 1872`, `Digital Personal Data Protection Act, 2023`, `Maternity Benefit Act, 1961 (as amended 2017)`).
=== END TEMPLATE FIDELITY ===

=== OUTPUT FORMAT: MARKDOWN ===
1. `##` headings for major section titles - e.g. `## RECITALS`, `## 1. APPOINTMENT AND POSITION`, `## SCHEDULE A - SALARY BREAKUP`.
2. Proper spacing: leave a blank line between every clause, and between a heading and its body. The draft should read like a formatted legal document, not a wall of text.
3. Use `**bold**` for:
   - Party defined terms on first use: **"Employer"**, **"Employee"**, **"this Agreement"**
   - Party full names in the execution block and recitals
   - Key defined terms in Clause 1 (Definitions): **Confidential Information**, **Intellectual Property Rights**, **Effective Date**
   - Section cross-references in body text (e.g., "subject to **Clause 12**")
4. Use `---` (horizontal rule) BETWEEN major sections only - not between individual clauses within a section.
5. Use markdown pipe tables (with header separator row) for salary breakups, payment schedules, leave entitlements, witness blocks, and any other tabular data:
   | Header 1 | Header 2 |
   |----------|----------|
   | Data     | Data     |
6. Use hierarchical numbering: `1.1`, `1.2`, `1.3` for sub-clauses; `(a)`, `(b)`, `(c)` for enumerations within a sub-clause; Roman numerals `(I)`, `(II)`, `(III)` for grounds / particulars in court filings.
7. Do NOT output raw HTML tags (`<p>`, `<div>`, `<table>`, `<br>`). Do NOT wrap output in ```code fences```.
8. Follow your specialized prompt's section structure exactly - each required section must appear as a `##` heading. When a `<template_reference>` block is provided in the user prompt, mirror its clause coverage and ordering, but use the user's actual parties, amounts, and terms.

When querying reference documents returns no results, proceed using your legal knowledge and any case law from legal_case_search. Never write "no documents found" in the draft.
=== END OUTPUT FORMAT ===

=== LEGAL CITATION FORMAT ===
ALL case citations MUST follow this EXACT format - no variation:

  **[Case Name]** - [Citation]

Examples:
  **Sushila Aggarwal v. State (NCT Delhi)** - (2020) 5 SCC 1
  **Gurbaksh Singh Sibbia v. State of Punjab** - (1980) 2 SCC 565
  **Arnesh Kumar v. State of Bihar** - (2014) 8 SCC 273
  **Sharad Birdhichand Sarda v. State of Maharashtra** - AIR 1984 SC 1622

Format rules:
1. Case name in **bold**, followed by em-dash (-), followed by citation in plain text
2. Supreme Court: (YYYY) Vol SCC Page - e.g., (2020) 5 SCC 1
3. AIR: AIR YYYY Court Page - e.g., AIR 1984 SC 1622
4. High Court: YYYY (Vol) Abbreviation Page - e.g., 2019 (3) GLH 45
5. Do NOT put the citation inside parentheses after the em-dash
6. Do NOT use markdown links - plain text only
=== END CITATION FORMAT ===

GROUNDING RULE FOR LEGAL CITATIONS (only when legal_case_search tool is provided):
When drafting grounds, prayer, or any section citing case law:
1. Call legal_case_search BEFORE writing each legal ground that needs a citation
2. Only cite cases returned by legal_case_search - never write citations from memory
3. If no relevant case is found, write the ground without a citation rather than inventing one
4. Make SEPARATE calls for different grounds (bail factors, precedent for anticipatory bail, etc.)"""


_PROVIDER_MAX_TOKENS: dict[str, int] = {
    "openai": 16384,
    "anthropic": 8192,
    "google-genai": 16384,
}


class DraftAgentState(TypedDict):
    """State for the drafting agent graph."""

    messages: Annotated[list[AnyMessage], add_messages]
    document: GeneratedDocument | None


def _extract_text_from_message(message) -> str:
    """Pull plain text out of a LangChain message, handling both str and
    list-of-content-blocks shapes (Anthropic/Gemini sometimes return lists)."""
    content = getattr(message, "content", message)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                parts.append(block.get("text", "") or "")
        return "".join(parts)
    return str(content) if content is not None else ""


# Maps a document_type enum to the template-loader category folder. Only
# sub-type-driven types need an entry; unmapped types will never load a
# template reference.
_TEMPLATE_CATEGORY: dict[DocumentType, str] = {
    DocumentType.CONTRACT: "contracts",
    DocumentType.AGREEMENT: "contracts",
}


class BaseDraftingAgent:
    """Base class for all drafting agents."""

    system_prompt: str = BASE_SYSTEM_PROMPT

    def __init__(self, model: str = "gpt-4o", provider: str = "openai"):
        """Initialize the drafting agent.

        Args:
            model: The LLM model name (e.g., 'gpt-4o', 'claude-3-opus')
            provider: The LangChain provider name (e.g., 'openai', 'anthropic', 'google-genai')
        """
        self.model_name = model
        self.provider = provider

    def _select_system_prompt(self, deps: DraftingDependencies) -> str:
        """Pick the system prompt to use for this draft.

        Default returns ``self.system_prompt``. Subclasses can override to
        route different document sub-types to different focused prompts -
        useful when one agent class handles several sub-types and feeding
        the LLM the union of all of them blows the context window or the
        TPM budget.
        """
        return self.system_prompt

    def _renders_cause_title(self, deps: DraftingDependencies) -> bool:
        """True iff this draft's cause title is rendered deterministically (not by the LLM)."""
        return False

    def _build_graph(
        self,
        tools: list,
        document_type: DocumentType,
        system_prompt: str | None = None,
        deps: DraftingDependencies | None = None,
    ):
        """Build a LangGraph workflow for drafting."""
        max_tokens = _PROVIDER_MAX_TOKENS.get(self.provider, 8192)
        llm = init_chat_model(self.model_name, model_provider=self.provider, max_tokens=max_tokens)
        llm_with_tools = llm.bind_tools(tools) if tools else llm
        from legal_agent.config import get_settings
        _settings = get_settings()
        _meta_provider = _settings.metadata_extraction_provider
        _meta_max_tokens = _PROVIDER_MAX_TOKENS.get(_meta_provider, 8192)
        llm_structured = init_chat_model(
            _settings.metadata_extraction_model,
            model_provider=_meta_provider,
            max_tokens=_meta_max_tokens,
        ).with_structured_output(GeneratedDocument)
        effective_system_prompt = system_prompt if system_prompt is not None else self.system_prompt
        system_msg = SystemMessage(content=effective_system_prompt)

        async def agent_node(state: DraftAgentState):
            response = await llm_with_tools.ainvoke([system_msg] + state["messages"])
            return {"messages": [response]}

        async def output_node(state: DraftAgentState):
            # Capture the raw markdown directly from the agent's last message -
            # preserves all formatting (headings, bold, tables, ---) that a
            # second structured-output LLM call tends to strip.
            raw_draft = _extract_text_from_message(state["messages"][-1])

            extraction_prompt = (
                "From the legal document you just drafted, extract ONLY metadata:\n"
                "- title: the document's title\n"
                "- summary: a 1-2 sentence summary\n"
                "- sections: split the document into its NATURAL sections (by "
                "the ## headings that appear in the document). For each, set "
                "'title' to the heading text and 'content' to the markdown "
                "under it.\n\n"
                "For the 'draft' field: return an empty string - it will be "
                "overridden with the original unmodified markdown.\n\n"
                "Section guidance for court filings: use natural headings like "
                "'Cause Title', 'Facts', 'Grounds', 'Prayer', 'Verification' - "
                "whatever appears in the actual document. Do NOT invent generic names."
            )
            msgs = [system_msg] + state["messages"] + [HumanMessage(content=extraction_prompt)]

            should_render_cause_title = bool(
                deps is not None and self._renders_cause_title(deps)
            )

            async def _extract_cause_title_safe() -> CauseTitleData | None:
                if not should_render_cause_title or deps is None:
                    return None
                try:
                    return await extract_cause_title(
                        reference_text=deps.uploaded_doc_text,
                        instructions=deps.instructions,
                        document_title=deps.title,
                        today=date.today().isoformat(),
                        provider=self.provider,
                    )
                except Exception as exc:
                    logger.warning(
                        f"[draft] Cause-title extraction failed "
                        f"({type(exc).__name__}: {exc}); "
                        "shipping body without rendered cause title"
                    )
                    return None

            metadata_result, cause_title_data = await asyncio.gather(
                llm_structured.ainvoke(msgs),
                _extract_cause_title_safe(),
                return_exceptions=True,
            )

            if isinstance(metadata_result, BaseException):
                logger.warning(
                    f"[draft] Structured metadata extraction failed "
                    f"({type(metadata_result).__name__}: {metadata_result}); "
                    "falling back to raw draft with minimal metadata"
                )
                raw_doc = GeneratedDocument(
                    document_type=document_type,
                    title="",
                    summary="",
                    sections=[],
                    draft=raw_draft or "",
                )
            else:
                raw_doc = cast(GeneratedDocument, metadata_result)

            if isinstance(cause_title_data, BaseException):
                cause_title_data = None

            # Override the draft field with the raw, unmodified markdown.
            # Fallback to structured output's draft if the raw message is unusable.
            final_draft = raw_draft if raw_draft and len(raw_draft.strip()) >= 200 else raw_doc.draft

            if (
                should_render_cause_title
                and isinstance(cause_title_data, CauseTitleData)
                and final_draft
                and len(final_draft.strip()) >= 200
            ):
                final_draft = prepend_cause_title_to_draft(final_draft, cause_title_data)

            # Citation-grounding check (warn-only). Tells us when the LLM
            # cited case law without ever calling legal_case_search, or when
            # citations in the draft don't appear in the tool's returned
            # results. Observability before enforcement.
            try:
                tool_results_parts: list[str] = []
                tool_was_called = False
                for msg in state["messages"]:
                    if isinstance(msg, ToolMessage) and getattr(msg, "name", "") == "legal_case_search":
                        tool_was_called = True
                        content = _extract_text_from_message(msg)
                        if content:
                            tool_results_parts.append(content)
                tool_results_joined = "\n".join(tool_results_parts)
                # Only run the check if tools were actually wired - if the retriever
                # wasn't available, it's pointless to warn about unverified citations.
                tools_available = bool(tools)
                if tools_available:
                    check_citation_grounding(
                        draft_markdown=final_draft or "",
                        tool_results_joined=tool_results_joined,
                        tool_was_called=tool_was_called,
                        document_type=document_type.value,
                    )
            except Exception as exc:
                logger.debug(f"[citation-check] internal error (non-blocking): {exc}")

            document = raw_doc.model_copy(update={
                "document_type": document_type,
                "draft": final_draft,
            })
            return {"document": document}

        workflow = StateGraph(DraftAgentState)
        workflow.add_node("agent", agent_node)
        if tools:
            workflow.add_node("tools", ToolNode(tools))
        else:
            workflow.add_node("tools", lambda state: state)
        workflow.add_node("output", output_node)
        workflow.set_entry_point("agent")
        workflow.add_conditional_edges(
            "agent", tools_condition, {"tools": "tools", "__end__": "output"}
        )
        workflow.add_edge("tools", "agent")
        workflow.add_edge("output", END)
        return workflow.compile()

    async def draft(self, deps: DraftingDependencies) -> GeneratedDocument:
        """Generate a legal document draft."""
        logger.info(
            f"[draft] Starting: title='{deps.title}' | agent={self.__class__.__name__} "
            f"| files={len(deps.file_ids)}"
        )

        # Load per-sub-type template reference. Prefer a caller-supplied
        # deps.template_reference; otherwise resolve via sub_type + category.
        template_reference = deps.template_reference
        if template_reference is None and deps.sub_type:
            category = _TEMPLATE_CATEGORY.get(deps.document_type)
            if category:
                template_reference = load_template_reference(category, deps.sub_type)
                if template_reference:
                    logger.info(
                        "[draft] Loaded template_reference: category=%s sub_type=%s length=%d chars",
                        category, deps.sub_type, len(template_reference),
                    )
                else:
                    logger.info(
                        "[draft] No template_reference loaded: category=%s sub_type=%s (file missing)",
                        category, deps.sub_type,
                    )
            else:
                logger.info(
                    "[draft] No template_reference category mapped for document_type=%s (sub_type=%s)",
                    deps.document_type.value, deps.sub_type,
                )
        elif template_reference is not None:
            logger.info(
                "[draft] Using caller-supplied deps.template_reference: length=%d chars",
                len(template_reference),
            )

        template_section = ""
        if template_reference:
            template_section = f"""
=== TEMPLATE REFERENCE (for structure only) ===
The following is a gold-standard example of a {deps.sub_type or deps.document_type.value} document.

USE this reference for:
- Section structure and ordering
- Clause coverage (what a complete draft of this sub-type includes)
- Tone and formality level

DO NOT copy from this reference:
- Party names, addresses, or identifiers
- Specific amounts, dates, or durations
- Any content specific to the example parties

Generate fresh content for this draft using ONLY the user's structured input.

{template_reference}
=== END TEMPLATE REFERENCE ===
"""

        examples_section = ""
        if deps.examples:
            examples_section = f"""
=== REFERENCE EXAMPLES (for format only) ===
The following shows drafting style, layout conventions, and format guidelines.

USE for:
- Layout and formatting conventions
- Indian legal phrasing patterns
- Clause style and tone

Any `[Bracketed Field]` in these examples is a STRUCTURAL SLOT, not literal output.
Fill each slot with real values from STRUCTURED INPUT and REFERENCE DOCUMENTS CONTEXT.
DO NOT copy specific content (names, amounts, dates, facts) from these examples.
Generate fresh content using ONLY the user's structured input and uploaded documents.

{deps.examples}
=== END REFERENCE EXAMPLES ===
"""

        language_section = ""
        if deps.language == "hindi":
            language_section = """
=== LANGUAGE INSTRUCTIONS ===
Draft this document ENTIRELY in Hindi (Devanagari script).
Use formal legal Hindi terminology throughout:
- आवेदक (Applicant), अनावेदक (Non-Applicant/Respondent)
- विरुद्ध (Versus), अपीलार्थी (Appellant), प्रत्यर्थी (Respondent)
- माननीय न्यायालय (Hon'ble Court), न्यायाधीश (Judge)
- आदेश (Order), निर्णय (Judgment), याचिका (Petition)
- प्रार्थना (Prayer), आधार (Grounds), तथ्य (Facts)
- धारा (Section), अधिनियम (Act), संहिता (Code)
- जमानत (Bail), अग्रिम जमानत (Anticipatory Bail)
- प्रथम सूचना रिपोर्ट (FIR), अपराध क्रमांक (Crime Number)
- दण्ड प्रक्रिया संहिता (CrPC), भारतीय न्याय सुरक्षा संहिता (BNSS)
- भारतीय नागरिक सुरक्षा संहिता (BNS)
Use Hindi numerals where appropriate but case numbers and section numbers may remain in English.
=== END LANGUAGE INSTRUCTIONS ===
"""
        elif deps.language == "bilingual":
            language_section = """
=== LANGUAGE INSTRUCTIONS ===
Draft this document in BILINGUAL format:
- Section headers, court name, case numbers: in ENGLISH
- Body text, facts, grounds, prayer: in HINDI (Devanagari script)
- Legal section references: English with Hindi translation in parentheses
- Party names: as provided (may be in either script)
Use formal legal Hindi terminology for the Hindi portions (see Hindi terms above).
=== END LANGUAGE INSTRUCTIONS ===
"""

        rag_section = ""
        if deps.uploaded_doc_text:
            rag_section = f"""
=== REFERENCE DOCUMENTS CONTEXT ===
The following is the FULL TEXT of the source document(s) the user uploaded.
Treat this as a PRIMARY DATA SOURCE - extract from it, and substitute into your
draft, every value relevant to the cause title and body, including (but not
limited to):
  - Court name and seat (e.g., "Hon'ble Small Causes Court, Pune at Pune")
  - Case caption / case number / year (if any)
  - Each party's full name, age, occupation, full residential or office
    address, and mobile number - preserving the role tag (Plaintiff /
    Defendant / Applicant / Respondent / Petitioner)
  - Property description, FIR / complaint particulars, statutory sections invoked
  - All dates, amounts (in figures and words), and named third parties

Precedence of sources:
  - When STRUCTURED INPUT and this REFERENCE DOCUMENT disagree, prefer
    STRUCTURED INPUT (it is what the advocate explicitly typed).
  - When STRUCTURED INPUT is silent on a field, take it from here.
  - Only when BOTH are silent, leave a clearly-named bracket like
    `[Applicant Mobile]` or `[Court Name]` for the advocate to fill.
    Do NOT fabricate.

{deps.uploaded_doc_text}
=== END REFERENCE DOCUMENTS CONTEXT ===
"""
        elif deps.file_ids:
            rag_query = f"{deps.sub_type or deps.document_type.value} {deps.title}: facts parties amounts"
            rag_context = await deps.rag_client.query(
                file_ids=deps.file_ids,
                query=rag_query,
                user_id=deps.user_id,
            )
            if rag_context:
                rag_section = f"""
=== REFERENCE DOCUMENTS CONTEXT ===
{rag_context}
=== END REFERENCE DOCUMENTS CONTEXT ===
"""
            else:
                logger.warning(f"[draft] RAG returned empty context for file_ids={deps.file_ids}")

        # Case-law retrieval (legal_case_search) is expensive (CPU embedding +
        # reranker per call). Only attach it when the user has uploaded files
        # - a strong signal that deep research is desired. Otherwise, draft
        # without citations to keep latency bounded.
        case_law_enabled = bool(deps.retriever and deps.file_ids)
        if case_law_enabled:
            search_instruction = (
                "legal_case_search is available. Make AT MOST one or two consolidated calls "
                "covering the main legal issues - do NOT call the tool per ground. "
                "Only cite cases returned by the tool. "
                "Format every citation as: **Case Name** - Citation (see CITATION FORMAT above)."
            )
        else:
            search_instruction = (
                "legal_case_search is NOT available in this session. Do NOT attempt to call it. "
                "Write all legal grounds without case citations. You may reference landmark case names "
                "only where essential (e.g., Sharad Birdhichand Sarda, Gurbaksh Singh Sibbia) "
                "but do not write any citation numbers."
            )

        today = date.today()
        today_long = today.strftime("%d %B, %Y")
        today_ddmmyyyy = today.strftime("%d/%m/%Y")

        prompt = f"""Draft the following document using ONLY the information provided.

Document Title: {deps.title}

Today's date: {today_long} ({today_ddmmyyyy}). Use this as the execution date when the user is silent; default the commencement / start date to the execution date when the user is silent on that too.

=== DRAFTING STRATEGY ===
Before writing, do the following mentally:
1. Identify the specific document sub-type (e.g., civil suit for possession, writ petition, anticipatory bail, notice for cheque bounce, NDA, etc.)
2. Select the MATCHING section structure from your specialized template for that sub-type
3. Extract all party names, ages, addresses, amounts, dates, FIR details from the input
4. Plan your legal_case_search queries - one per ground/section requiring case law
Then draft the document following your template sections EXACTLY.
=== END DRAFTING STRATEGY ===

=== STRUCTURED INPUT DATA ===
The following contains all party details, facts, and requirements extracted from the user's input.
Use these EXACT details - names, ages, addresses, amounts, dates - in your draft.

{deps.instructions}

=== END STRUCTURED INPUT ===
{template_section}{language_section}{rag_section}{examples_section}

{search_instruction}

=== FORMATTING REMINDER ===
- Output CLEAN MARKDOWN following your template exactly.
- Section headers as ## headings (e.g., ## 1. APPOINTMENT AND POSITION, ## RECITALS, ## SCHEDULE A - SALARY BREAKUP). Do NOT collapse the entire document into flat numbered paragraphs.
- Leave a blank line between clauses and between headings and their body.
- Within each section, use hierarchical sub-numbering: 1.1, 1.2, 1.3 for sub-clauses; (a), (b), (c) for enumerations; Roman numerals (I), (II), (III) for grounds in court filings.
- Tables: markdown pipe syntax with header separator row |---|.
- Bold: **text** for party defined terms, full names in the execution block, key defined terms, section cross-references.
- Amounts: ALWAYS in figures AND words - Rs. 4,25,000/- (Rupees Four Lakh Twenty-Five Thousand Only). Indian numbering (lakh, crore).
- Dates: DD/MM/YYYY in clause headings; natural-language date permissible in the execution recital.
- Do NOT emit unfilled placeholders like [Name], [Date], _____, XXXX in the final output. If a detail is missing, apply the sensible Indian-advocate default (see DRAFTING PRINCIPLES).
- Aadhaar numbers rendered as XXXX-XXXX-<last 4>. Never emit a full Aadhaar.
- Do NOT output HTML tags. Do NOT use code fences.
- Follow your specialized template structure EXACTLY - each required section must be present.
=== END FORMATTING ===

Generate a COMPLETE, FINISHED Indian contract following the exact markdown template from your specialized prompt. Every major section appears as a ## heading. Every input field from the structured data appears naturally in the draft. Missing details filled with sensible Indian-advocate defaults. The output must read like a document a practising courtroom advocate would hand to a client for execution."""

        selected_system_prompt = self._select_system_prompt(deps)

        tools = []
        if deps.retriever and deps.file_ids:
            tools.append(create_legal_search_tool(deps.retriever))

        graph = self._build_graph(
            tools,
            deps.document_type,
            system_prompt=selected_system_prompt,
            deps=deps,
        )
        try:
            # recursion_limit caps the agent tool-loop - prevents the LLM
            # from infinitely emitting tool calls until the job-manager
            # timeout kills the whole job. 25 is the LangGraph default but
            # making it explicit documents the bound and lets us lower it.
            result = await graph.ainvoke(
                {"messages": [HumanMessage(content=prompt)], "document": None},
                config={"recursion_limit": 25},
            )
        except Exception as exc:
            logger.exception(
                f"[draft] Graph execution failed: document_type={deps.document_type.value} "
                f"user={deps.user_id} model={self.model_name} title='{deps.title}'"
            )
            raise RuntimeError(
                f"Drafting agent failed for {deps.document_type.value}: {type(exc).__name__}: {exc}"
            ) from exc
        if not result.get("document"):
            raise RuntimeError(
                f"Drafting agent completed without producing a document "
                f"(document_type={deps.document_type.value}). "
                "Likely indicates the agent hit the recursion_limit without calling output_node."
            )
        return result["document"]
