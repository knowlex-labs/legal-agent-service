import time
import logging
from typing import List, Dict, Any, Optional
from legal_agent.rag_engine.config import Config

logger = logging.getLogger(__name__)

class Reranker:
    """
    Reranker module for improving document relevance in RAG pipeline.
    Uses CrossEncoder to rerank retrieved documents based on query relevance.
    Implements singleton pattern for efficient model loading.
    """

    _instance = None
    _model = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        # Lazy loading - model will be loaded on first use
        pass

    def _ensure_model_loaded(self):
        """Load model lazily on first use."""
        if self._model is None:
            try:
                logger.info(f"Loading reranker model: {Config.reranking.RERANKER_MODEL}")
                from sentence_transformers import CrossEncoder
                self._model = CrossEncoder(Config.reranking.RERANKER_MODEL)
                logger.info("Reranker model loaded successfully")
            except Exception as e:
                logger.error(f"Failed to load reranker model: {e}")
                self._model = None

    def rerank(self, query: str, documents: List[Dict[str, Any]], top_k: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        Rerank documents based on query relevance using CrossEncoder.

        Args:
            query: The search query
            documents: List of document dictionaries (must have 'text' field)
            top_k: Number of top documents to return (defaults to config value)

        Returns:
            List of reranked documents, sorted by relevance score
        """
        start_time = time.time()

        # Use config value if top_k not specified
        final_top_k = top_k or Config.reranking.RERANKER_TOP_K
        logger.info(f"Fetching top {final_top_k} documents")

        # Load model lazily if enabled
        if Config.reranking.RERANKER_ENABLED:
            self._ensure_model_loaded()

        # If reranker is disabled or model failed to load, return original order
        if not Config.reranking.RERANKER_ENABLED or self._model is None:
            logger.info("Reranker disabled or model unavailable, returning original order")
            return documents[:final_top_k]

        # If no documents or empty query, return as-is
        if not documents or not query.strip():
            return documents[:final_top_k]

        try:
            # Extract text content from documents for scoring
            document_texts = []
            for doc in documents:
                # Try different possible text fields
                text = doc.get("text") or doc.get("content") or doc.get("payload", {}).get("text", "")
                if not text:
                    logger.warning(f"Document missing text content: {doc}")
                    text = ""
                document_texts.append(text)

            # Create query-document pairs for the CrossEncoder
            pairs = [(query, text) for text in document_texts]

            # Get relevance scores from the model
            scores = self._model.predict(pairs)

            # Combine documents with their scores
            scored_docs = list(zip(documents, scores))

            # Sort by relevance score (highest first)
            scored_docs.sort(key=lambda x: x[1], reverse=True)

            # Extract the reranked documents
            reranked_docs = [doc for doc, score in scored_docs[:final_top_k]]

            elapsed_time = time.time() - start_time
            logger.info(f"Reranking completed in {elapsed_time:.3f}s for {len(documents)} documents -> {len(reranked_docs)} results")

            return reranked_docs

        except Exception as e:
            logger.error(f"Error during reranking: {e}")
            # Fallback to original order on error
            return documents[:final_top_k]

    def is_available(self) -> bool:
        """Check if reranker is available and enabled."""
        if Config.reranking.RERANKER_ENABLED:
            self._ensure_model_loaded()
        return Config.reranking.RERANKER_ENABLED and self._model is not None

# Global reranker instance
reranker = Reranker()