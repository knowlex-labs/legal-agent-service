"""LangChain tool wrapper for the legal case retriever."""

import logging

from langchain_core.tools import tool

from legal_agent.legal_retrieval.retriever import LegalCaseRetriever

logger = logging.getLogger(__name__)


def create_legal_search_tool(retriever: LegalCaseRetriever):
    """Create a LangChain tool that wraps the legal case retriever.

    Args:
        retriever: An initialized LegalCaseRetriever instance.

    Returns:
        A LangChain @tool function for searching legal cases.
    """

    @tool
    def legal_case_search(
        query: str,
        court: str | None = None,
        year_from: int | None = None,
        year_to: int | None = None,
        judge: str | None = None,
        top_k: int = 5,
    ) -> str:
        """Search Indian Supreme Court judgments for relevant case law.

        Use this tool to find cases related to a legal question, statute,
        or legal principle. You can filter by court, year range, or judge.

        Args:
            query: The legal search query (e.g., "right to bail under Section 439 CrPC").
            court: Filter by court name (optional).
            year_from: Filter cases from this year onwards (optional).
            year_to: Filter cases up to this year (optional).
            judge: Filter by judge name on the bench (optional).
            top_k: Number of results to return (default 5, max 10).
        """
        try:
            top_k = min(top_k, 10)
            filters = {}
            if court:
                filters["court"] = court
            if year_from:
                filters["year_from"] = year_from
            if year_to:
                filters["year_to"] = year_to
            if judge:
                filters["judge"] = judge

            results = retriever.search(
                query=query,
                filters=filters if filters else None,
                top_k=top_k,
            )

            if not results:
                return "No relevant cases found for the given query."

            output_parts = [f"Found {len(results)} relevant cases:\n"]
            for i, result in enumerate(results, 1):
                citation = result.get("citation", "N/A")
                case_name = result.get("case_title", "Unknown")
                court_name = result.get("court", "")
                year = result.get("year", "")
                para_num = result.get("paragraph_number", "")
                text = result.get("text", "")

                # Truncate long paragraphs
                if len(text) > 800:
                    text = text[:800] + "..."

                output_parts.append(
                    f"---\n"
                    f"**Case {i}: {case_name}**\n"
                    f"Citation: {citation} | Court: {court_name} | Year: {year}\n"
                    f"Paragraph {para_num}:\n{text}\n"
                )

            return "\n".join(output_parts)

        except Exception as e:
            logger.exception("Error in legal_case_search tool")
            return f"Error searching case law database: {e}"

    return legal_case_search
