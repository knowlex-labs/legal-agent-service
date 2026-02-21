"""Tools for the case agent."""

import logging

from langchain_core.tools import tool

from legal_agent.clients.rag_client import RAGClient

logger = logging.getLogger(__name__)


def create_source_query_tool(rag_client: RAGClient, source_ids: list[str]):
    """Create a tool that queries source documents via the RAG client."""

    @tool
    async def query_source_documents(query: str) -> str:
        """Query the case's source documents for relevant content. Use this to find specific information from uploaded documents like contracts, filings, evidence, or correspondence."""
        if not source_ids:
            return "No source documents available."

        logger.debug(f"query_source_documents called: query='{query[:50]}...' source_ids={source_ids}")
        context = await rag_client.query(source_ids, query)

        if not context:
            return "No relevant content found in the source documents."

        logger.debug(f"Source query returned {len(context)} chars")
        return context

    return query_source_documents
