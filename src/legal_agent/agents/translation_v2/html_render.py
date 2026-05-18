"""Stage 5 (v3 flow-layout): per-page semantic HTML rendering.

Each source page → one HTML document at the same physical size, but content
flows with normal typography (no absolute-positioned blocks).

- title  → `<h1.title>` centered, larger, bold, underlined.
- heading→ `<h2.heading>` centered, medium, bold, underlined.
- header → `<div.page-header>` at top.
- paragraph → `<p.paragraph>` justified, ~11pt, line-height 1.6.
- numbered paragraph ("1. That, on..." / "a) ..." / "(i) ...") → flex marker
  + body so wrapped lines align under the body, not under the marker.
- table_cell (with table_id / row_index / column_index from Azure) →
  grouped into `<table.legal-table><thead>?<tbody><tr><td>…`
- footer / page_number → `<div.page-footer>` at bottom-centered.
- separator role is dropped — table borders come from CSS, not synthetic blocks.

A single JS pass after layout measures content height and, if it overflows
the page, applies a CSS transform-scale (down to a 0.7 readability floor).
"""

from __future__ import annotations

import base64
import html as _html
import re
from functools import lru_cache
from pathlib import Path

from legal_agent.agents.translation_v2.schemas import (
    Block,
    BlockRole,
    BlockWeight,
    TranslatedPage,
)

_ASSETS = Path(__file__).parent / "assets"

# Allow only inline emphasis tags in translated text; everything else escapes.
_INLINE_ALLOWED = re.compile(r"<(/?)(b|i|u|strong|em)\s*>", re.IGNORECASE)

# Detect a leading marker like "1." / "(a)" / "(i)" / "क)" / "1)" for hanging indent.
# Supports Devanagari letter markers too. The marker MUST be followed by whitespace.
NUMBERED_RE = re.compile(
    r"^\s*((?:\d{1,3}\.|"
    r"\([a-zA-Z0-9ivxIVXऀ-ॿ]{1,4}\)|"
    r"[a-zA-Zऀ-ॿ]\)|"
    r"\d{1,3}\)))\s+(.*)$",
    re.S,
)


def _sanitize_inline(text: str) -> str:
    """Escape HTML except for inline <b>/<i>/<u>/<strong>/<em>."""
    placeholders: dict[str, str] = {}

    def stash(match: re.Match[str]) -> str:
        token = f"\x00TAG{len(placeholders)}\x00"
        slash, name = match.group(1), match.group(2).lower()
        canonical = {"strong": "b", "em": "i"}.get(name, name)
        placeholders[token] = f"<{slash}{canonical}>"
        return token

    masked = _INLINE_ALLOWED.sub(stash, text)
    escaped = _html.escape(masked, quote=False)
    for token, tag in placeholders.items():
        escaped = escaped.replace(token, tag)
    return escaped


@lru_cache(maxsize=1)
def load_font_face_css() -> str:
    """Base64-embed bundled Noto Serif Devanagari TTFs as @font-face."""
    reg = (_ASSETS / "NotoSerifDevanagari-Regular.ttf").read_bytes()
    bold = (_ASSETS / "NotoSerifDevanagari-Bold.ttf").read_bytes()
    reg_b64 = base64.b64encode(reg).decode("ascii")
    bold_b64 = base64.b64encode(bold).decode("ascii")
    return (
        "@font-face { font-family: 'NSDev'; font-weight: 400; font-style: normal; "
        f"src: url(data:font/ttf;base64,{reg_b64}) format('truetype'); }}\n"
        "@font-face { font-family: 'NSDev'; font-weight: 700; font-style: normal; "
        f"src: url(data:font/ttf;base64,{bold_b64}) format('truetype'); }}\n"
    )


