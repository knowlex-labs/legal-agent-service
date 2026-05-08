"""Convert translated markdown to a PDF file.

Primary: WeasyPrint (HTML/CSS → PDF) — correct Indic script rendering via Pango/HarfBuzz.
Fallback: fpdf2 — used if WeasyPrint is unavailable.
"""

from __future__ import annotations

import logging
import os
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from legal_agent.agents.translation.doc_profiles import DocProfile

logger = logging.getLogger(__name__)


def markdown_to_pdf(
    md_text: str,
    target_language: str | None = None,
    profile: "DocProfile | None" = None,
) -> bytes:
    """Convert markdown text to PDF bytes.

    Renderer priority:
    1. WeasyPrint  — best Indic shaping via Pango/HarfBuzz; requires GTK libs (Linux/Docker).
    2. Playwright  — headless Chromium; works on Windows/Mac/Linux; excellent Indic/RTL support.
    3. fpdf2       — last resort, Latin/English only (raises for Indic targets).

    Args:
        md_text: Markdown-formatted translated document.
        target_language: Target language name (e.g. "hindi") for font optimization.
        profile: Doc-type profile that selects layout CSS. None → default.

    Returns:
        PDF file as bytes.
    """
    weasy_exc: Exception | None = None
    try:
        return _markdown_to_pdf_weasyprint(md_text, target_language, profile)
    except Exception as exc:
        weasy_exc = exc
        logger.warning(f"WeasyPrint failed ({exc}), trying Playwright fallback")

    try:
        return _markdown_to_pdf_playwright(md_text, target_language)
    except Exception as playwright_exc:
        if _is_non_latin_target(target_language):
            logger.error(
                f"Both WeasyPrint and Playwright failed for '{target_language}'. "
                f"WeasyPrint: {weasy_exc}. Playwright: {playwright_exc}"
            )
            raise RuntimeError(
                f"PDF rendering failed for {target_language}. "
                f"WeasyPrint: {weasy_exc}. "
                f"Playwright: {playwright_exc}. "
                "For Indic/RTL scripts, ensure one of: (a) WeasyPrint GTK libs installed "
                "(Linux/Docker), or (b) Playwright Chromium installed: `playwright install chromium`."
            ) from playwright_exc
        logger.warning(f"Playwright failed ({playwright_exc}), falling back to fpdf2")

    return _markdown_to_pdf_fpdf2(md_text, target_language)


# Languages whose scripts CANNOT be rendered by fpdf2 + Helvetica fallback.
# Anything not in this set is treated as Latin/English-friendly.
_NON_LATIN_LANGUAGES = {
    "hindi", "marathi", "sanskrit", "nepali", "konkani", "maithili", "dogri", "bodo",
    "bengali", "assamese",
    "tamil", "telugu", "kannada", "malayalam",
    "gujarati", "punjabi", "odia",
    "urdu", "kashmiri", "sindhi",
    "manipuri", "santali",
}


def _is_non_latin_target(target_language: str | None) -> bool:
    if not target_language:
        return False
    return target_language.lower().strip() in _NON_LATIN_LANGUAGES


def _markdown_to_pdf_weasyprint(
    md_text: str,
    target_language: str | None = None,
    profile: "DocProfile | None" = None,
) -> bytes:
    """Render markdown to PDF via WeasyPrint with legal document styling."""
    import weasyprint

    from legal_agent.agents.translation.html_builder import markdown_to_html

    html_str = markdown_to_html(md_text, target_language, profile)
    pdf_bytes = weasyprint.HTML(string=html_str).write_pdf()
    logger.info(f"Generated PDF via WeasyPrint ({len(pdf_bytes)} bytes)")
    return pdf_bytes


def _markdown_to_pdf_playwright(md_text: str, target_language: str | None = None) -> bytes:
    """Render markdown to PDF via headless Chromium (Playwright).

    Fallback for environments where WeasyPrint's GTK/Pango libs are unavailable
    (e.g. Windows local dev). Chromium handles Indic scripts and RTL natively.
    Requires: playwright install chromium

    Uses the async API with an explicit ProactorEventLoop because:
    - This runs inside asyncio.to_thread (clean thread, no running loop)
    - Playwright needs subprocess support which SelectorEventLoop lacks on Windows
    """
    import asyncio

    from legal_agent.agents.translation.html_builder import markdown_to_html

    html_str = markdown_to_html(md_text, target_language)

    async def _render() -> bytes:
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            try:
                page = await browser.new_page()
                await page.set_content(html_str, wait_until="networkidle")
                return await page.pdf(
                    format="A4",
                    print_background=True,
                    margin={"top": "2.5cm", "bottom": "2.5cm", "left": "2cm", "right": "2cm"},
                )
            finally:
                await browser.close()

    # ProactorEventLoop is required on Windows for subprocess support.
    # Safe to create a fresh loop here — we're in asyncio.to_thread with no running loop.
    loop = asyncio.ProactorEventLoop() if hasattr(asyncio, "ProactorEventLoop") else asyncio.new_event_loop()
    try:
        pdf_bytes = loop.run_until_complete(_render())
    finally:
        loop.close()

    logger.info(f"Generated PDF via Playwright ({len(pdf_bytes)} bytes)")
    return pdf_bytes


