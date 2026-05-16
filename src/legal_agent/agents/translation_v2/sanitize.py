"""Post-process validator for translated block text.

Catches and where safe repairs engine artifacts:

  - **Concat tokens** — a Latin letter immediately adjacent to a Devanagari
    letter ("criminधारा"). Inserted space. Always safe.
  - **Fallback markers** — strings the LLM emits when it "gives up"
    ("(अनुवाद नहीं किया गया)", "[NOT TRANSLATED]", etc.). Reported; caller
    falls back to source text.
  - **Leftover sentinels** — `__KEEP_N__` that survived restoration. Restored
    from keep_map if present; else reported.
  - **Empty translations** — non-empty source but blank Hindi output.

The sanitizer never touches Devanagari content or "fixes" translation
choices. Only artifacts and formatting glitches.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from legal_agent.agents.translation_v2.keep_english import (
    has_leftover_sentinels,
    restore_english_tokens,
)

SanityKind = Literal[
    "leftover_sentinel",
    "concat_token",
    "fallback_marker",
    "empty_translation",
]


@dataclass(frozen=True)
class SanityIssue:
    block_id: str
    kind: SanityKind
    detail: str


# Devanagari range: U+0900 – U+097F.
_CONCAT_RE = re.compile(r"([A-Za-z])([ऀ-ॿ])|([ऀ-ॿ])([A-Za-z])")

_FALLBACK_MARKERS: tuple[str, ...] = (
    "(अनुवाद नहीं किया गया)",
    "अनुवाद नहीं किया गया",
    "[NOT TRANSLATED]",
    "(not translated)",
    "[translation not available]",
    "(translation not available)",
    "[UNTRANSLATABLE]",
    "___",
)


def _detect_concat_tokens(text: str) -> tuple[str, int]:
    """Insert a space at every Latin↔Devanagari boundary. Returns (repaired, count)."""
    count = 0

    def repair(m: re.Match[str]) -> str:
        nonlocal count
        count += 1
        # Pick whichever capture group matched. Group 1+2 = Latin then Devanagari;
        # group 3+4 = Devanagari then Latin.
        if m.group(1) is not None:
            return f"{m.group(1)} {m.group(2)}"
        return f"{m.group(3)} {m.group(4)}"

    repaired = _CONCAT_RE.sub(repair, text)
    return repaired, count


def _detect_fallback_marker(text: str) -> str | None:
    """Return the first fallback marker found in `text`, or None."""
    lowered = text.lower()
    for marker in _FALLBACK_MARKERS:
        if marker.lower() in lowered:
            return marker
    return None


def sanitize_translation(
    block_id: str,
    text_en: str,
    text_hi: str,
    keep_map: dict[str, str],
) -> tuple[str, list[SanityIssue]]:
    """Validate and minimally repair a translated block.

    Returns (repaired_text_hi, issues). Always-safe repairs are applied in
    place. Unrepairable issues are reported only; the caller decides whether
    to fall back to source.
    """
    issues: list[SanityIssue] = []
    out = text_hi

    # 1. Restore any leftover sentinels using keep_map; report unresolved ones.
    if has_leftover_sentinels(out):
        out = restore_english_tokens(out, keep_map)
        if has_leftover_sentinels(out):
            issues.append(
                SanityIssue(
                    block_id=block_id,
                    kind="leftover_sentinel",
                    detail="Sentinel(s) not in keep_map remained after restore",
                )
            )

    # 2. Repair Latin↔Devanagari concat tokens by inserting a space.
    out, concat_count = _detect_concat_tokens(out)
    if concat_count:
        issues.append(
            SanityIssue(
                block_id=block_id,
                kind="concat_token",
                detail=f"Inserted space at {concat_count} script boundary token(s)",
            )
        )

    # 3. Detect fallback markers — caller is expected to fall back to text_en.
    marker = _detect_fallback_marker(out)
    if marker is not None:
        issues.append(
            SanityIssue(
                block_id=block_id,
                kind="fallback_marker",
                detail=f"LLM emitted fallback marker: {marker!r}",
            )
        )

    # 4. Empty translation when source was non-empty.
    if text_en.strip() and not out.strip():
        issues.append(
            SanityIssue(
                block_id=block_id,
                kind="empty_translation",
                detail="Non-empty source produced empty translation",
            )
        )

    return out, issues
