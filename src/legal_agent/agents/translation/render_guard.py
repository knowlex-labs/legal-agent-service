"""Post-render validation for translated PDFs.

The translation pipeline can fail in subtle ways the user only sees after they
open the file: the system has no Devanagari font and renders tofu (`□□□`); a
ledger citation got dropped; the page count is suspiciously low. This module
runs after WeasyPrint and surfaces these as warnings — critical ones bubble up
as a `[RENDER_GUARD]` staged error so we never upload an unreadable PDF.

Cheap by design: opens the rendered PDF with PyMuPDF, samples text, runs a few
Unicode-range checks. No external services, no LLM calls.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, cast

if TYPE_CHECKING:
    from legal_agent.agents.translation.structure_aware_extractor import LedgerEntry

logger = logging.getLogger(__name__)


Severity = Literal["info", "warning", "critical"]


@dataclass
class GuardWarning:
    severity: Severity
    code: str
    message: str


# Unicode ranges per script — used to detect tofu (font-miss) by checking how
# many target-script characters survived the render.
_SCRIPT_RANGES: dict[str, list[tuple[int, int]]] = {
    "hindi":     [(0x0900, 0x097F), (0x1CD0, 0x1CFF), (0xA8E0, 0xA8FF)],
    "marathi":   [(0x0900, 0x097F)],
    "sanskrit":  [(0x0900, 0x097F)],
    "nepali":    [(0x0900, 0x097F)],
    "konkani":   [(0x0900, 0x097F)],
    "maithili":  [(0x0900, 0x097F)],
    "dogri":     [(0x0900, 0x097F)],
    "bodo":      [(0x0900, 0x097F)],
    "bengali":   [(0x0980, 0x09FF)],
    "assamese":  [(0x0980, 0x09FF)],
    "tamil":     [(0x0B80, 0x0BFF)],
    "telugu":    [(0x0C00, 0x0C7F)],
    "kannada":   [(0x0C80, 0x0CFF)],
    "malayalam": [(0x0D00, 0x0D7F)],
    "gujarati":  [(0x0A80, 0x0AFF)],
    "punjabi":   [(0x0A00, 0x0A7F)],
    "odia":      [(0x0B00, 0x0B7F)],
    "urdu":      [(0x0600, 0x06FF), (0xFB50, 0xFDFF), (0xFE70, 0xFEFF)],
    "kashmiri":  [(0x0600, 0x06FF), (0xFB50, 0xFDFF), (0xFE70, 0xFEFF)],
    "sindhi":    [(0x0600, 0x06FF), (0xFB50, 0xFDFF), (0xFE70, 0xFEFF)],
    "manipuri":  [(0xABC0, 0xABFF), (0x0980, 0x09FF)],
    "santali":   [(0x1C50, 0x1C7F)],
}

_TARGET_SCRIPT_COVERAGE_THRESHOLD = 0.5
_MIN_PDF_BYTES = 1024
_PAGE_RATIO_LO = 0.25  # source_chars / 3000 / 2x
_PAGE_RATIO_HI = 4.0   # source_chars / 3000 * 2x


def validate_rendered_pdf(
    pdf_bytes: bytes,
    target_language: str | None,
    source_text_len: int,
    ledger: "list[LedgerEntry] | None" = None,
) -> list[GuardWarning]:
    """Run all post-render checks and return the warning list.

    Caller decides what to do with critical warnings — the convention in
    `service.py` is to raise `StagedError(ErrorStage.RENDER_GUARD, ...)`.
    """
    warnings: list[GuardWarning] = []

    if not pdf_bytes or len(pdf_bytes) < _MIN_PDF_BYTES:
        warnings.append(GuardWarning(
            "critical", "empty_pdf",
            f"Rendered PDF is suspiciously small ({len(pdf_bytes)} bytes).",
        ))
        return warnings  # everything below assumes a usable PDF

    rendered_text, page_count = _read_pdf(pdf_bytes)

    # 1. Page count plausibility.
    if source_text_len > 0 and page_count > 0:
        expected = max(1, source_text_len / 3000)
        ratio = page_count / expected
        if ratio < _PAGE_RATIO_LO or ratio > _PAGE_RATIO_HI:
            warnings.append(GuardWarning(
                "warning", "page_count_implausible",
                f"Page count {page_count} vs expected ~{expected:.1f} "
                f"(ratio {ratio:.2f}). Source was {source_text_len} chars.",
            ))

    # 2. Tofu / font-miss check via target-script coverage.
    coverage_warning = _check_target_script_coverage(rendered_text, target_language)
    if coverage_warning:
        warnings.append(coverage_warning)

    # 3. Ledger preservation — every entry must appear verbatim in the output.
    if ledger:
        missing = [e.text for e in ledger if e.text and e.text not in rendered_text]
        if missing:
            preview = ", ".join(repr(m) for m in missing[:3])
            warnings.append(GuardWarning(
                "critical", "ledger_missing",
                f"{len(missing)} do-not-translate entries dropped from output (e.g. {preview}).",
            ))

    return warnings


def has_critical(warnings: list[GuardWarning]) -> bool:
    return any(w.severity == "critical" for w in warnings)


def summarize(warnings: list[GuardWarning]) -> list[dict[str, str]]:
    """Render-guard warnings as plain dicts suitable for job metadata storage."""
    return [
        {"severity": w.severity, "code": w.code, "message": w.message}
        for w in warnings
    ]


# ── internals ───────────────────────────────────────────────────────────────


def _read_pdf(pdf_bytes: bytes) -> tuple[str, int]:
    """Open the rendered PDF and return (concatenated_text, page_count)."""
    try:
        import fitz  # PyMuPDF
    except Exception as exc:
        logger.warning(f"PyMuPDF unavailable for render guard: {exc}")
        return "", 0
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:
        logger.warning(f"Could not open rendered PDF: {exc}")
        return "", 0
    try:
        parts = [cast(str, page.get_text("text")) for page in doc]
        page_count = doc.page_count
    finally:
        doc.close()
    return "\n".join(parts), page_count


def _check_target_script_coverage(
    rendered_text: str,
    target_language: str | None,
) -> GuardWarning | None:
    """For non-Latin targets, ≥50% of meaningful characters must fall in the
    target script. Below that → font miss / tofu / wrong language output."""
    if not target_language:
        return None
    ranges = _SCRIPT_RANGES.get(target_language.lower().strip())
    if not ranges:
        return None  # English / Latin target: no script-coverage check
    if not rendered_text:
        return GuardWarning(
            "critical", "empty_render",
            "Rendered PDF text-extraction returned empty — likely tofu.",
        )

    meaningful = 0
    in_target = 0
    for ch in rendered_text:
        if ch.isspace():
            continue
        cp = ord(ch)
        # Skip ASCII digits / punctuation — they're shared across every script
        # and would dilute the coverage ratio for Indic-script documents that
        # legitimately preserve numbers and citations in Latin form.
        if cp < 0x0080 and not ch.isalpha():
            continue
        meaningful += 1
        for lo, hi in ranges:
            if lo <= cp <= hi:
                in_target += 1
                break

    if meaningful == 0:
        return GuardWarning(
            "critical", "no_meaningful_chars",
            "Rendered PDF contains no script-bearing characters — likely tofu.",
        )

    coverage = in_target / meaningful
    if coverage < _TARGET_SCRIPT_COVERAGE_THRESHOLD:
        return GuardWarning(
            "critical", "script_coverage_low",
            f"Only {coverage:.1%} of rendered text is in the target script "
            f"({target_language}). Likely font missing or wrong-language output.",
        )
    return None
