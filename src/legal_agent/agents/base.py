"""Base drafting agent with common functionality."""

import logging
from dataclasses import dataclass
from typing import Annotated

from langchain.chat_models import init_chat_model
from langchain_core.messages import AnyMessage, HumanMessage, SystemMessage
from langchain_core.tools import tool
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition
from typing_extensions import TypedDict

from legal_agent.clients.rag_client import RAGClient
from legal_agent.legal_retrieval.langchain_tools import create_legal_search_tool
from legal_agent.legal_retrieval.retriever import LegalCaseRetriever
from legal_agent.models.documents import GeneratedDocument

logger = logging.getLogger(__name__)


@dataclass
class DraftingDependencies:
    """Dependencies injected into drafting agents."""

    rag_client: RAGClient
    file_ids: list[str]
    user_id: str
    title: str
    instructions: str
    examples: str = ""
    language: str = "english"
    retriever: LegalCaseRetriever | None = None


# Base system prompt for all legal drafting agents
BASE_SYSTEM_PROMPT = """You are an expert legal drafting assistant specializing in Indian law.
Your task is to draft professional, legally sound documents based on the provided instructions.

CRITICAL RULES - VIOLATION IS NOT ACCEPTABLE:
1. NEVER use placeholder text like [Name], [Address], [Amount], [Date], _____, XXXX, etc.
2. NEVER leave blanks to be filled in later
3. Use ONLY the actual information provided in the instructions
4. If specific information is missing (like exact date), use contextual alternatives:
   - Missing date → "on or about [month] [year]" or describe the event contextually
   - Missing exact amount → use the amount mentioned or say "the agreed amount"
   - Missing address → use whatever location info is provided
5. The document must be COMPLETE and READY TO USE as-is
6. Every field must have real content - no templates, no blanks

Key guidelines:
1. Follow Indian legal conventions and formatting standards
2. Use formal, precise legal language appropriate for Indian courts and tribunals
3. Include all necessary clauses and provisions required by Indian law
4. Reference relevant Indian statutes, acts, and regulations where appropriate
5. Ensure the document is complete, well-structured, and ready for review
6. Use proper Indian legal terminology and citation formats

When provided with reference documents via the RAG context, use that information to ensure
accuracy and consistency with existing documents or precedents.

=== OUTPUT FORMAT: MARKDOWN ===
You MUST output the document as clean, well-structured MARKDOWN.

CRITICAL - SINGLE PAGE OUTPUT:
1. The entire document must be OUTPUT AS A SINGLE CONTINUOUS BLOCK - do NOT separate sections with blank lines or extra newlines
2. Use markdown pipe tables for ALL tabular data (always include header separator row):
   | Header 1 | Header 2 |
   |----------|----------|
   | Data     | Data     |

3. Use **bold** for names, headings, and emphasis
4. Use ## headings for major section titles (e.g., ## PRAYER, ## GROUNDS)
5. Use --- (horizontal rule) ONLY between MAJOR sections (not between every paragraph)
6. Do NOT output raw HTML tags (no <p>, <div>, <table>, <br>)
7. Do NOT wrap output in ```code fences```
8. Do NOT use page breaks, section dividers, or any markers that would cause the document to split
9. Follow the EXACT template structure provided by your specialized prompt
=== END OUTPUT FORMAT ===

GROUNDING RULE FOR LEGAL CITATIONS:
When drafting grounds, prayer, or any section citing case law:
1. Call legal_case_search BEFORE writing each legal ground that needs a citation
2. Only cite cases returned by legal_case_search — never write citations from memory
3. If no relevant case is found, write the ground without a citation rather than inventing one
4. Make SEPARATE calls for different grounds (bail factors, precedent for anticipatory bail, etc.)"""


class DraftAgentState(TypedDict):
    """State for the drafting agent graph."""

    messages: Annotated[list[AnyMessage], add_messages]
    document: GeneratedDocument | None


