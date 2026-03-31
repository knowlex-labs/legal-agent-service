import google.generativeai as genai
import json
import time
import logging
from typing import List, Dict, Any, Optional
from legal_agent.config import get_settings

logger = logging.getLogger(__name__)

class CriticHead:
    _instance = None
    _model = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if self._model is None and get_settings().critic_model_api_key:
            try:
                genai.configure(api_key=get_settings().critic_model_api_key)
                self._model = genai.GenerativeModel(get_settings().critic_model_name)
                logger.info(f"Critic model loaded: {get_settings().critic_model_name}")
            except Exception as e:
                logger.error(f"Failed to load critic model: {e}")
                self._model = None

    def evaluate(self, query: str, context_chunks: List[str], answer: str) -> Optional[Dict[str, Any]]:
        if not get_settings().critic_enabled or not self._model:
            return None

        start_time = time.time()

        try:
            context_text = "\n\n".join(context_chunks)
            prompt = self._build_evaluation_prompt(query, context_text, answer)

            response = self._model.generate_content(
                prompt,
                generation_config=genai.types.GenerationConfig(
                    temperature=get_settings().critic_model_temperature
                )
            )

            if not response.text or not response.text.strip():
                return None

            # Strip markdown code blocks if present
            clean_text = response.text.strip()
            if clean_text.startswith('```json'):
                clean_text = clean_text[7:]
            if clean_text.endswith('```'):
                clean_text = clean_text[:-3]
            clean_text = clean_text.strip()

            result = json.loads(clean_text)
            elapsed = time.time() - start_time
            logger.info(f"Critic evaluation completed in {elapsed:.3f}s")

            return result

        except Exception as e:
            logger.error(f"Critic evaluation failed: {e}")
            return None

    def _build_evaluation_prompt(self, query: str, context: str, answer: str) -> str:
        return f"""You are an AI critic evaluating the quality and completeness of a RAG system's answer.

Your task: Analyze if the answer adequately addresses the user's query given the available context.

Query: {query}

Available Context:
{context}

Generated Answer:
{answer}

Evaluate the answer and respond with valid JSON only:
{{
  "confidence": <float 0.0-1.0>,
  "missing_info": "<what key information is missing or unclear>",
  "enrichment_suggestions": ["<topic1>", "<topic2>"]
}}

Scoring guidelines:
- confidence 0.9+: Complete, accurate answer
- confidence 0.7-0.9: Good answer with minor gaps
- confidence 0.5-0.7: Partial answer, missing key details
- confidence <0.5: Inadequate or misleading answer

Focus on factual completeness, not writing quality."""

    def is_available(self) -> bool:
        return get_settings().critic_enabled and self._model is not None

critic = CriticHead()