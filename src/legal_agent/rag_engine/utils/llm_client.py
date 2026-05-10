from typing import List, Optional, Any, Dict
from legal_agent.config import get_settings
import logging
import os
import json

logger = logging.getLogger(__name__)

class LlmClient:
    def __init__(self):
        self.provider = get_settings().draft_llm_provider

        if self.provider == "gemini":
            from google import genai
            logger.info("Using Gemini via google-genai")
            self.client = genai.Client(api_key=get_settings().gemini_api_key or "")
            self.model_id = get_settings().gemini_model # e.g. "gemini-2.0-flash"
            self.max_tokens = get_settings().gemini_max_tokens
            self.temperature = get_settings().gemini_temperature

        elif self.provider == "openai":
            from openai import OpenAI
            self.client = OpenAI(api_key=get_settings().openai_api_key or "")
            self.model_id = get_settings().openai_model
            self.max_tokens = get_settings().openai_max_tokens
            self.temperature = get_settings().openai_temperature

    def generate_answer(self, query: str, context_chunks: List[str], force_json: bool = None, answer_style: str = "detailed") -> str:
        if context_chunks is None:
            context_chunks = []

        should_use_json = force_json if force_json is not None else get_settings().enable_json_response

        if should_use_json and self._is_educational_query(query):
            return self._generate_educational_json(query, context_chunks)
        else:
            return self._generate_text_response(query, context_chunks, answer_style)

    def _is_educational_query(self, query: str) -> bool:
        educational_keywords = [
            "mcq", "questions", "quiz", "test", "exam", "assessment",
            "generate", "create", "multiple choice", "true false",
            "practice", "exercise", "homework", "assignment", "worksheet"
        ]
        return any(keyword in query.lower() for keyword in educational_keywords)

    def _generate_text_response(self, query: str, context_chunks: List[str], answer_style: str = "detailed") -> str:
        context = "\n\n".join(context_chunks)

        if not context or not context.strip():
            return "Context not found. I can only answer questions based on the documents that have been indexed in the system."

        prompt = f"""You are an advanced Legal and Academic AI assistant. 
        
### GUIDELINES:
1. **Prioritize Context**: Use the provided context as your primary source of information. 
2. **Be Detailed**: Provide thorough explanations. Avoid one-word or very short answers unless requested.
3. **Legal Specificity**: Cite specific Sections, Articles, or Case laws mentioned in the context.
4. **Formatting**: Use markdown (bolding, lists) for structure.
5. **Missing Info**: If context is unrelated, state clearly: "Note: This information is not found in the indexed documents."

### CONTEXT FROM INDEXED DOCUMENTS:
{context}

### USER QUERY:
{query}

### STYLE: {answer_style}
"""
        return self._call_llm(prompt)

    def _generate_educational_json(self, query: str, context_chunks: List[str]) -> str:
        context = "\n\n".join(context_chunks)
        if not context or not context.strip():
            return json.dumps({"error": "Context not found"})

        prompt = f"""Generate educational content based STRICTLY on the provided context.
        
### CONTEXT:
{context}

### REQUEST:
{query}

### OUTPUT FORMAT:
Return valid JSON as specified:
{{
    "questions": [
        {{
            "question_text": "...",
            "options": ["A", "B", "C", "D"],
            "correct_answer": "...",
            "explanation": "..."
        }}
    ]
}}
"""
        return self._call_llm(prompt, response_mime_type="application/json")

    def _call_llm(self, prompt: str, response_mime_type: str = "text/plain") -> str:
        try:
            if self.provider == "gemini":
                from google.genai import types
                response = self.client.models.generate_content(
                    model=self.model_id,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        max_output_tokens=self.max_tokens,
                        temperature=self.temperature,
                        response_mime_type=response_mime_type
                    )
                )
                return response.text.strip()
            
            elif self.provider == "openai":
                response_format = {"type": "json_object"} if response_mime_type == "application/json" else None
                response = self.client.chat.completions.create(
                    model=self.model_id,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=self.max_tokens,
                    temperature=self.temperature,
                    response_format=response_format
                )
                return response.choices[0].message.content.strip()
                
        except Exception as e:
            logger.error(f"LLM Error: {e}")
            return f"Error: {str(e)}"

    def extract_legal_graph_triplets(self, text: str) -> str:
        prompt = f"Extract logical relationships from this legal text as a JSON graph (nodes, edges):\n\n{text}"
        return self._call_llm(prompt, response_mime_type="application/json")

llm_client = LlmClient()