"""Pre-translation deterministic source-text cleanup.

Fixes extraction artefacts (merged words, OCR residue, mid-word splits,
broken line joins, duplicate fragments, NFC / smart-quote noise) before the
translator sees them. Pure functions; emits a `CleanupReport` so each pass's
contribution surfaces in `pipeline_metrics`. The unambiguous Devanagari
mid-word split rule (second token starts with a dependent vowel sign) is the
only Hindi-aware pass — syllable-boundary splits like "झू ठी" are left for
the style smoother to fix since they aren't deterministically detectable.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

# Invisible / formatting characters that bloat token counts and break regexes
# downstream — soft hyphen, zero-width spaces, joiners, BOM.
_INVISIBLE_RE = re.compile(r"[­​‌‍﻿⁠]")

# C0 / C1 controls except tab + newline.
_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")

# Smart quote / dash normalization. Em-dash kept verbatim; en-dash collapsed
# to plain hyphen because Sarvam often confuses the two.
_SMART_QUOTES = {
    "‘": "'",
    "’": "'",
    "‚": "'",
    "“": '"',
    "”": '"',
    "„": '"',
    "«": '"',
    "»": '"',
    "–": "-",   # en-dash → hyphen
    # em-dash (U+2014) intentionally not normalized
}
_SMART_QUOTE_RE = re.compile("|".join(map(re.escape, _SMART_QUOTES)))

# Form-field dotted underlines, signature bars, ASCII-art separators —
# Sarvam rejects these with "excessively repeated characters". Cap at 5.
_REPEAT_RUN_RE = re.compile(r"([^\w\s])\1{5,}")

# End-of-line hyphenation: "exam-\nple" → "example" when the next-line head is
# lowercase. We do NOT join across uppercase boundaries to preserve proper
# nouns that legitimately wrap.
_DEHYPHEN_RE = re.compile(r"(\w+)-\n([a-z])")

# Two-or-more spaces → one. Tabs → space. Trailing whitespace stripped per line.
_MULTI_SPACE_RE = re.compile(r"[ \t]{2,}")

# Devanagari dependent-vowel signs / nukta / anusvara / visarga / virama —
# none can begin a word. When a token starts with one, the space before it is
# an extraction artefact. The regex anchors the class at position 0 (no
# leading `[ऀ-ॿ]*`); without that, the pattern would glue together any pair
# of Devanagari tokens where the second happens to contain a dependent vowel
# anywhere, which is most Hindi words.
DEVA_DEPENDENT_OPENER = "़ािीुूृॄॅॆेैॉॊोौ्ँंः"
DEVA_MIDWORD_SPLIT_RE = re.compile(
    rf"([ऀ-ॿ]+) ([{DEVA_DEPENDENT_OPENER}][ऀ-ॿ]*)"
)

# Line-wrap detection: line that doesn't end with a sentence-terminator,
# followed by a line that doesn't start with a list marker / capital /
# page-number / heading-like uppercase run.
_TERMINATORS = ".!?।:;…"  # also include ellipsis
_BULLET_PREFIXES = ("•", "◦", "○", "–", "—", "-", "*", "·", "▪", "▫", "■", "□")


@dataclass
class CleanupReport:
    """Per-pass edit counters surfaced to pipeline_metrics."""
    nfc_normalized: int = 0
    invisible_stripped: int = 0
    controls_stripped: int = 0
    smart_quotes_normalized: int = 0
    repeat_runs_capped: int = 0
    dehyphenations: int = 0
    line_rejoins: int = 0
    midword_splits_repaired: int = 0
    adjacent_dupes_collapsed: int = 0
    whitespace_collapses: int = 0

    def as_dict(self) -> dict[str, int]:
        return {k: v for k, v in self.__dict__.items() if v}

    def merge(self, other: "CleanupReport") -> None:
        for k, v in other.__dict__.items():
            setattr(self, k, getattr(self, k) + v)


def _strip_invisible(text: str, report: CleanupReport) -> str:
    new = _INVISIBLE_RE.sub("", text)
    if new != text:
        report.invisible_stripped += len(text) - len(new)
    return new


def _strip_controls(text: str, report: CleanupReport) -> str:
    new = _CTRL_RE.sub("", text)
    if new != text:
        report.controls_stripped += len(text) - len(new)
    return new


def _normalize_smart_quotes(text: str, report: CleanupReport) -> str:
    def _repl(m: re.Match) -> str:
        report.smart_quotes_normalized += 1
        return _SMART_QUOTES[m.group(0)]
    return _SMART_QUOTE_RE.sub(_repl, text)


def _cap_repeat_runs(text: str, report: CleanupReport) -> str:
    def _repl(m: re.Match) -> str:
        report.repeat_runs_capped += 1
        return m.group(1) * 5
    return _REPEAT_RUN_RE.sub(_repl, text)


def _dehyphenate(text: str, report: CleanupReport) -> str:
    def _repl(m: re.Match) -> str:
        report.dehyphenations += 1
        return m.group(1) + m.group(2)
    return _DEHYPHEN_RE.sub(_repl, text)


def _rejoin_wrapped_lines(text: str, report: CleanupReport) -> str:
    """Rejoin lines inside a block that are visually one paragraph.

    Heuristic: line A ends with a content character (not a terminator,
    not a colon/semicolon), and line B does not start with a bullet, a
    capital ASCII letter, a digit (page numbers, numbered headings), or
    a Devanagari sentence-leading construction. Join with a space.
    """
    lines = text.split("\n")
    if len(lines) < 2:
        return text
    out: list[str] = [lines[0]]
    for nxt in lines[1:]:
        prev = out[-1]
        prev_stripped = prev.rstrip()
        nxt_stripped = nxt.lstrip()
        if not prev_stripped or not nxt_stripped:
            out.append(nxt)
            continue
        last_char = prev_stripped[-1]
        first_char = nxt_stripped[0]
        if last_char in _TERMINATORS:
            out.append(nxt)
            continue
        # Bullet / list marker / numbered-list start → keep break.
        if any(nxt_stripped.startswith(p) for p in _BULLET_PREFIXES):
            out.append(nxt)
            continue
        if re.match(r"^\d+[.)]\s", nxt_stripped):
            out.append(nxt)
            continue
        # ALL-CAPS heading-like line (>= 3 chars of consecutive uppercase) → keep break.
        if re.match(r"^[A-Z]{3,}", nxt_stripped):
            out.append(nxt)
            continue
        # New sentence start signalled by capital letter after a comma is
        # rare; only treat as a new line when prev ended with a letter and
        # next starts with a capital plus a space and >= 4 chars (likely a
        # title-cased fragment like "Section 5"). Otherwise rejoin.
        if first_char.isupper() and not (last_char.isalnum() or last_char in ",-—"):
            out.append(nxt)
            continue
        # Otherwise: fuse.
        sep = "" if prev.endswith(" ") or nxt.startswith(" ") else " "
        out[-1] = prev_stripped + sep + nxt_stripped
        report.line_rejoins += 1
    return "\n".join(out)


def _repair_devanagari_midword_splits(text: str, report: CleanupReport) -> str:
    """Merge `<deva> <deva-starting-with-dependent>` — these can never be two words.

    Applied iteratively because one merge can expose another (rare but possible
    when an extraction split a word into three pieces).
    """
    prev = None
    while prev != text:
        prev = text
        text, n = DEVA_MIDWORD_SPLIT_RE.subn(lambda m: m.group(1) + m.group(2), text)
        report.midword_splits_repaired += n
    return text


def _collapse_adjacent_duplicates(
    text: str,
    report: CleanupReport,
    *,
    glossary_sources: frozenset[str] | None = None,
) -> str:
    """Collapse adjacent identical 2-6 word sequences.

    Glossary source forms are exempt — party names like "State of Maharashtra"
    legitimately repeat. Operates per-line so we don't bleed across paragraph
    boundaries that line-rejoin already collapsed (it doesn't cross blocks).
    """
    if not text:
        return text
    out_lines: list[str] = []
    glossary_lower = (
        frozenset(g.lower() for g in glossary_sources) if glossary_sources else frozenset()
    )
    for line in text.split("\n"):
        tokens = line.split(" ")
        if len(tokens) < 4:
            out_lines.append(line)
            continue
        i = 0
        result: list[str] = []
        n = len(tokens)
        while i < n:
            collapsed = False
            # Try longest-first so "X X X X" collapses correctly when the bigram
            # AND trigram are both repeated.
            for span in range(min(6, (n - i) // 2), 1, -1):
                left = tokens[i : i + span]
                right = tokens[i + span : i + 2 * span]
                if not left or not right:
                    continue
                if left != right:
                    continue
                phrase = " ".join(left).strip()
                if not phrase:
                    continue
                if phrase.lower() in glossary_lower:
                    continue
                # Skip glossary terms even if they're a substring of the phrase.
                if any(g and g in phrase.lower() for g in glossary_lower):
                    continue
                result.extend(left)
                i += 2 * span
                report.adjacent_dupes_collapsed += 1
                collapsed = True
                break
            if not collapsed:
                result.append(tokens[i])
                i += 1
        out_lines.append(" ".join(result))
    return "\n".join(out_lines)


def _collapse_whitespace(text: str, report: CleanupReport) -> str:
    new = text.replace("\t", " ")
    new = _MULTI_SPACE_RE.sub(" ", new)
    new = "\n".join(line.rstrip() for line in new.split("\n"))
    if new != text:
        report.whitespace_collapses += 1
    return new


def clean_source_text(
    text: str,
    *,
    glossary_sources: frozenset[str] | None = None,
) -> tuple[str, CleanupReport]:
    """Run every deterministic pre-translation cleanup pass.

    Order matters: NFC first so subsequent regex sees canonical codepoints;
    dehyphenation before line-rejoin (rejoin assumes wrapped lines are intact
    words); mid-word repair after rejoin so it sees fused tokens; dedup last
    on the textual side. `glossary_sources` lets dedup skip legitimate
    repeated party / scheme names.
    """
    report = CleanupReport()
    if not text:
        return text, report
    nfc = unicodedata.normalize("NFC", text)
    if nfc != text:
        report.nfc_normalized = 1
    text = nfc
    text = _strip_invisible(text, report)
    text = _strip_controls(text, report)
    text = _normalize_smart_quotes(text, report)
    text = _dehyphenate(text, report)
    text = _rejoin_wrapped_lines(text, report)
    text = _repair_devanagari_midword_splits(text, report)
    text = _collapse_adjacent_duplicates(text, report, glossary_sources=glossary_sources)
    text = _collapse_whitespace(text, report)
    text = _cap_repeat_runs(text, report)
    return text, report