_BASE_CSS = """
*, *::before, *::after { box-sizing: border-box; }
html, body { margin: 0; padding: 0; }
body {
  font-family: 'NSDev', 'Noto Serif Devanagari', serif;
  font-size: 11pt;
  line-height: 1.6;
  color: #000;
}
.page {
  position: relative;
  padding: 18mm 18mm;
  overflow: hidden;
}
.page-content {
  transform-origin: top left;
  width: 100%;
}
/* Typography by role */
.title {
  text-align: center;
  font-size: 16pt;
  font-weight: 700;
  text-decoration: underline;
  margin: 0 0 6mm 0;
  line-height: 1.3;
}
.heading {
  text-align: center;
  font-size: 13pt;
  font-weight: 700;
  text-decoration: underline;
  margin: 6mm 0 4mm 0;
  line-height: 1.3;
}
.paragraph {
  text-align: justify;
  margin: 0 0 5pt 0;
}
.paragraph.center { text-align: center; }
.paragraph.right { text-align: right; }
.paragraph.bold { font-weight: 700; }
.paragraph.italic { font-style: italic; }
.paragraph.underline { text-decoration: underline; }
/* Numbered items: hanging indent — marker in gutter, body justified */
.paragraph.numbered {
  display: flex;
  gap: 4mm;
  align-items: baseline;
  margin: 0 0 7pt 0;
}
.paragraph.numbered .marker { flex: 0 0 8mm; }
.paragraph.numbered .body { flex: 1; text-align: justify; }
.page-header {
  font-size: 10pt;
  text-align: center;
  margin: 0 0 4mm 0;
}
.page-footer {
  font-size: 10pt;
  text-align: center;
  margin: 4mm 0 0 0;
}
/* Tables */
table.legal-table {
  width: 100%;
  border-collapse: collapse;
  margin: 4mm 0;
  border-top: 0.5pt solid #000;
  border-bottom: 0.5pt solid #000;
}
table.legal-table thead th {
  text-align: left;
  font-weight: 700;
  border-bottom: 0.5pt solid #000;
  padding: 2mm 3mm;
  font-size: 10.5pt;
}
table.legal-table tbody td {
  padding: 1.5mm 3mm;
  vertical-align: top;
  font-size: 10.5pt;
}
"""


_AUTOFIT_JS = """
(function(){
  /* Single-shot page-level autofit. If the content overflows the printable
     area, apply a uniform transform: scale(N) so it fits. No per-block
     gymnastics; one measurement, one transform. Floor at 0.7 to keep text
     readable; below that we let the bottom clip (rare in practice). */
  var page = document.querySelector('.page');
  var content = document.querySelector('.page-content');
  if (!page || !content) return;
  var pageRect = page.getBoundingClientRect();
  var st = getComputedStyle(page);
  var padT = parseFloat(st.paddingTop) || 0;
  var padB = parseFloat(st.paddingBottom) || 0;
  var availableHeight = pageRect.height - padT - padB;
  var contentHeight = content.scrollHeight;
  if (contentHeight <= availableHeight || contentHeight === 0) return;
  var scale = availableHeight / contentHeight;
  if (scale < 0.7) scale = 0.7;
  content.style.transform = 'scale(' + scale + ')';
  content.style.width = (100 / scale).toFixed(2) + '%';
})();
"""


def _text_for(block: Block) -> str:
    return block.text_hi or block.text_en


def _style_classes(block: Block) -> list[str]:
    classes: list[str] = []
    if block.weight == BlockWeight.bold:
        classes.append("bold")
    if block.italic:
        classes.append("italic")
    if block.underline:
        classes.append("underline")
    if block.align.value == "center":
        classes.append("center")
    elif block.align.value == "right":
        classes.append("right")
    return classes


def _emit_paragraph(block: Block) -> str:
    text = _text_for(block)
    classes = ["paragraph", *_style_classes(block)]

    m = NUMBERED_RE.match(text)
    if m:
        marker, rest = m.groups()
        classes.append("numbered")
        return (
            f'<p class="{" ".join(classes)}" data-id="{block.id}">'
            f'<span class="marker">{_sanitize_inline(marker)}</span>'
            f'<span class="body">{_sanitize_inline(rest)}</span>'
            f"</p>"
        )
    return (
        f'<p class="{" ".join(classes)}" data-id="{block.id}">'
        f"{_sanitize_inline(text)}</p>"
    )


def _emit_heading(block: Block, *, level: int) -> str:
    tag = "h1" if level == 1 else "h2"
    klass = "title" if level == 1 else "heading"
    return (
        f'<{tag} class="{klass}" data-id="{block.id}">'
        f"{_sanitize_inline(_text_for(block))}</{tag}>"
    )


def _emit_band(block: Block, klass: str) -> str:
    return (
        f'<div class="{klass}" data-id="{block.id}">'
        f"{_sanitize_inline(_text_for(block))}</div>"
    )


