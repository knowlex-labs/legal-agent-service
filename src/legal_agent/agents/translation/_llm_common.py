"""Shared helpers for translation pipeline LLM callers.

Used by document_glossary (Stage A), reviewer + style_smoother (Stage C),
and translator (Stage B LLM backend). Centralises provider inference,
JSON-fence stripping, prompt formatting, and content-block flattening so
the four call sites can't drift.
"""

from __future__ import annotations

import re


def infer_provider(model: str) -> str:
    """Map a model-name prefix to a LangChain provider id."""
    m = model.lower().removeprefix("models/")
    if m.startswith("gemini"):
        return "google-genai"
    if m.startswith("claude"):
        return "anthropic"
    if m.startswith("gpt") or m.startswith("o"):
        return "openai"
    raise ValueError(f"Unsupported model: {model!r}")


def strip_fences(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
        if raw.endswith("```"):
            raw = raw[:-3]
    return raw.strip()


def extract_json_blob(raw: str, opener: str) -> str:
    """Return the first balanced JSON-shaped substring opening with `opener`.

    Tolerates models that prepend a stray sentence before the JSON payload.
    """
    cleaned = strip_fences(raw)
    if cleaned.startswith(opener):
        return cleaned
    closer = "]" if opener == "[" else "}"
    match = re.search(re.escape(opener) + r".*" + re.escape(closer), cleaned, re.DOTALL)
    return match.group(0) if match else cleaned


def format_numbered(items: list[str]) -> str:
    return "\n".join(f"[{i}] {t}" for i, t in enumerate(items))


def format_glossary_lines(
    glossary: dict[str, str] | None,
    *,
    targets_only: bool = False,
) -> str:
    """Format glossary as one bullet per line.

    Default `src → tgt` for callers that need to assert the binding (reviewer,
    translator). `targets_only=True` lists just the target forms — the style
    smoother uses this because it only needs to know which surface strings
    not to rewrite, not the source mapping.
    """
    if not glossary:
        return "(none)"
    if targets_only:
        return "\n".join(f"- {tgt}" for tgt in glossary.values())
    return "\n".join(f"- {src} → {tgt}" for src, tgt in glossary.items())


def message_content_to_text(content: object) -> str:
    """Flatten a LangChain response.content (str or list of content blocks)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            p.get("text", "") if isinstance(p, dict) else str(p) for p in content
        )
    return str(content)
