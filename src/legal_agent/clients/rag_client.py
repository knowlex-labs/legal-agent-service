"""RAG client for querying document context from the RAG engine."""

import logging
from abc import ABC, abstractmethod

import httpx

from legal_agent.config import Settings

logger = logging.getLogger(__name__)


class RAGClient(ABC):
    """Abstract interface for RAG engine clients."""

    @abstractmethod
    async def query(self, file_ids: list[str], query: str) -> str:
        """Query the RAG engine for relevant context."""
        pass


class HTTPRAGClient(RAGClient):
    """HTTP client for RAG engine /api/v1/retrieve endpoint."""

    def __init__(self, settings: Settings):
        self.base_url = settings.rag_engine_base_url.rstrip("/")
        self.user_id = settings.rag_engine_user_id
        self._client: httpx.AsyncClient | None = None
        logger.info(f"Initialized RAG client with base_url={self.base_url}")

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers={"X-User-Id": self.user_id},
                timeout=30.0,
            )
        return self._client

    async def query(self, file_ids: list[str], query: str) -> str:
        """Query RAG engine and return formatted context string."""
        if not query:
            logger.debug("Empty query, skipping RAG retrieval")
            return ""

        client = await self._get_client()

        filters = None
        if file_ids:
            filters = {"file_ids": file_ids, "content_type": "legal"}

        request_body = {"query": query, "filters": filters, "top_k": 10}

        logger.debug(f"RAG request: query='{query[:50]}...' file_ids={file_ids}")

        try:
            response = await client.post("/api/v1/retrieve", json=request_body)
            response.raise_for_status()
            data = response.json()

            if not data.get("success"):
                logger.warning(f"RAG returned success=false: {data}")
                return ""

            results = data.get("results", [])
            if not results:
                logger.info(f"No results from RAG for query: {query[:50]}...")
                return ""

            logger.info(f"RAG returned {len(results)} chunks")

            return self._format_chunks(results)

        except httpx.TimeoutException:
            logger.error(f"RAG request timed out for query: {query[:50]}...")
            return ""
        except httpx.HTTPStatusError as e:
            logger.error(f"RAG HTTP error {e.response.status_code}: {e.response.text}")
            return ""
        except httpx.RequestError as e:
            logger.error(f"RAG request failed: {e}")
            return ""

    def _format_chunks(self, chunks: list[dict]) -> str:
        """Format RAG chunks into a context string for the LLM."""
        context_parts = []

        for chunk in chunks:
            text = chunk.get("chunk_text", "")
            if not text:
                continue

            score = chunk.get("relevance_score", 0)
            page = chunk.get("page_number")
            concepts = chunk.get("concepts", [])

            header = f"[Relevance: {score:.2f}]"
            if page:
                header += f" [Page: {page}]"
            if concepts:
                header += f" [Concepts: {', '.join(concepts[:3])}]"

            context_parts.append(f"{header}\n{text}")

        return "\n\n---\n\n".join(context_parts)

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None
            logger.debug("RAG client closed")


class MockRAGClient(RAGClient):
    """Mock RAG client for testing."""

    async def query(self, file_ids: list[str], query: str) -> str:
        if not file_ids:
            return ""
        return (
            f"[Mock RAG Context]\n"
            f"Query: {query}\n"
            f"Files: {', '.join(file_ids)}\n"
            f"This is placeholder context from mock RAG client."
        )
