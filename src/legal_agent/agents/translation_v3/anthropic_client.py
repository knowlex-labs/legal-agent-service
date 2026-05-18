"""Thin Anthropic client wrapper for v3 — structured JSON via tool_use.

Centralised so glossary + translate stages share retry + timeout behaviour
without reaching into the raw SDK.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from legal_agent.config import get_settings

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

_client: Any | None = None
_client_lock = asyncio.Lock()


async def _get_client():
    global _client
    if _client is not None:
        return _client
    async with _client_lock:
        if _client is not None:
            return _client
        from anthropic import AsyncAnthropic

        settings = get_settings()
        api_key = getattr(settings, "anthropic_api_key", None) or None
        _client = AsyncAnthropic(api_key=api_key) if api_key else AsyncAnthropic()
        return _client


def _build_tool(schema_model: type[BaseModel], tool_name: str) -> dict[str, Any]:
    """Anthropic tool_use forces the model to fill a JSON schema."""
    return {
        "name": tool_name,
        "description": "Return the requested structured data.",
        "input_schema": schema_model.model_json_schema(),
    }


async def call_anthropic_json(
    model: str,
    schema: type[T],
    *,
    prompt: str | None = None,
    messages: list[dict[str, Any]] | None = None,
    tool_name: str = "submit",
    max_tokens: int = 8192,
    temperature: float = 0.1,
    retries: int = 1,
    context: str = "",
    system: str | list[dict[str, Any]] | None = None,
) -> T:
    """Call Anthropic with tool_use forcing the JSON shape.

    Pass either a single `prompt` (becomes one user message) or a full
    `messages` list (e.g. when using prompt-caching blocks). `system` may be
    a string or a list of content blocks (use the list form to attach
    cache_control to the system prefix).
    """
    if not messages:
        if prompt is None:
            raise ValueError("call_anthropic_json: provide either prompt or messages")
        messages = [{"role": "user", "content": prompt}]

    client = await _get_client()
    tool = _build_tool(schema, tool_name)

    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            kwargs: dict[str, Any] = {
                "model": model,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "tools": [tool],
                "tool_choice": {"type": "tool", "name": tool_name},
                "messages": messages,
            }
            if system is not None:
                kwargs["system"] = system
            resp = await client.messages.create(**kwargs)
            for block in resp.content:
                if getattr(block, "type", None) == "tool_use" and getattr(block, "name", None) == tool_name:
                    try:
                        return schema.model_validate(block.input)
                    except ValidationError as ve:
                        last_exc = ve
                        logger.warning(
                            "anthropic %s: schema validation failed (%s); attempt %d/%d",
                            context or tool_name,
                            ve,
                            attempt + 1,
                            retries + 1,
                        )
                        break
            else:
                last_exc = RuntimeError(
                    f"anthropic {context or tool_name}: model returned no tool_use block"
                )
        except Exception as exc:  # noqa: BLE001 — network/rate errors retried
            last_exc = exc
            logger.warning(
                "anthropic %s: call failed (%s: %s); attempt %d/%d",
                context or tool_name,
                type(exc).__name__,
                exc,
                attempt + 1,
                retries + 1,
            )
        if attempt < retries:
            await asyncio.sleep(0.5 * (2**attempt))

    assert last_exc is not None
    raise last_exc
