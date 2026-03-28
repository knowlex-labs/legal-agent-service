import json
import os
from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple
import numpy as np
from legal_agent.rag_engine.config import Config


class FeedbackRepository:
    def __init__(self):
        self.feedback_file = "feedback_data.jsonl"

    def save_feedback(self, query: str, query_vector: List[float], doc_ids: List[str],
                     label: int, collection: str) -> bool:
        try:
            feedback_entry = {
                "query": query,
                "q_vec": query_vector,
                "doc_ids": doc_ids,
                "label": label,
                "collection": collection,
                "timestamp": datetime.utcnow().isoformat() + "Z"
            }

            with open(self.feedback_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(feedback_entry) + "\n")

            return True
        except Exception:
            return False

    def get_relevant_feedback(self, query_vector: List[float], collection: str,
                            similarity_threshold: float = 0.8) -> List[Dict[str, Any]]:
        if not os.path.exists(self.feedback_file):
            return []

        relevant_feedback = []
        query_vec = np.array(query_vector)

        try:
            with open(self.feedback_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue

                    feedback = json.loads(line)

                    if feedback.get("collection") != collection:
                        continue

                    stored_vec = np.array(feedback.get("q_vec", []))
                    if len(stored_vec) == 0:
                        continue

                    similarity = self._cosine_similarity(query_vec, stored_vec)

                    if similarity >= similarity_threshold:
                        feedback["similarity"] = similarity
                        relevant_feedback.append(feedback)

            relevant_feedback.sort(key=lambda x: x["similarity"], reverse=True)
            return relevant_feedback

        except Exception:
            return []

    def calculate_feedback_scores(self, doc_ids: List[str],
                                relevant_feedback: List[Dict[str, Any]]) -> Dict[str, float]:
        doc_scores = {}

        for doc_id in doc_ids:
            positive_count = 0
            total_count = 0

            for feedback in relevant_feedback:
                feedback_doc_ids = feedback.get("doc_ids", [])
                if doc_id in feedback_doc_ids:
                    total_count += 1
                    if feedback.get("label", 0) == 1:
                        positive_count += 1

            if total_count == 0:
                doc_scores[doc_id] = 0.5
            else:
                doc_scores[doc_id] = self._bayesian_smooth(positive_count, total_count, 1.0, 1.0)

        return doc_scores

    def _cosine_similarity(self, vec1: np.ndarray, vec2: np.ndarray) -> float:
        try:
            norm1 = np.linalg.norm(vec1)
            norm2 = np.linalg.norm(vec2)

            if norm1 == 0 or norm2 == 0:
                return 0.0

            return np.dot(vec1, vec2) / (norm1 * norm2)
        except Exception:
            return 0.0

    def _bayesian_smooth(self, positive: int, total: int, alpha: float = 1.0,
                        beta: float = 1.0) -> float:
        return (positive + alpha) / (total + alpha + beta)

    def get_feedback_stats(self, collection: Optional[str] = None) -> Dict[str, Any]:
        if not os.path.exists(self.feedback_file):
            return {"total_feedback": 0, "positive_ratio": 0.0, "collections": []}

        total_feedback = 0
        positive_feedback = 0
        collections = set()

        try:
            with open(self.feedback_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue

                    feedback = json.loads(line)
                    feedback_collection = feedback.get("collection", "")

                    if collection and feedback_collection != collection:
                        continue

                    total_feedback += 1
                    collections.add(feedback_collection)

                    if feedback.get("label", 0) == 1:
                        positive_feedback += 1

            positive_ratio = positive_feedback / total_feedback if total_feedback > 0 else 0.0

            return {
                "total_feedback": total_feedback,
                "positive_feedback": positive_feedback,
                "negative_feedback": total_feedback - positive_feedback,
                "positive_ratio": positive_ratio,
                "collections": list(collections)
            }

        except Exception:
            return {"total_feedback": 0, "positive_ratio": 0.0, "collections": []}