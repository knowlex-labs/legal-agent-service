"""Convert translated markdown to a PDF file using fpdf2."""

import os
import re

from fpdf import FPDF

# Font search paths — tried in order until a suitable TTF/TTC is found.
_FONT_CANDIDATES = [
    # Docker / Debian (fonts-noto)
    "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans[wght].ttf",
    # FreeFonts fallback
    "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
    # macOS
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


def markdown_to_pdf(md_text: str) -> bytes:
    """Convert markdown text to PDF bytes.

    Handles: headings (##, ###), bold (**), numbered lists, bullet lists,
    horizontal rules (---), and regular paragraphs.
    """
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=25)
    pdf.set_margins(left=25, top=25, right=25)
    pdf.add_page()

    # Register Unicode font
    regular_path = _find_font(_FONT_CANDIDATES)
    bold_path = _find_font(_BOLD_FONT_CANDIDATES)

    if regular_path:
        pdf.add_font("unifont", "", regular_path)
        if bold_path and bold_path != regular_path:
            pdf.add_font("unifont", "B", bold_path)
        else:
            # Use regular for bold too (fpdf2 will fake-bold)
            pdf.add_font("unifont", "B", regular_path)
        font_family = "unifont"
    else:
        font_family = "Helvetica"

    BODY_SIZE = 11
    pdf.set_font(font_family, size=BODY_SIZE)

    for line in md_text.split("\n"):
        stripped = line.strip()

        if not stripped:
            pdf.ln(4)
            continue

        # Horizontal rule
        if re.match(r"^-{3,}$", stripped):
            y = pdf.get_y()
            pdf.set_draw_color(160, 160, 160)
            pdf.line(25, y + 2, pdf.w - 25, y + 2)
            pdf.ln(6)
            continue

        # Headings
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

        # Bullet list
        if stripped.startswith("- ") or stripped.startswith("* "):
            text = stripped[2:]
            _write_list_item(pdf, font_family, BODY_SIZE, "•  ", text)
            continue

        # Numbered list
        num_match = re.match(r"^(\d+)[.)]\s+(.*)", stripped)
        if num_match:
            num = num_match.group(1)
            text = num_match.group(2)
            _write_list_item(pdf, font_family, BODY_SIZE, f"{num}. ", text)
            continue

        # Regular paragraph
        _write_rich_line(pdf, font_family, BODY_SIZE, stripped)
        pdf.ln(3)

    return bytes(pdf.output())


def _write_list_item(pdf: FPDF, font_family: str, size: float, prefix: str, text: str) -> None:
    """Write a list item with bullet/number prefix and rich text."""
    pdf.set_font(font_family, size=size)
    pdf.cell(w=12, h=size * 0.5, text=prefix)
    _write_rich_line(pdf, font_family, size, text)
    pdf.ln(2)


def _write_rich_line(pdf: FPDF, font_family: str, size: float, text: str) -> None:
    """Write a line with inline **bold** support using pdf.write()."""
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
    """Remove ** markers from text."""
    return re.sub(r"\*\*(.*?)\*\*", r"\1", text)