def create_rag_tool(rag_client: RAGClient, file_ids: list[str], user_id: str):
    """Create a RAG query tool closed over runtime dependencies."""

    @tool
    async def query_reference_documents(query: str) -> str:
        """Query reference documents for relevant context."""
        if not file_ids:
            logger.debug("No file_ids provided, skipping RAG query")
            return "No reference documents provided."

        logger.debug(f"RAG tool called with query: {query[:50]}... | user={user_id}")
        context = await rag_client.query(file_ids, query, user_id=user_id)

        if not context:
            logger.debug("RAG returned no context")
            return "No relevant context found in the reference documents."

        logger.debug(f"RAG returned {len(context)} chars of context")
        return context

    return query_reference_documents


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

    def _build_graph(self, tools: list):
        """Build a LangGraph workflow for drafting."""
        llm = init_chat_model(self.model_name, model_provider=self.provider)
        llm_with_tools = llm.bind_tools(tools) if tools else llm
        llm_structured = init_chat_model(
            self.model_name, model_provider=self.provider
        ).with_structured_output(GeneratedDocument)
        system_msg = SystemMessage(content=self.system_prompt)

        async def agent_node(state: DraftAgentState):
            response = await llm_with_tools.ainvoke([system_msg] + state["messages"])
            return {"messages": [response]}

        async def output_node(state: DraftAgentState):
            extraction_prompt = (
                "Extract the document you just drafted into the required structured format. "
                "Include the full draft text, title, document type, sections, and summary.\n\n"
                "CRITICAL: The draft field and each section's content must preserve the MARKDOWN "
                "exactly as generated. Do NOT strip formatting, convert to plain text, or remove "
                "table syntax. Keep all **bold**, headings, tables, and --- separators intact.\n\n"
                "IMPORTANT - SINGLE CONTINUOUS OUTPUT:\n"
                "- Combine all paragraphs and sections into a SINGLE CONTINUOUS draft field\n"
                "- Do NOT add extra blank lines between paragraphs\n"
                "- Use --- (horizontal rule) sparingly - only between major sections\n"
                "- The document should flow as ONE continuous piece without page breaks\n\n"
                "For sections: Split the document into its NATURAL sections as they "
                "appear in the actual document. For court filings, use sections like:\n"
                "- 'Cause Title' (court header + party blocks + Vs.)\n"
                "- 'Affidavit' or 'Petition' or 'Application' (opening statement + all numbered paragraphs)\n"
                "- 'Prayer' (prayer/closing clause)\n"
                "- 'Verification' (verification section)\n"
                "DO NOT create artificial sections like 'Case Header', 'Party Details', 'Body', etc. "
                "The section titles should match what appears in the document itself."
            )
            msgs = [system_msg] + state["messages"] + [HumanMessage(content=extraction_prompt)]
            document = await llm_structured.ainvoke(msgs)
            return {"document": document}

        workflow = StateGraph(DraftAgentState)
        workflow.add_node("agent", agent_node)
        if tools:
            workflow.add_node("tools", ToolNode(tools))
        else:
            workflow.add_node("tools", lambda s: s)
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
        logger.debug(f"Starting draft: title='{deps.title}', file_ids={deps.file_ids}")

        examples_section = ""
        if deps.examples:
            examples_section = f"""
=== REFERENCE EXAMPLES AND FORMAT GUIDELINES ===
{deps.examples}
=== END EXAMPLES ===
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

        prompt = f"""Draft the following document using ONLY the information provided.

Document Type: {deps.title}

=== STRUCTURED INPUT DATA ===
The following contains all party details, facts, and requirements extracted from the user's input.
Use these EXACT details - names, ages, addresses, amounts, dates - in your draft.

{deps.instructions}

=== END STRUCTURED INPUT ===
{language_section}{examples_section}
If reference documents are available, use the query_reference_documents tool to gather
relevant context before drafting.

If legal_case_search is available, use it for EACH ground requiring case law support.
Query specifically: e.g., "anticipatory bail Section 438 factors", "bail default Section 167 right",
"chain of circumstantial evidence reasonable doubt". Make targeted per-ground queries.

=== FORMATTING REMINDER ===
- Output CLEAN MARKDOWN following your template exactly
- Tables: Use markdown pipe syntax with header separators |---|
- Bold: **text** for names, headings, emphasis
- Numbered paragraphs: (1), (2), (3) or 1., 2., 3.
- Roman numerals for grounds: (I), (II), (III)
- Amounts: ALWAYS in figures AND words - Rs. 4,25,000/- (Rupees Four Lakh Twenty Five Thousand Only)
- Dates: DD/MM/YYYY format
- DO NOT use placeholders like [Name], [Date], _____, XXXX
- Use descriptive alternatives if info missing ("the plaintiff", "the said property")
- Do NOT output HTML tags. Do NOT use code fences.
- Follow your specialized template EXACTLY.
=== END FORMATTING ===

Generate a COMPLETE, court-ready document following the EXACT markdown template from your specialized prompt."""

        # Create tool with runtime deps
        rag_tool = create_rag_tool(deps.rag_client, deps.file_ids, deps.user_id)
        tools = [rag_tool] if deps.file_ids else []
        if deps.retriever:
            tools.append(create_legal_search_tool(deps.retriever))

        # Build and invoke graph
        graph = self._build_graph(tools)
        result = await graph.ainvoke({"messages": [HumanMessage(content=prompt)], "document": None})

        logger.debug(f"Draft completed: {result['document'].document_type.value}")
        return result["document"]
