"""Shared helpers used by both v3 translators (Haiku + Sarvam).

Lives in v3 rather than v2 to keep v2 untouched. Imports from v2 schemas to
guarantee a single source of truth for Block/VisionPage shape.
"""

from __future__ import annotations

import re

from legal_agent.agents.translation_v2.keep_english import mask_english_tokens
from legal_agent.agents.translation_v2.schemas import VisionPage

_TRANSLATABLE_PLACEHOLDER_RE = re.compile(
    r"^\s*\[(STAMP|SEAL|LOGO|SIGNATURE|IMAGE)\b", re.IGNORECASE
)


def needs_translation(text: str) -> bool:
    """Whether a block's source text is worth sending through the translator.

    Returns False for empty / pure-numeric / placeholder strings — those pass
    through verbatim into text_hi. Requires at least 2 alphabetic characters
    so single-letter tokens and pure numbers don't burn translator calls.
    """
    stripped = text.strip()
    if not stripped:
        return False
    if _TRANSLATABLE_PLACEHOLDER_RE.match(stripped):
        return False
    alpha = sum(1 for c in stripped if c.isalpha())
    return alpha >= 2


def build_blocks_payload(
    page: VisionPage,
) -> tuple[list[dict[str, str]], dict[str, dict[str, str]]]:
    """Build per-block payload with English-token masking applied.

    Returns (payload, keep_maps_by_id). Non-translatable blocks pass through
    unmasked so the translator sees the literal text and can copy it back.
    """
    payload: list[dict[str, str]] = []
    keep_maps: dict[str, dict[str, str]] = {}
    for b in page.blocks:
        if not needs_translation(b.text_en):
            payload.append({"id": b.id, "role": b.role.value, "text_en": b.text_en})
            continue
        masked, km = mask_english_tokens(b.text_en)
        if km:
            keep_maps[b.id] = km
        payload.append({"id": b.id, "role": b.role.value, "text_en": masked})
    return payload, keep_maps


def render_glossary_table(glossary: dict[str, str]) -> str:
    """Markdown table of {EN → HI} for inclusion in a translator prompt.

    Always renders the FULL document glossary (not a page-filtered subset)
    so the cached prefix is byte-identical across pages — enables prompt
    caching on both Anthropic and Gemini.
    """
    if not glossary:
        return "(glossary is empty)"
    lines = ["| English | Hindi |", "|---|---|"]
    for en, hi in glossary.items():
        lines.append(f"| {en} | {hi} |")
    return "\n".join(lines)


def pick_style_anchors(all_pages: list[VisionPage], max_anchors: int = 2) -> str:
    """Pick 1-2 representative paragraph blocks from page 1 as register anchors.

    Used to bias the translator toward the document's existing tone instead of
    falling into generic formal Hindi.
    """
    if not all_pages:
        return "(no anchors available)"
    p1 = all_pages[0]
    paragraph_blocks = [
        b
        for b in p1.blocks
        if b.role.value in ("paragraph", "clause") and len(b.text_en) > 30
    ]
    chosen = paragraph_blocks[:max_anchors] if paragraph_blocks else p1.blocks[:max_anchors]
    return "\n\n".join(f"- {b.text_en}" for b in chosen) or "(no anchors available)"
