"""Mask byte-perfect English tokens before translation, restore after.

Names are explicitly OUT OF SCOPE — they're handled via the glossary
transliteration path. The patterns here protect content that must stay
byte-identical in the output:

  - emails           (alex@example.com)
  - URLs             (https://hc.mp.nic.in)
  - long numeric IDs (≥4 digits — years like 2026, postal codes)
  - statute / annexure codes with dots or slashes (M.Cr.C., P/1, S.482)

Numbers under 4 digits (clause "482", page "9") are NOT masked — the translate
prompt rule already says "preserve numerals", and over-masking creates more
brittle round-trips than it solves.
"""

from __future__ import annotations

import re

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w.-]+\.\w+")
_URL_RE = re.compile(r"https?://\S+")
_LONG_DIGITS_RE = re.compile(r"\b\d{4,}\b")
# Capitalised tokens that contain a "." or "/" — covers "M.Cr.C.", "P/1",
# "S.482", "Cr.P.C.", "P.S.", etc. Bounded length so we don't grab paragraphs.
_STATUTE_CODE_RE = re.compile(r"\b[A-Z][A-Za-z]{0,7}[./][A-Z0-9./]{1,15}\b")

_SENTINEL_TEMPLATE = "__KEEP_{}__"
_SENTINEL_RE = re.compile(r"__KEEP_\d+__")


def mask_english_tokens(text: str) -> tuple[str, dict[str, str]]:
    """Replace email / URL / long-number / statute-code spans with sentinels.

    Returns (masked_text, mapping). `mapping` keys are the sentinel strings
    (`__KEEP_0__`, …); values are the original substrings. Order-preserving.
    Idempotent: running twice yields the same mapping plus a no-op pass.
    """
    if not text:
        return text, {}
    mapping: dict[str, str] = {}
    counter = [0]

    def stash(match: re.Match[str]) -> str:
        original = match.group(0)
        # Reuse a sentinel if we've already stashed this exact substring.
        for sentinel, stored in mapping.items():
            if stored == original:
                return sentinel
        sentinel = _SENTINEL_TEMPLATE.format(counter[0])
        counter[0] += 1
        mapping[sentinel] = original
        return sentinel

    # Order matters: URLs before emails (URLs can contain @), emails before
    # statute codes (foo.bar@x.y looks like a statute code), long digits last
    # (years inside URLs shouldn't be double-masked).
    masked = _URL_RE.sub(stash, text)
    masked = _EMAIL_RE.sub(stash, masked)
    masked = _STATUTE_CODE_RE.sub(stash, masked)
    masked = _LONG_DIGITS_RE.sub(stash, masked)
    return masked, mapping


def restore_english_tokens(text: str, mapping: dict[str, str]) -> str:
    """Replace every __KEEP_N__ sentinel in text with its original substring.

    Sentinels not present in `mapping` are left as-is so the sanitizer can flag
    them. Substitution is whole-token; partial matches inside other words are
    not affected because sentinels start with `__KEEP_` which is unique.
    """
    if not mapping or not text:
        return text

    def restore(match: re.Match[str]) -> str:
        return mapping.get(match.group(0), match.group(0))

    return _SENTINEL_RE.sub(restore, text)


def has_leftover_sentinels(text: str) -> bool:
    """True iff any __KEEP_N__ sentinel remains in the text."""
    return _SENTINEL_RE.search(text) is not None
