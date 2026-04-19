"""Generate a short auto-title for a chat session from the user's first message."""

import logging

from langchain.chat_models import init_chat_model
from langchain_core.messages import HumanMessage

logger = logging.getLogger(__name__)

_MAX_INPUT_CHARS = 2000
_MAX_TITLE_CHARS = 80

_PROMPT_TEMPLATE = (
    "Write a concise 4-6 word title in Title Case for a chat session whose first "
    "user message is below. Output ONLY the title — no quotes, no trailing "
    "punctuation, no preamble.\n\n"
    "User's first message:\n{message}"
)


async def generate_session_title(
    first_message: str,
    model: str | None = None,
) -> str | None:
    """Generate a short auto-title from a chat session's first user message.

    Returns None on any failure so callers can skip emitting the SSE event
    without breaking the main response stream.

    The `model` argument should be the chat session's own model so the title
    call reuses whatever the caller already has working. Falls back to a
    reasonable default only when not supplied.
    """
    if not first_message or not first_message.strip():
        return None
    model_name = model or "gemini-3.1-flash-lite-preview"
    provider = "google-genai" if model_name.startswith("gemini") else "openai"
    prompt = _PROMPT_TEMPLATE.format(message=first_message[:_MAX_INPUT_CHARS])
    try:
        # Intentionally omit max_tokens / temperature — google-genai is
        # sensitive to low max_tokens (may return empty content) and some
        # model variants disallow unfamiliar kwargs. Defaults keep it simple.
        llm = init_chat_model(model_name, model_provider=provider)
        response = await llm.ainvoke([HumanMessage(content=prompt)])
        raw = response.content
        if isinstance(raw, str):
            content = raw
        elif isinstance(raw, list):
            content = "".join(
                p.get("text", "") if isinstance(p, dict) else str(p) for p in raw
            )
        else:
            content = str(raw)
        title = content.strip().strip('"\'').rstrip(".").strip()
        if not title:
            logger.warning(
                "generate_session_title: empty content from model=%s", model_name
            )
            return None
        return title[:_MAX_TITLE_CHARS]
    except Exception as exc:
        # Log error type + message (the traceback alone can hide the actual
        # API rejection reason when wrapped in tenacity retries).
        logger.warning(
            "generate_session_title failed (model=%s): %s: %s",
            model_name, exc.__class__.__name__, exc,
        )
        return None
