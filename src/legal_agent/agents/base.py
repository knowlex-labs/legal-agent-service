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


# Base system prompt for all legal drafting agents
BASE_SYSTEM_PROMPT = """You are an expert legal drafting assistant specializing in Indian law.
Your task is to draft professional, legally sound documents based on the provided instructions.

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

        prompt = f"""Please draft the following document:

Title: {deps.title}

Instructions:
{deps.instructions}

If reference documents are available, use the query_reference_documents tool to gather
relevant context before drafting."""

        result = await self.agent.run(prompt, deps=deps)
        logger.debug(f"Draft completed: {result.output.document_type.value}")
        return result.output
