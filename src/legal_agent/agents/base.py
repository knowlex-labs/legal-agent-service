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
from legal_agent.models.documents import GeneratedDocument

logger = logging.getLogger(__name__)


@dataclass
class DraftingDependencies:
    """Dependencies injected into drafting agents."""

    rag_client: RAGClient
    file_ids: list[str]
    title: str
    instructions: str
    examples: str = ""


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

Always structure your output with clear sections and proper formatting."""


class DraftAgentState(TypedDict):
    """State for the drafting agent graph."""

    messages: Annotated[list[AnyMessage], add_messages]
    document: GeneratedDocument | None


def create_rag_tool(rag_client: RAGClient, file_ids: list[str]):
    """Create a RAG query tool closed over runtime dependencies."""

    @tool
    async def query_reference_documents(query: str) -> str:
        """Query reference documents for relevant context."""
        if not file_ids:
            logger.debug("No file_ids provided, skipping RAG query")
            return "No reference documents provided."

        logger.debug(f"RAG tool called with query: {query[:50]}...")
        context = await rag_client.query(file_ids, query)

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
                "IMPORTANT for sections: Split the document into its NATURAL sections as they "
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

        prompt = f"""Draft the following document using ONLY the information provided.

Document Type: {deps.title}

=== STRUCTURED INPUT DATA ===
The following contains all party details, facts, and requirements extracted from the user's input.
Use these EXACT details - names, ages, addresses, amounts, dates - in your draft.

{deps.instructions}

=== END STRUCTURED INPUT ===
{examples_section}
If reference documents are available, use the query_reference_documents tool to gather
relevant context before drafting.

=== CRITICAL FORMATTING REQUIREMENTS ===

1. PARTY BLOCKS LAYOUT:
   - Plaintiff/Petitioner name in BOLD
   - Each detail on SEPARATE lines (Age, Occupation, Address lines, Mobile)
   - Role marker (………Plaintiff) RIGHT-ALIGNED on the mobile number line
   - Address broken into multiple lines for readability

2. DOCUMENT STRUCTURE:
   - Court header: CENTERED, UNDERLINED
   - Case number: RIGHT-ALIGNED
   - Vs.: CENTERED between party blocks
   - Document title: CENTERED, UNDERLINED, BOLD
   - Body paragraphs: NUMBERED (1, 2, 3...), JUSTIFIED text
   - Signature block: THREE-COLUMN layout (Place/Date | Plaintiff | Advocate)

3. CONTENT RULES:
   - First body paragraph: Start with "I say that..."
   - Subsequent paragraphs: Start with "That..."
   - Amounts: ALWAYS in figures AND words - Rs. 4,25,000/- (Rupees Four Lakh Twenty Five Thousand Only)
   - Dates: DD/MM/YYYY format
   - DO NOT use placeholders like [Name], [Date], _____, XXXX
   - Use descriptive alternatives if info missing ("the plaintiff", "the said property")

4. OUTPUT FORMAT:
   The generated text should preserve the visual layout when rendered:
   - Use proper line breaks for party blocks
   - Maintain spacing between sections
   - The ………Plaintiff marker should appear right-aligned (use spaces/tabs to position)

Generate a COMPLETE, court-ready document with EXACT formatting as described."""

        # Create tool with runtime deps
        rag_tool = create_rag_tool(deps.rag_client, deps.file_ids)
        tools = [rag_tool] if deps.file_ids else []

        # Build and invoke graph
        graph = self._build_graph(tools)
        result = await graph.ainvoke({"messages": [HumanMessage(content=prompt)], "document": None})

        logger.debug(f"Draft completed: {result['document'].document_type.value}")
        return result["document"]
