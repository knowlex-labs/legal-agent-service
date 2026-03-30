"""LangChain tool wrapper for the legal case retriever."""

import logging

from langchain_core.tools import tool

from legal_agent.legal_retrieval.retriever import LegalCaseRetriever

logger = logging.getLogger(__name__)


def create_legal_search_tool(retriever: LegalCaseRetriever):

    @tool
    def legal_case_search(
        query: str,
        court: str | None = None,
        year_from: int | None = None,
        year_to: int | None = None,
        judge: str | None = None,
        top_k: int = 5,
    ) -> str:
        """Search Indian Supreme Court and High Court judgments for relevant case law.

        When citing a result in your draft, use EXACTLY this format:
          **[case_title]** — [citation]
        Examples:
          **Sushila Aggarwal v. State (NCT Delhi)** — (2020) 5 SCC 1
          **Arnesh Kumar v. State of Bihar** — (2014) 8 SCC 273

        Do NOT cite inline as [L1], [L2]. Always write the full case name and citation.
        Only cite cases returned by this tool — never write citations from memory.

        Args:
            query: Legal search query (e.g., "right to bail under Section 439 CrPC").
            court: Filter by court name.
            year_from: Filter cases from this year onwards.
            year_to: Filter cases up to this year.
            judge: Filter by judge name on the bench.
            top_k: Number of results (default 5, max 10).
        """
        try:
            logger.info(f"Tool called — query='{query}', court={court}, year={year_from}-{year_to}, judge={judge}")
            if court:
                court = f"%{court}%"
            filters = {k: v for k, v in {"court": court, "year_from": year_from, "year_to": year_to, "judge": judge}.items() if v}
            results = retriever.search(query=query, filters=filters or None, top_k=min(top_k, 10))
            logger.info(f"Search returned {len(results)} results")

            if not results:
                return "No relevant cases found for the given query."

            parts = [f"Found {len(results)} relevant cases:\n"]
            for i, r in enumerate(results, 1):
                text = r.get("text", "")
                if len(text) > 800:
                    text = text[:800] + "..."
                para_num = r.get("paragraph_number", "N/A")
                parts.append(
                    f"---\n"
                    f"Result {i}:\n"
                    f"Title: {r.get('case_title', 'Unknown')}\n"
                    f"Citation: {r.get('citation', 'N/A')}\n"
                    f"Court: {r.get('court', 'N/A')}\n"
                    f"Year: {r.get('year', 'N/A')}\n"
                    f"Relevant Paragraph (para {para_num}):\n\"{text}\"\n"
                )
            return "\n".join(parts)

        except Exception as e:
            logger.exception("legal_case_search failed")
            return f"Error searching case law database: {e}"

    return legal_case_search
