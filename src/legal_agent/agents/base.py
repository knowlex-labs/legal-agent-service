"""Base drafting agent with common functionality."""

import logging
from dataclasses import dataclass

from pydantic_ai import Agent, RunContext

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


class BaseDraftingAgent:
    """Base class for all drafting agents."""

    system_prompt: str = BASE_SYSTEM_PROMPT
    agent: Agent[DraftingDependencies, GeneratedDocument]

    def __init__(self, model: str = "openai:gpt-4o"):
        """Initialize the drafting agent.

        Args:
            model: The LLM model to use (e.g., 'openai:gpt-4o', 'anthropic:claude-3-opus')
        """
        self.agent = Agent(
            model,
            system_prompt=self.system_prompt,
            output_type=GeneratedDocument,
            deps_type=DraftingDependencies,
        )
        self._register_tools()

    def _register_tools(self) -> None:
        """Register common tools for the agent."""

        @self.agent.tool
        async def query_reference_documents(
            ctx: RunContext[DraftingDependencies], query: str
        ) -> str:
            """Query reference documents for relevant context."""
            if not ctx.deps.file_ids:
                logger.debug("No file_ids provided, skipping RAG query")
                return "No reference documents provided."

            logger.debug(f"RAG tool called with query: {query[:50]}...")
            context = await ctx.deps.rag_client.query(ctx.deps.file_ids, query)

            if not context:
                logger.debug("RAG returned no context")
                return "No relevant context found in the reference documents."

            logger.debug(f"RAG returned {len(context)} chars of context")
            return context

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

        result = await self.agent.run(prompt, deps=deps)
        logger.debug(f"Draft completed: {result.output.document_type.value}")
        return result.output
