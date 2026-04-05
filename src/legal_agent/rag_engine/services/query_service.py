from typing import List, Dict, Any, Optional
from legal_agent.rag_engine.repositories.qdrant_repository import QdrantRepository
from legal_agent.rag_engine.repositories.feedback_repository import FeedbackRepository
from legal_agent.rag_engine.utils.embedding_client import embedding_client
from legal_agent.rag_engine.utils.llm_client import LlmClient
from legal_agent.rag_engine.utils.response_enhancer import enhance_response_if_needed
from legal_agent.rag_engine.models.api_models import QueryResponse, ChunkConfig, CriticEvaluation, ChunkType
from legal_agent.rag_engine.core.reranker import reranker
from legal_agent.rag_engine.core.critic import critic
from legal_agent.config import get_settings
import re
import logging

logger = logging.getLogger(__name__)

class QueryService:
    def __init__(self):
        self.qdrant_repo = QdrantRepository()
        self.embedding_client = embedding_client
        self.llm_client = LlmClient()
        self.feedback_repo = FeedbackRepository()

        # Patterns for detecting query intent
        self.concept_patterns = [
            r'^what (is|are|does|do)',
            r'^explain',
            r'^define',
            r'^describe',
            r'definition of',
            r'meaning of',
            r'concept of',
            r'understanding',
            r'tell me about'
        ]
        self.example_patterns = [
            r'example',
            r'show me',
            r'demonstrate',
            r'illustration',
            r'sample',
            r'case study'
        ]
        self.question_patterns = [
            r'^how (do|to|can)',
            r'^solve',
            r'^calculate',
            r'^find',
            r'^determine',
            r'practice',
            r'exercise',
            r'problem'
        ]

    def _detect_query_intent(self, query: str) -> Optional[str]:
        """Detect query intent to prioritize matching chunk types."""
        query_lower = query.lower().strip()

        for pattern in self.concept_patterns:
            if re.search(pattern, query_lower, re.IGNORECASE):
                return ChunkType.CONCEPT.value

        for pattern in self.example_patterns:
            if re.search(pattern, query_lower, re.IGNORECASE):
                return ChunkType.EXAMPLE.value

        for pattern in self.question_patterns:
            if re.search(pattern, query_lower, re.IGNORECASE):
                return ChunkType.QUESTION.value

        return None  # No specific intent, search all types

    def _query_qdrant(
        self,
        collection_name: str,
        query_vector: List[float],
        limit: int,
        collection_ids: Optional[List[str]] = None,
        file_ids: Optional[List[str]] = None,
        content_type: Optional[str] = None
    ) -> List[Dict]:
        """Query Qdrant and normalize results for the retrieval pipeline."""
        raw_results = self.qdrant_repo.query_collection(
            collection_name=collection_name,
            query_vector=query_vector,
            limit=limit,
            collection_ids=collection_ids,
            file_ids=file_ids,
        )

        normalized = []
        for result in raw_results:
            payload = result.get("payload", {})
            metadata = payload.get("metadata", {})

            if content_type and not file_ids and metadata.get("content_type") != content_type:
                continue

            normalized.append({
                "chunk_id": payload.get("chunk_id", ""),
                "text": payload.get("text", ""),
                "file_id": metadata.get("file_id", ""),
                "chunk_type": metadata.get("chunk_type", ""),
                "page_start": metadata.get("page_start"),
                "key_terms": metadata.get("key_terms", []),
                "score": result.get("score", 0.0)
            })
        return normalized

    def _smart_chunk_retrieval(
        self,
        collection_name: str,
        query_vector: List[float],
        query_text: str,
        limit: int = 10,
        collection_ids: Optional[List[str]] = None,
        file_ids: Optional[List[str]] = None,
        content_type: Optional[str] = None,
    ) -> List[Dict]:
        intent = self._detect_query_intent(query_text)

        results = self._query_qdrant(
            collection_name=collection_name,
            query_vector=query_vector,
            limit=limit,
            collection_ids=collection_ids,
            file_ids=file_ids,
            content_type=content_type,
        )

        if not intent:
            return results

        prioritized = []
        secondary = []

        for result in results:
            chunk_type = result.get("chunk_type", "")
            if chunk_type == intent:
                prioritized.append(result)
            else:
                secondary.append(result)

        combined = prioritized + secondary
        return combined[:limit]

    def _filter_relevant_results(self, results: List[Dict], threshold: float = None) -> List[Dict]:
        if threshold is None:
            threshold = get_settings().relevance_threshold
        return [result for result in results if result.get("score", 0) >= threshold]

    def _is_valid_text(self, text: str) -> bool:
        if not text or len(text.strip()) == 0:
            return False
        printable_chars = sum(1 for c in text if c.isprintable() or c.isspace())
        return (printable_chars / len(text)) > 0.8

    def _extract_relevant_chunks(self, results: List[Dict]) -> List[ChunkConfig]:
        if not results:
            return []

        chunks = []
        seen_texts = set()

        for result in results:
            text = result.get("text", "")
            file_id = result.get("file_id", "unknown")
            chunk_id = result.get("chunk_id", "")
            page_start = result.get("page_start")
            score = result.get("score", 0.0)
            key_terms = result.get("key_terms", [])

            if text and self._is_valid_text(text) and text not in seen_texts:
                seen_texts.add(text)
                chunks.append(ChunkConfig(
                    source=file_id,
                    text=text,
                    chunk_id=chunk_id,
                    relevance_score=score,
                    file_id=file_id,
                    page_number=page_start,
                    timestamp=None,
                    concepts=key_terms if isinstance(key_terms, list) else []
                ))

        return chunks[:5]

    def _extract_full_texts(self, results: List[Dict]) -> List[str]:
        full_texts = []
        seen_texts = set()

        for result in results:
            text = result.get("text", "")

            if text and self._is_valid_text(text) and text not in seen_texts:
                seen_texts.add(text)
                full_texts.append(text)

        return full_texts[:5]

    def _calculate_confidence(self, results: List[Dict]) -> float:
        if not results:
            return 0.0
        return max(result.get("score", 0) for result in results)

    def _create_query_response(self, results: List[Dict], query: str, enable_critic: bool = True, structured_output: bool = False, answer_style: str = "detailed") -> QueryResponse:
        relevant_results = self._filter_relevant_results(results)

        if not relevant_results:
            return QueryResponse(
                answer="Context not found",
                confidence=0.0,
                is_relevant=False,
                chunks=[]
            )

        chunks = self._extract_relevant_chunks(relevant_results)

        if not chunks:
            return QueryResponse(
                answer="Error: Stored content is corrupted or unreadable",
                confidence=0.0,
                is_relevant=False,
                chunks=[]
            )

        # Tag image chunks so the LLM knows they describe visual content
        chunk_texts = []
        for i, chunk in enumerate(chunks):
            result = relevant_results[i] if i < len(relevant_results) else {}
            chunk_type = result.get("chunk_type", "")
            if chunk_type == "image":
                chunk_texts.append(f"[Image Content]: {chunk.text}")
            else:
                chunk_texts.append(chunk.text)
        full_chunk_texts = self._extract_full_texts(relevant_results)
        answer = self.llm_client.generate_answer(query, chunk_texts, force_json=structured_output, answer_style=answer_style)
        answer = enhance_response_if_needed(answer, query)

        confidence = self._calculate_confidence(relevant_results)

        critic_result = None
        if enable_critic and critic.is_available():
            if critic_evaluation := critic.evaluate(query, full_chunk_texts, answer):
                critic_result = CriticEvaluation(**critic_evaluation)

        return QueryResponse(
            answer=answer,
            confidence=confidence,
            is_relevant=True,
            chunks=chunks,
            critic=critic_result
        )

    def _apply_feedback_scoring(self, results: List[Dict], query_vector: List[float],
                              collection_name: str) -> List[Dict]:
        if not get_settings().feedback_enabled or not results:
            return results

        try:
            relevant_feedback = self.feedback_repo.get_relevant_feedback(
                query_vector, collection_name, get_settings().feedback_similarity_threshold
            )

            if not relevant_feedback:
                return results

            doc_ids = [result.get("payload", {}).get("document_id", "") for result in results]
            feedback_scores = self.feedback_repo.calculate_feedback_scores(doc_ids, relevant_feedback)

            for result in results:
                doc_id = result.get("payload", {}).get("document_id", "")
                original_score = result.get("score", 0.0)
                rerank_score = result.get("rerank_score", original_score)
                feedback_score = feedback_scores.get(doc_id, 0.5)

                final_score = (0.45 * original_score + 0.35 * rerank_score +
                             0.10 * feedback_score + 0.10 * feedback_score)

                result["score"] = final_score
                result["feedback_score"] = feedback_score

            results.sort(key=lambda x: x.get("score", 0), reverse=True)
        except Exception:
            return results

    async def retrieve_context(
        self,
        query: str,
        user_id: str,
        collection_ids: Optional[List[str]] = None,
        top_k: int = 5,
        file_ids: Optional[List[str]] = None,
        content_type: Optional[str] = None,
        enable_reranking: bool = True,
    ) -> List[Dict[str, Any]]:
        """
        Retrieve relevant context chunks for a query without generating an answer.
        This provides a cleaner interface for summary and other internal services.
        """
        try:
            query_vector = self.embedding_client.generate_single_embedding(query)
            collection_name = user_id  # user_id here is actually the collection_id

            results = self._smart_chunk_retrieval(
                collection_name,
                query_vector,
                query,
                top_k,
                collection_ids=collection_ids,
                file_ids=file_ids,
                content_type=content_type,
            )

            if enable_reranking and reranker.is_available() and results:
                results = reranker.rerank(query, results)

            return results
        except Exception as e:
            logger.error(f"Error retrieving context: {str(e)}")
            return []

    def get_all_embeddings(self, collection_name: str, limit: int = 100) -> Dict[str, Any]:
        return {"message": "Get all embeddings not implemented"}

    def search(self, collection_name: str, query_text: str, limit: int = 10, enable_critic: bool = True, structured_output: bool = False, collection_ids: Optional[List[str]] = None, file_ids: Optional[List[str]] = None, content_type: Optional[str] = None, answer_style: str = "detailed") -> QueryResponse:
        try:
            query_vector = self.embedding_client.generate_single_embedding(query_text)

            results = self._smart_chunk_retrieval(
                collection_name,
                query_vector,
                query_text,
                limit,
                collection_ids=collection_ids,
                file_ids=file_ids,
                content_type=content_type,
            )

            if reranker.is_available() and results:
                results = reranker.rerank(query_text, results)

            results = self._apply_feedback_scoring(results, query_vector, collection_name)

            return self._create_query_response(results, query_text, enable_critic, structured_output, answer_style=answer_style)

        except Exception as e:
            logger.error(f"Error in query search: {str(e)}")
            return QueryResponse(
                answer="Context not found",
                confidence=0.0,
                is_relevant=False,
                chunks=[]
            )