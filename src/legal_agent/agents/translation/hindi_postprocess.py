"""Rule-based Hindi post-translation cleanup.

Runs after the parallel reviewer + smoother. Surface fixes (danda / comma /
period spacing, mid-word splits, adjacent sentence + ngram dedup, NFC,
Devanagari-scoped smart quotes, numerals policy) plus govt-Hindi label
rewrites gated to the `government_legal` register so academic docs aren't
bureaucrat-ified. Emits a `HindiCleanupReport` for `pipeline_metrics`.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from legal_agent.agents.translation.source_cleanup import DEVA_MIDWORD_SPLIT_RE

_DEVA = "ऀ-ॿ"
_DEVA_RANGE_RE = re.compile(f"[{_DEVA}]")

# CBIC/Rajbhasha conventions — ID label transliteration. The alphanumeric ID
# itself is preserved by the `_ID_RE` sentinel in glossary.freeze.
_GOVT_LABEL_REWRITES = (
    (re.compile(r"\bDIN-"), "डीआईएन-"),
    (re.compile(r"\bF\.\s*NO\.?[-:\s]"), "फा.सं.-"),
    (re.compile(r"\bF\.\s*No\.?[-:\s]"), "फा.सं.-"),
)
_ADDRESS_NORMALIZATIONS = (
    (re.compile(r"प्लॉट\s*नं\.?"), "प्लॉट संख्या"),
    (re.compile(r"बैंक\s*खाता\s*नं\.?"), "बैंक खाता संख्या"),
    (re.compile(r"मकान\s*नं\.?"), "मकान संख्या"),
)

# Danda spacing — sentence-final danda should have a single space after and no
# space before. Sarvam sometimes emits "वह । नहीं" or "वह।नहीं".
_DANDA_LEADING_SPACE_RE = re.compile(r"\s+।")
_DANDA_NO_TRAILING_RE = re.compile(r"।(?=[^\s\n।])")

# Comma / period — strip leading space, ensure one trailing when followed by a
# non-space, non-numeral, non-terminator character. We skip inside decimal /
# section-number patterns (e.g. "1,000", "Sec. 3.5").
_COMMA_LEADING_SPACE_RE = re.compile(r"\s+([,।])")
_PERIOD_LEADING_SPACE_RE = re.compile(r" +(\.)")
_PUNCT_NO_TRAILING_RE = re.compile(r"([,])(?=[^\s\d\n])")

# Multi-space collapse
_MULTI_SPACE_RE = re.compile(r"[ \t]{2,}")

# Sentence splitter for dedup — keeps the terminator with the sentence.
_SENT_SPLIT_RE = re.compile(r"([^।.!?]+[।.!?]+\s*)")

# Western → Devanagari digit map (opt-in).
_WESTERN_TO_DEVA = str.maketrans("0123456789", "०१२३४५६७८९")
# Devanagari → Western (default, applied when documents come back mixed).
_DEVA_TO_WESTERN = str.maketrans("०१२३४५६७८९", "0123456789")


@dataclass
class HindiCleanupReport:
    nfc_normalized: int = 0
    danda_spacing_fixed: int = 0
    comma_spacing_fixed: int = 0
    period_spacing_fixed: int = 0
    midword_splits_repaired: int = 0
    sentence_dedupes: int = 0
    ngram_dedupes: int = 0
    smart_quotes_applied: int = 0
    numerals_converted: int = 0
    govt_label_rewrites: int = 0
    address_normalizations: int = 0
    whitespace_collapses: int = 0

    def as_dict(self) -> dict[str, int]:
        return {k: v for k, v in self.__dict__.items() if v}


def _fix_danda_spacing(text: str, report: HindiCleanupReport) -> str:
    new = _DANDA_LEADING_SPACE_RE.sub("।", text)
    new = _DANDA_NO_TRAILING_RE.sub("। ", new)
    if new != text:
        report.danda_spacing_fixed += 1
    return new


def _fix_comma_spacing(text: str, report: HindiCleanupReport) -> str:
    new = _COMMA_LEADING_SPACE_RE.sub(r"\1", text)
    new = _PUNCT_NO_TRAILING_RE.sub(r"\1 ", new)
    if new != text:
        report.comma_spacing_fixed += 1
    return new


def _fix_period_spacing(text: str, report: HindiCleanupReport) -> str:
    new = _PERIOD_LEADING_SPACE_RE.sub(r"\1", text)
    if new != text:
        report.period_spacing_fixed += 1
    return new


def _repair_midword_splits(text: str, report: HindiCleanupReport) -> str:
    prev = None
    while prev != text:
        prev = text
        text, n = DEVA_MIDWORD_SPLIT_RE.subn(lambda m: m.group(1) + m.group(2), text)
        report.midword_splits_repaired += n
    return text


def _normalize_for_dedup(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip().lower())


def _dedupe_adjacent_sentences(
    text: str,
    report: HindiCleanupReport,
    *,
    glossary_targets: frozenset[str],
) -> str:
    parts = _SENT_SPLIT_RE.findall(text)
    if len(parts) < 2:
        return text
    out: list[str] = []
    last_norm: str | None = None
    for sent in parts:
        norm = _normalize_for_dedup(sent)
        # Glossary-protected sentences (e.g. recurring party names) pass through.
        is_glossary = any(g and g.lower() in norm for g in glossary_targets)
        if last_norm is not None and norm == last_norm and not is_glossary and len(norm) > 4:
            report.sentence_dedupes += 1
            continue
        out.append(sent)
        last_norm = norm
    # Append any trailing content not captured by the regex (no terminator).
    consumed = "".join(parts)
    if len(consumed) < len(text):
        out.append(text[len(consumed):])
    return "".join(out)


def _dedupe_adjacent_ngrams(
    text: str,
    report: HindiCleanupReport,
    *,
    glossary_targets: frozenset[str],
) -> str:
    if not text:
        return text
    glossary_lower = frozenset(g.lower() for g in glossary_targets if g)
    out_lines: list[str] = []
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
            for span in range(min(6, (n - i) // 2), 1, -1):
                left = tokens[i : i + span]
                right = tokens[i + span : i + 2 * span]
                if not left or left != right:
                    continue
                phrase = " ".join(left).strip().lower()
                if not phrase:
                    continue
                if any(g and g in phrase for g in glossary_lower):
                    continue
                result.extend(left)
                i += 2 * span
                report.ngram_dedupes += 1
                collapsed = True
                break
            if not collapsed:
                result.append(tokens[i])
                i += 1
        out_lines.append(" ".join(result))
    return "\n".join(out_lines)


def _apply_devanagari_quotes(text: str, report: HindiCleanupReport) -> str:
    """Replace `"..."` with Devanagari curly quotes only when both delimiters
    sit immediately next to Devanagari codepoints. English-language regions
    keep ASCII quotes intact.
    """
    if '"' not in text:
        return text
    # Find paired ASCII double quotes. Open quote must have a Devanagari char
    # within 2 chars after; close quote must have one within 2 chars before.
    pattern = re.compile(r'"([^"]{1,500})"')

    def _repl(m: re.Match) -> str:
        inner = m.group(1)
        start, end = m.span()
        head = text[max(0, start - 2): start]
        tail = text[end : end + 2]
        prev_deva = bool(_DEVA_RANGE_RE.search(head)) or bool(_DEVA_RANGE_RE.search(inner[:3]))
        next_deva = bool(_DEVA_RANGE_RE.search(tail)) or bool(_DEVA_RANGE_RE.search(inner[-3:]))
        if prev_deva and next_deva:
            report.smart_quotes_applied += 1
            return f"“{inner}”"
        return m.group(0)

    return pattern.sub(_repl, text)


def _apply_numerals_policy(
    text: str, report: HindiCleanupReport, policy: str
) -> str:
    """Convert numerals according to policy.

    "western" (default for legal docs): Devanagari digits → 0-9.
    "devanagari" (opt-in): 0-9 → ०-९ outside protected patterns. Skipped
    inside sentinel-like brackets to avoid corrupting identifiers.
    """
    if policy == "western":
        new = text.translate(_DEVA_TO_WESTERN)
        if new != text:
            report.numerals_converted += 1
        return new
    if policy == "devanagari":
        # Only convert digits that sit inside Devanagari context to avoid
        # rewriting URLs, emails, citations.
        def _repl(m: re.Match) -> str:
            start = m.start()
            head = text[max(0, start - 3): start]
            tail = text[m.end() : m.end() + 3]
            if _DEVA_RANGE_RE.search(head) or _DEVA_RANGE_RE.search(tail):
                report.numerals_converted += 1
                return m.group(0).translate(_WESTERN_TO_DEVA)
            return m.group(0)

        return re.sub(r"\d+", _repl, text)
    return text


def _collapse_whitespace(text: str, report: HindiCleanupReport) -> str:
    new = _MULTI_SPACE_RE.sub(" ", text)
    new = "\n".join(line.rstrip() for line in new.split("\n"))
    if new != text:
        report.whitespace_collapses += 1
    return new


def _apply_govt_hindi(text: str, report: HindiCleanupReport) -> str:
    for pat, repl in _GOVT_LABEL_REWRITES:
        new = pat.sub(repl, text)
        if new != text:
            report.govt_label_rewrites += 1
            text = new
    for pat, repl in _ADDRESS_NORMALIZATIONS:
        new = pat.sub(repl, text)
        if new != text:
            report.address_normalizations += 1
            text = new
    return text


def clean_hindi_output(
    text: str,
    *,
    register: str = "general",
    glossary_targets: frozenset[str] | None = None,
    numerals_policy: str = "western",
) -> tuple[str, HindiCleanupReport]:
    """Run all rule-based Hindi cleanup passes.

    `register` gates govt-Hindi rules (DIN-, F.NO., address-abbrev expansions)
    to government_legal docs only — academic translations no longer pick up
    the CBIC bureaucratic feel from these rules.
    """
    report = HindiCleanupReport()
    if not text:
        return text, report
    targets = glossary_targets or frozenset()

    nfc = unicodedata.normalize("NFC", text)
    if nfc != text:
        report.nfc_normalized = 1
    text = nfc

    text = _repair_midword_splits(text, report)
    text = _fix_danda_spacing(text, report)
    text = _fix_comma_spacing(text, report)
    text = _fix_period_spacing(text, report)
    text = _dedupe_adjacent_sentences(text, report, glossary_targets=targets)
    text = _dedupe_adjacent_ngrams(text, report, glossary_targets=targets)
    text = _apply_devanagari_quotes(text, report)
    text = _apply_numerals_policy(text, report, numerals_policy)
    text = _collapse_whitespace(text, report)
    if register == "government_legal":
        text = _apply_govt_hindi(text, report)
    return text, report