def _emit_table(cells: list[Block]) -> str:
    """Group a contiguous run of table_cell blocks into one `<table>`.

    Cells are bucketed by `row_index`. Rows where every cell has
    `is_header_cell == True` go in `<thead>`; the rest go in `<tbody>`.
    """
    if not cells:
        return ""
    by_row: dict[int, list[Block]] = {}
    for c in cells:
        by_row.setdefault(c.row_index, []).append(c)
    rows_sorted = sorted(by_row.items())

    header_rows: list[tuple[int, list[Block]]] = []
    body_rows: list[tuple[int, list[Block]]] = []
    # Header rows = consecutive leading rows where every cell is_header_cell.
    in_header = True
    for idx, row in rows_sorted:
        row.sort(key=lambda c: c.column_index)
        if in_header and all(c.is_header_cell for c in row):
            header_rows.append((idx, row))
        else:
            in_header = False
            body_rows.append((idx, row))

    def _cell_html(cell: Block, tag: str) -> str:
        attrs = []
        if cell.row_span > 1:
            attrs.append(f'rowspan="{cell.row_span}"')
        if cell.column_span > 1:
            attrs.append(f'colspan="{cell.column_span}"')
        attrs_str = (" " + " ".join(attrs)) if attrs else ""
        return f"<{tag}{attrs_str}>{_sanitize_inline(_text_for(cell))}</{tag}>"

    parts: list[str] = ['<table class="legal-table">']
    if header_rows:
        parts.append("<thead>")
        for _, row in header_rows:
            parts.append(
                "<tr>" + "".join(_cell_html(c, "th") for c in row) + "</tr>"
            )
        parts.append("</thead>")
    if body_rows:
        parts.append("<tbody>")
        for _, row in body_rows:
            parts.append(
                "<tr>" + "".join(_cell_html(c, "td") for c in row) + "</tr>"
            )
        parts.append("</tbody>")
    parts.append("</table>")
    return "\n".join(parts)


def _emit_block(block: Block) -> str:
    role = block.role
    if role == BlockRole.title:
        return _emit_heading(block, level=1)
    if role in (BlockRole.heading, BlockRole.subheading):
        return _emit_heading(block, level=2)
    if role == BlockRole.header:
        return _emit_band(block, "page-header")
    if role in (BlockRole.footer, BlockRole.page_number):
        return _emit_band(block, "page-footer")
    if role == BlockRole.separator:
        return ""  # Tables handle their own rules in CSS.
    # paragraph, clause, list_item, signature, caption, table_cell-without-table,
    # other → flowing paragraph.
    return _emit_paragraph(block)


def render_page_html(
    page: TranslatedPage,
    page_w_mm: float,
    page_h_mm: float,
    font_face_css: str | None = None,
) -> str:
    """Render one source page as a semantic HTML document of the same size."""
    font_css = font_face_css if font_face_css is not None else load_font_face_css()
    blocks = sorted(page.blocks, key=lambda b: b.reading_order)

    parts: list[str] = []
    i = 0
    while i < len(blocks):
        b = blocks[i]
        # Group consecutive table_cell blocks with the same `table_id` into one
        # HTML table. table_id=None falls back to per-block rendering.
        if b.role == BlockRole.table_cell and b.table_id is not None:
            tid = b.table_id
            j = i
            while (
                j < len(blocks)
                and blocks[j].role == BlockRole.table_cell
                and blocks[j].table_id == tid
            ):
                j += 1
            table_html = _emit_table(blocks[i:j])
            if table_html:
                parts.append(table_html)
            i = j
            continue
        chunk = _emit_block(b)
        if chunk:
            parts.append(chunk)
        i += 1
    body_html = "\n".join(parts)

    page_style = (
        f"@page {{ size: {page_w_mm:.2f}mm {page_h_mm:.2f}mm; margin: 0; }}\n"
        f".page {{ width: {page_w_mm:.2f}mm; height: {page_h_mm:.2f}mm; }}\n"
    )
    return (
        '<!doctype html>\n<html lang="hi"><head><meta charset="utf-8">'
        "<title>translation_v3</title><style>\n"
        f"{font_css}{page_style}{_BASE_CSS}"
        "</style></head><body>\n"
        f'<div class="page" data-page="{page.page_no}">\n'
        f'<div class="page-content">\n{body_html}\n</div>\n'
        "</div>\n"
        f"<script>{_AUTOFIT_JS}</script>\n"
        "</body></html>\n"
    )
