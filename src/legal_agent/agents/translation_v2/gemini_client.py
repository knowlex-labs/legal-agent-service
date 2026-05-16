"""Thin async wrapper around google.genai for translation_v2.

Matches the SDK pattern already used in legal_agent.utils.ocr: synchronous
genai.Client invoked via asyncio.to_thread, JSON output enforced via
response_mime_type + Pydantic schema validation. One transient-failure retry.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from legal_agent.config import get_settings
from legal_agent.utils.ocr import _strip_code_fence

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


def build_client() -> Any:
    """Construct a google.genai.Client keyed off settings.gemini_api_key."""
    from google import genai

    api_key = get_settings().gemini_api_key or ""
    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY is not configured; translation_v2 requires Gemini access."
        )
    return genai.Client(api_key=api_key)


def _call_sync(
    client: Any,
    model: str,
    contents: list[Any],
    *,
    temperature: float,
    max_output_tokens: int,
    response_mime_type: str | None,
    thinking_budget: int | None,
) -> str:
    from google.genai import types

    cfg_kwargs: dict[str, Any] = {
        "temperature": temperature,
        "max_output_tokens": max_output_tokens,
    }
    if response_mime_type:
        cfg_kwargs["response_mime_type"] = response_mime_type
    # Disable / cap "extended thinking" — vision-OCR doesn't need reasoning and
    # Gemini 2.5 Pro otherwise spends 10–30s on thinking tokens per call.
    if thinking_budget is not None:
        try:
            cfg_kwargs["thinking_config"] = types.ThinkingConfig(thinking_budget=thinking_budget)
        except (AttributeError, TypeError):
            # SDK version doesn't support thinking_config — silently proceed.
            pass
    response = client.models.generate_content(
        model=model,
        contents=contents,
        config=types.GenerateContentConfig(**cfg_kwargs),
    )
    return response.text or ""


async def call_gemini_json(
    client: Any,
    model: str,
    contents: list[Any],
    *,
    schema: type[T],
    temperature: float = 0.1,
    max_output_tokens: int = 32768,
    retries: int = 1,
    context: str = "",
    thinking_budget: int | None = None,
) -> T:
    """Run a JSON-mode Gemini call and validate the response against `schema`.

    Parameters
    ----------
    contents : list of google.genai parts (Part.from_bytes, raw str, etc.).
    schema   : Pydantic model class to validate against.
    retries  : Number of retries after the first attempt (default 1 = 2 total).
    context  : Short tag used in log lines, e.g. "vision page 3".
    thinking_budget : Optional thinking-token cap. Pass 0 on Flash to disable
        extended-thinking entirely; on Pro it's clamped to the minimum allowed.
    """
    last_exc: BaseException | None = None
    for attempt in range(retries + 1):
        try:
            raw = await asyncio.to_thread(
                _call_sync,
                client,
                model,
                contents,
                temperature=temperature,
                max_output_tokens=max_output_tokens,
                response_mime_type="application/json",
                thinking_budget=thinking_budget,
            )
            cleaned = _strip_code_fence(raw)
            if not cleaned.strip():
                raise RuntimeError(f"Gemini returned empty response ({context})")
            data = json.loads(cleaned)
            return schema.model_validate(data)
        except (json.JSONDecodeError, ValidationError, RuntimeError) as exc:
            last_exc = exc
            if attempt < retries:
                logger.warning(
                    "[gemini %s] attempt %d/%d failed (%s); retrying",
                    context,
                    attempt + 1,
                    retries + 1,
                    type(exc).__name__,
                )
                await asyncio.sleep(0.5 * (attempt + 1))
                continue
            break
        except Exception as exc:
            last_exc = exc
            if attempt < retries:
                logger.warning(
                    "[gemini %s] attempt %d/%d API error (%s); retrying",
                    context,
                    attempt + 1,
                    retries + 1,
                    type(exc).__name__,
                )
                await asyncio.sleep(1.0 * (attempt + 1))
                continue
            break
    assert last_exc is not None
    raise last_exc


async def call_gemini_text(
    client: Any,
    model: str,
    contents: list[Any],
    *,
    temperature: float = 0.1,
    max_output_tokens: int = 32768,
    retries: int = 1,
    context: str = "",
    thinking_budget: int | None = None,
) -> str:
    """Plain-text Gemini call with retry. No JSON parsing."""
    last_exc: BaseException | None = None
    for attempt in range(retries + 1):
        try:
            raw = await asyncio.to_thread(
                _call_sync,
                client,
                model,
                contents,
                temperature=temperature,
                max_output_tokens=max_output_tokens,
                response_mime_type=None,
                thinking_budget=thinking_budget,
            )
            return raw
        except Exception as exc:
            last_exc = exc
            if attempt < retries:
                logger.warning(
                    "[gemini %s] text attempt %d/%d failed (%s); retrying",
                    context,
                    attempt + 1,
                    retries + 1,
                    type(exc).__name__,
                )
                await asyncio.sleep(1.0 * (attempt + 1))
                continue
            break
    assert last_exc is not None
    raise last_exc
