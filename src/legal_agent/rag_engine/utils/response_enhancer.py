import json
import logging
import re
from typing import Dict, Any, List

logger = logging.getLogger(__name__)


def enhance_response_if_needed(response: str, original_query: str) -> str:
    """
    Enhance response if it's educational JSON content.
    """
    if _is_json_response(response) and _is_educational_query(original_query):
        return _enhance_educational_json(response, original_query)
    return response


def _is_json_response(response: str) -> bool:
    """Check if response is valid JSON."""
    try:
        json.loads(response)
        return True
    except (json.JSONDecodeError, TypeError):
        return False


def _is_educational_query(query: str) -> bool:
    """Check if query is educational in nature."""
    educational_keywords = [
        "mcq", "questions", "quiz", "test", "exam", "assessment",
        "generate", "create questions", "multiple choice", "true false"
    ]
    return any(keyword in query.lower() for keyword in educational_keywords)


def _enhance_educational_json(response: str, original_query: str) -> str:
    """
    Transform LLM minimal JSON to structured educational format.
    """
    try:
        llm_data = json.loads(response)

        if "questions" not in llm_data:
            return response

        enhanced_data = {
            "content_text": _extract_content_summary(original_query),
            "summary": _generate_summary(llm_data, original_query),
            "questions": [],
            "metadata": _generate_metadata(llm_data, original_query)
        }

        for i, question in enumerate(llm_data["questions"]):
            enhanced_question = {
                "question_id": f"q{i+1}",
                "question_config": {
                    "question_text": question.get("question_text", ""),
                    "type": _determine_question_type(question, original_query),
                    "requires_diagram": question.get("requires_diagram", False),
                    "contains_math": question.get("contains_math", False),
                    "diagram_type": question.get("diagram_type"),
                    "complexity_level": "intermediate"
                },
                "answer_config": {
                    "options": question.get("options", []),
                    "correct_answer": question.get("correct_answer", ""),
                    "reason": question.get("explanation", ""),
                    "max_score": 1
                },
                "context": "Generated from physics content",
                "metadata": {
                    "auto_generated_id": True,
                    "question_number": i + 1,
                    "estimated_time": 30
                }
            }
            enhanced_data["questions"].append(enhanced_question)

        return json.dumps(enhanced_data, indent=2, ensure_ascii=False)

    except Exception as e:
        logger.error(f"Failed to enhance educational JSON: {e}")
        return response


def _extract_content_summary(query: str) -> str:
    """Generate content summary from query."""
    return f"Educational content generated for: {query}"


def _generate_summary(llm_data: Dict[str, Any], query: str) -> str:
    """Generate comprehensive summary."""
    question_count = len(llm_data.get("questions", []))
    return f"This content provides {question_count} educational questions based on physics concepts, designed to test understanding and application skills."


def _determine_question_type(question: Dict[str, Any], query: str) -> str:
    """Determine question type from structure and query."""
    if "options" in question and len(question.get("options", [])) > 2:
        return "multiple_choice"
    elif len(question.get("options", [])) == 2:
        return "true_false"
    else:
        return "short_answer"


def _generate_metadata(llm_data: Dict[str, Any], query: str) -> Dict[str, Any]:
    """Generate metadata for the enhanced response."""
    question_count = len(llm_data.get("questions", []))

    # Extract difficulty from query
    difficulty = "intermediate"
    if "basic" in query.lower() or "easy" in query.lower():
        difficulty = "basic"
    elif "advanced" in query.lower() or "difficult" in query.lower():
        difficulty = "advanced"

    # Extract question types
    question_types = {}
    for question in llm_data.get("questions", []):
        q_type = _determine_question_type(question, query)
        question_types[q_type] = question_types.get(q_type, 0) + 1

    return {
        "analysis": {
            "main_topics": ["Physics", "Newton's Laws", "Mechanics"],
            "content_type": "educational",
            "complexity_level": difficulty,
            "estimated_reading_time": question_count * 2,
            "target_audience": "Physics students preparing for examinations"
        },
        "question_generation": {
            "total_questions": question_count,
            "question_types": question_types,
            "user_request": query,
            "difficulty_level": difficulty
        }
    }