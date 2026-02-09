"""Legal case retrieval module with pgvector hybrid search."""

from legal_agent.legal_retrieval.langchain_tools import create_legal_search_tool
from legal_agent.legal_retrieval.retriever import LegalCaseRetriever

__all__ = ["LegalCaseRetriever", "create_legal_search_tool"]
