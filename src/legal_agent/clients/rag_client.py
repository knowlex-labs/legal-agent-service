"""RAG client for querying document context from the RAG engine."""

import logging
import asyncio
from abc import ABC, abstractmethod

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

        logger.debug(
            f"LocalRAG request: query='{query[:50]}...' user={user_id} "
            f"collection={collection} file_ids={file_ids}"
        )

        try:
            query_service = self._get_query_service()
            all_results = []
            for file_id in file_ids:
                file_results = []
                for attempt in range(3):
                    try:
                        file_results = await query_service.retrieve_context(
                            query=query,
                            user_id=collection,
                            top_k=8,
                            file_ids=[file_id],
                        )
                        break
                    except Exception as e:
                        if "429" in str(e):
                            wait = 2**attempt #exponential backoff
                            logger.warning(
                                f"[RAG] Rate limited for {file_id}. Retrying in {wait}s..."
                            )
                            await asyncio.sleep(wait)
                        else:
                            logger.error(f"[RAG] Failed for file {file_id}: {e}")
                            break

                if file_results:
                    logger.info(f"[RAG] {file_id} → {len(file_results)} chunks")
                    all_results.extend(file_results)
                else:
                    logger.warning(f"[RAG] {file_id} → 0 chunks after retries")
                # ✅ small delay to avoid burst limits
                await asyncio.sleep(0.3)

            results = all_results
            if not results:
                logger.warning(f"[RAG] No results for ANY file_ids: {file_ids}")
                return ""

            logger.info(f"[RAG] Total chunks collected: {len(results)}")

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

