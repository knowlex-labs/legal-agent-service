"""RAG client for querying document context from the RAG engine."""

import logging
from abc import ABC, abstractmethod

import httpx

from legal_agent.config import Settings

logger = logging.getLogger(__name__)


class RAGClientError(Exception):
    """Raised when RAG client fails to retrieve context."""

    pass


def _collection_for_user(user_id: str) -> str:
    """Derive the Qdrant collection name from user ID."""
    return f"user_{user_id}"


class RAGClient(ABC):
    """Abstract interface for RAG engine clients."""

    @abstractmethod
    async def query(
        self,
        file_ids: list[str],
        query: str,
        user_id: str,
    ) -> str:
        """Query the RAG engine for relevant context."""
        pass


class LocalRAGClient(RAGClient):
    """In-process RAG client — calls QueryService directly without HTTP overhead.

    Used when the rag-engine code is co-located in the same process (merged service).
    """

    def __init__(self):
        # Lazy-import to avoid circular imports at module load time
        self._query_service = None
        logger.info("Initialized LocalRAGClient (in-process RAG)")

    def _get_query_service(self):
        if self._query_service is None:
            from legal_agent.rag_engine.services.query_service import QueryService
            self._query_service = QueryService()
        return self._query_service

    async def query(
        self,
        file_ids: list[str],
        query: str,
        user_id: str,
    ) -> str:
        """Query RAG engine and return formatted context string.

        Calls QueryService.retrieve_context directly in-process.
        Collection is derived as 'user_{user_id}' to match Qdrant naming convention.
        """
        if not query:
            logger.debug("Empty query, skipping RAG retrieval")
            return ""

        collection = _collection_for_user(user_id)

        filters_file_ids = file_ids if file_ids else None

        logger.debug(
            f"LocalRAG request: query='{query[:50]}...' user={user_id} "
            f"collection={collection} file_ids={file_ids}"
        )

        try:
            query_service = self._get_query_service()
            results = await query_service.retrieve_context(
                query=query,
                user_id=collection,
                top_k=10,
                file_ids=filters_file_ids,
            )

            if not results:
                logger.info(f"No results from RAG for query: {query[:50]}...")
                return ""

            logger.info(f"RAG returned {len(results)} chunks")
            return self._format_chunks(results)

        except Exception as e:
            logger.error(f"LocalRAG request failed: {e}")
            return ""

    def _format_chunks(self, chunks: list[dict]) -> str:
        """Format RAG chunks into a context string for the LLM."""
        context_parts = []

        for i, chunk in enumerate(chunks, 1):
            text = chunk.get("text", "")
            if not text:
                continue

            score = chunk.get("score", 0)
            page = chunk.get("page_start")
            concepts = chunk.get("key_terms", [])
            file_id = chunk.get("file_id", "")

            header = f"[Indexed chunk {i}] [Relevance: {score:.2f}]"
            if file_id:
                header += f" [File id: {file_id}]"
            if page is not None:
                header += f" [Page: {page}]"
            if concepts:
                header += f" [Concepts: {', '.join(concepts[:3])}]"

            context_parts.append(f"{header}\n{text}")

        return "\n\n---\n\n".join(context_parts)

    async def close(self) -> None:
        pass


class HTTPRAGClient(RAGClient):
    """HTTP client for RAG engine /api/v1/collections/{collection}/retrieve endpoint.

    Kept for backwards-compatibility / standalone deployments where rag-engine
    is still running as a separate service.
    """

    def __init__(self, settings: Settings):
        self.base_url = settings.rag_engine_base_url.rstrip("/")
        self._client: httpx.AsyncClient | None = None
        logger.info(f"Initialized HTTPRAGClient with base_url={self.base_url}")

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=30.0,
            )
        return self._client

    async def query(
        self,
        file_ids: list[str],
        query: str,
        user_id: str,
    ) -> str:
        """Query RAG engine and return formatted context string.

        The collection is derived as 'user_{user_id}' to match the RAG engine's
        physical Qdrant collection naming convention.
        """
        if not query:
            logger.debug("Empty query, skipping RAG retrieval")
            return ""

        collection = _collection_for_user(user_id)
        client = await self._get_client()
        headers = {"X-User-Id": user_id}

        filters = None
        if file_ids:
            filters = {"file_ids": file_ids, "content_type": "legal"}

        request_body = {"query": query, "filters": filters, "top_k": 10}

        logger.debug(
            f"RAG request: query='{query[:50]}...' user={user_id} "
            f"collection={collection} file_ids={file_ids}"
        )

        try:
            response = await client.post(
                f"/api/v1/collections/{collection}/retrieve",
                json=request_body,
                headers=headers,
            )
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
            if e.response.status_code == 404:
                logger.info(f"RAG collection not found for user {user_id}, skipping retrieval")
                return ""
            logger.error(f"RAG HTTP error {e.response.status_code}: {e.response.text}")
            return ""
        except httpx.RequestError as e:
            logger.error(f"RAG request failed: {e}")
            return ""

    def _format_chunks(self, chunks: list[dict]) -> str:
        """Format RAG chunks into a context string for the LLM."""
        context_parts = []

        for i, chunk in enumerate(chunks, 1):
            text = chunk.get("chunk_text", "")
            if not text:
                continue

            score = chunk.get("relevance_score", 0)
            page = chunk.get("page_number")
            concepts = chunk.get("concepts", [])
            file_id = chunk.get("file_id") or chunk.get("source") or ""

            header = f"[Indexed chunk {i}] [Relevance: {score:.2f}]"
            if file_id:
                header += f" [File id: {file_id}]"
            if page is not None:
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

    async def query(
        self,
        file_ids: list[str],
        query: str,
        user_id: str,
    ) -> str:
        if not file_ids:
            return ""
        collection = _collection_for_user(user_id)
        return (
            f"[Mock RAG Context]\n"
            f"Query: {query}\n"
            f"User: {user_id} | Collection: {collection}\n"
            f"Files: {', '.join(file_ids)}\n"
            f"This is placeholder context from mock RAG client."
        )
