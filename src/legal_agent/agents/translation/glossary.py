"""Protected-term glossary and pre/post translation utilities.

Wraps Sarvam translate calls so technical terms (product names, languages,
frameworks) are never sent to the translation engine. Each protected occurrence
is replaced with a sentinel [__NNNN__], translated as-is, then restored to its
target form afterwards. Sentinels use only digits, brackets, and underscores —
no Latin letters that Sarvam would transliterate.

`DocState` tracks first-mention so brand names render as dual-form
("रागा एआई (Raga AI)") on first occurrence and Latin-only thereafter.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

DEFAULT_GLOSSARY_PATH = Path(__file__).parent / "glossary_en_hi.yaml"

# Sentinels use plain brackets + underscores + digits only — no Latin letters that
# Sarvam could transliterate, no Unicode math brackets that Sarvam normalises to `[`.
# Pattern [__NNNN__] is distinct from real text (legal citations use [2024], not [__2024__]).
_SENTINEL_RE = re.compile(r"\[__\d{4}__\]")
_SENTINEL_FUZZY_RE = re.compile(r"\[__(\d+)__\]")

_METRIC_RE = re.compile(
    r"(?:"
    r"\$[\d,.]+[KMBkm+%]?\+?"        # $40K+, $1.2M, $500
    r"|[\d,.]+[KMBkm]\+?(?=\s|$)"    # 40K+, 1.2M (bare)
    r"|\b\d+(?:\.\d+)?%\b"           # 42%, 99.9%
    r")"
)

# Private-use-area glyphs (Font Awesome icons in extracted PDFs). Drop before
# translation so the engine doesn't hallucinate around garbage codepoints.
# Covers BMP PUA (U+E000–U+F8FF) and Supplementary PUA-A/B.
_PUA_RE = re.compile(
    "["
    "-"
    "\U000f0000-\U000ffffd"
    "\U00100000-\U0010fffd"
    "]"
)

_MILLION_RE = re.compile(r"\b(\d+(?:\.\d+)?)\s*million\b", re.IGNORECASE)
_BILLION_RE = re.compile(r"\b(\d+(?:\.\d+)?)\s*billion\b", re.IGNORECASE)


@dataclass
class GlossaryEntry:
    term: str
    hi: str
    first_mention: str | None = None


class Glossary:
    """Loaded en→hi glossary with longest-first matching."""

    def __init__(self, entries: list[GlossaryEntry]):
        self._entries = entries
        # Longest-first so "Spring Boot" matches before "Spring", "CI/CD" before "API".
        self._terms_sorted = sorted({e.term for e in entries}, key=len, reverse=True)
        self._by_term = {e.term: e for e in entries}

    @classmethod
    def load(cls, path: Path | str | None = None) -> "Glossary":
        path = Path(path) if path else DEFAULT_GLOSSARY_PATH
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        entries = [
            GlossaryEntry(term=k, hi=v["hi"], first_mention=v.get("first_mention"))
            for k, v in data.items()
        ]
        return cls(entries)

    def terms(self) -> list[str]:
        return list(self._terms_sorted)

    def target(self, term: str, *, first_mention: bool) -> str:
        entry = self._by_term[term]
        if first_mention and entry.first_mention:
            return entry.first_mention
        return entry.hi


@dataclass
class DocState:
    """Tracks which protected terms have already appeared in this document.

    Threaded through every freeze() call across all paragraphs of one document
    so first-mention dual-form fires exactly once per term.
    """
    _seen: set[str] = field(default_factory=set)

    def seen(self, term: str) -> bool:
        return term in self._seen

    def mark(self, term: str) -> None:
        self._seen.add(term)


def _escape_term(term: str) -> str:
    """Word-boundary regex for a term. Uses lookarounds because some terms end
    in non-word chars (`C#`, `C++`, `Node.js`, `CI/CD`) where `\\b` fails."""
    return rf"(?<!\w){re.escape(term)}(?!\w)"


def freeze(
    text: str,
    state: DocState,
    glossary: Glossary,
) -> tuple[str, dict[str, str]]:
    """Replace protected terms with sentinels. Returns (frozen_text, sentinels).

    Iterates terms longest-first so multi-word terms are protected before any
    sub-word match steals them.
    """
    sentinels: dict[str, str] = {}

    # Freeze currency/metric patterns verbatim before glossary terms so Sarvam never sees them.
    # Process right-to-left so string positions stay valid after each substitution.
    for match in reversed(list(_METRIC_RE.finditer(text))):
        sid = f"[__{len(sentinels):04d}__]"
        sentinels[sid] = match.group(0)
        text = text[: match.start()] + sid + text[match.end() :]

    for term in glossary.terms():
        pattern = re.compile(_escape_term(term))

        def repl(_match: re.Match, _term: str = term) -> str:
            sid = f"[__{len(sentinels):04d}__]"
            target = glossary.target(_term, first_mention=not state.seen(_term))
            state.mark(_term)
            sentinels[sid] = target
            return sid

        text = pattern.sub(repl, text)
    return text, sentinels


def restore(translated: str, sentinels: dict[str, str]) -> str:
    """Replace sentinels with their glossary targets. NFC-normalises the result.

    Uses fuzzy digit matching so Sarvam's occasional digit-padding (e.g. [__0003__] →
    [__00003__]) is still resolved. Unresolvable sentinels are stripped with a warning.
    """
    by_idx: dict[int, str] = {}
    for sid, target in sentinels.items():
        m = _SENTINEL_FUZZY_RE.fullmatch(sid)
        if m:
            by_idx[int(m.group(1))] = target

    def _repl(m: re.Match) -> str:
        return by_idx.get(int(m.group(1)), m.group(0))

    translated = _SENTINEL_FUZZY_RE.sub(_repl, translated)

    leftover = _SENTINEL_FUZZY_RE.findall(translated)
    if leftover:
        logger.warning("Stripping %d unresolved sentinels: %s", len(leftover), leftover)
        translated = _SENTINEL_FUZZY_RE.sub("", translated)

    return unicodedata.normalize("NFC", translated)


def localize_units(text: str) -> str:
    """`N million` → `N×10 लाख`, `N billion` → `N×100 करोड़`.

    Order matters: billion first, then million, so we don't double-convert.
    """
    def m_to_lakh(m: re.Match) -> str:
        v = float(m.group(1)) * 10
        return f"{int(v)} लाख" if v == int(v) else f"{v:g} लाख"

    def b_to_crore(m: re.Match) -> str:
        v = float(m.group(1)) * 100
        return f"{int(v)} करोड़" if v == int(v) else f"{v:g} करोड़"

    text = _BILLION_RE.sub(b_to_crore, text)
    text = _MILLION_RE.sub(m_to_lakh, text)
    return text


def strip_pua(text: str) -> str:
    """Remove Private-Use-Area glyphs (Font Awesome icons etc.) before translation."""
    return _PUA_RE.sub("", text)