# ── fpdf2 fallback ──────────────────────────────────────────────────────────

_FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans[wght].ttf",
    "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
    "/System/Library/Fonts/Kohinoor.ttc",
    "/Library/Fonts/Arial Unicode.ttf",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
]

_BOLD_FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
    "/System/Library/Fonts/Kohinoor.ttc",
    "/Library/Fonts/Arial Unicode.ttf",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
]


def _find_font(candidates: list[str]) -> str | None:
    for path in candidates:
        if os.path.exists(path):
            return path
    return None


def _markdown_to_pdf_fpdf2(md_text: str, target_language: str | None = None) -> bytes:
    """Legacy fallback: convert markdown text to PDF bytes using fpdf2.

    For non-Latin target languages, this path REQUIRES a Unicode-capable font
    on disk. If none is found we raise rather than render Helvetica tofu.
    """
    from fpdf import FPDF

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=25)
    pdf.set_margins(left=25, top=25, right=25)
    pdf.add_page()

    regular_path = _find_font(_FONT_CANDIDATES)
    bold_path = _find_font(_BOLD_FONT_CANDIDATES)

    if regular_path:
        pdf.add_font("unifont", "", regular_path)
        if bold_path and bold_path != regular_path:
            pdf.add_font("unifont", "B", bold_path)
        else:
            pdf.add_font("unifont", "B", regular_path)
        font_family = "unifont"
    else:
        if _is_non_latin_target(target_language):
            raise RuntimeError(
                f"No Unicode-capable font found on this host; cannot render "
                f"{target_language} PDF via fpdf2 fallback. Searched: {_FONT_CANDIDATES}. "
                "Install fonts-noto (Debian/Ubuntu: apt-get install fonts-noto "
                "fonts-noto-cjk fonts-noto-extra) or ensure WeasyPrint is available "
                "so this fallback path is not exercised."
            )
        font_family = "Helvetica"

    BODY_SIZE = 11
    pdf.set_font(font_family, size=BODY_SIZE)

    for line in md_text.split("\n"):
        stripped = line.strip()

        if not stripped:
            pdf.ln(4)
            continue

        if re.match(r"^-{3,}$", stripped):
            y = pdf.get_y()
            pdf.set_draw_color(160, 160, 160)
            pdf.line(25, y + 2, pdf.w - 25, y + 2)
            pdf.ln(6)
            continue

        heading_match = re.match(r"^(#{1,4})\s+(.*)", stripped)
        if heading_match:
            level = len(heading_match.group(1))
            text = heading_match.group(2)
            sizes = {1: 20, 2: 17, 3: 14, 4: 12}
            pdf.ln(4)
            pdf.set_font(font_family, "B", sizes.get(level, 12))
            pdf.multi_cell(w=0, h=sizes.get(level, 12) * 0.6, text=_strip_bold(text))
            pdf.ln(2)
            pdf.set_font(font_family, size=BODY_SIZE)
            continue

        if stripped.startswith("- ") or stripped.startswith("* "):
            text = stripped[2:]
            _write_list_item(pdf, font_family, BODY_SIZE, "•  ", text)
            continue

        num_match = re.match(r"^(\d+)[.)]\s+(.*)", stripped)
        if num_match:
            num = num_match.group(1)
            text = num_match.group(2)
            _write_list_item(pdf, font_family, BODY_SIZE, f"{num}. ", text)
            continue

        _write_rich_line(pdf, font_family, BODY_SIZE, stripped)
        pdf.ln(3)

    return bytes(pdf.output())


def _write_list_item(pdf, font_family: str, size: float, prefix: str, text: str) -> None:
    pdf.set_font(font_family, size=size)
    pdf.cell(w=12, h=size * 0.5, text=prefix)
    _write_rich_line(pdf, font_family, size, text)
    pdf.ln(2)


def _write_rich_line(pdf, font_family: str, size: float, text: str) -> None:
    parts = re.split(r"(\*\*.*?\*\*)", text)
    for part in parts:
        if part.startswith("**") and part.endswith("**"):
            pdf.set_font(font_family, "B", size)
            pdf.write(h=size * 0.5, text=part[2:-2])
            pdf.set_font(font_family, "", size)
        else:
            pdf.write(h=size * 0.5, text=part)
    pdf.ln()


def _strip_bold(text: str) -> str:
    return re.sub(r"\*\*(.*?)\*\*", r"\1", text)
