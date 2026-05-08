"""Load and merge legal-translation glossaries.

`base.json` (under `data/glossaries/`) is the canonical English → target-language
mapping. It used to live as `_LEGAL_TERMS` in `generator.py`; relocating to JSON
makes it pluggable so doc-type profiles and (eventually) per-user overlays can
merge on top without touching code.

Loaded once at module import. Read-only by design — overlays produce a fresh
dict via `merge_overlay`.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


_BASE_PATH = (
    Path(__file__).resolve().parents[2] / "data" / "glossaries" / "base.json"
)


def _load_base() -> dict[str, dict[str, str]]:
    if not _BASE_PATH.exists():
        logger.warning(f"Base glossary not found at {_BASE_PATH}, using empty mapping")
        return {}
    try:
        return json.loads(_BASE_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.error(f"Failed to parse base glossary at {_BASE_PATH}: {exc}")
        return {}


_BASE_GLOSSARY: dict[str, dict[str, str]] = _load_base()


def get_glossary(target_language: str) -> dict[str, str]:
    """Return the base glossary for one target language ({} if not present)."""
    return _BASE_GLOSSARY.get(target_language, {})


def merge_overlay(
    base: dict[str, str],
    overlay: dict[str, str] | None,
) -> dict[str, str]:
    """Return a new dict where overlay entries win over base entries."""
    if not overlay:
        return dict(base)
    merged = dict(base)
    merged.update(overlay)
    return merged
