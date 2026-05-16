"""Stage 5: per-page HTML rendering.

One HTML document per source page, sized in mm to match the source page exactly.
Blocks are absolute-positioned by their normalized bbox so each output page
corresponds 1:1 to a source page. Hindi expansion is absorbed by a CSS autofit
ladder (line-height → font-size) that runs in Chromium before page.pdf() captures.
"""

from __future__ import annotations

import base64
import html as _html
import re
from functools import lru_cache
from pathlib import Path

from legal_agent.agents.translation_v2.schemas import Block, TranslatedPage

_ASSETS = Path(__file__).parent / "assets"

# Allow only inline emphasis tags in translated text; everything else gets escaped.
_INLINE_ALLOWED = re.compile(r"<(/?)(b|i|u|strong|em)\s*>", re.IGNORECASE)


def _sanitize_inline(text: str) -> str:
    """Escape HTML except for inline <b>/<i>/<u>/<strong>/<em>."""
    placeholders: dict[str, str] = {}

    def stash(match: re.Match[str]) -> str:
        token = f"\x00TAG{len(placeholders)}\x00"
        slash, name = match.group(1), match.group(2).lower()
        # Normalize strong/em → b/i.
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
    """Base64-embed the bundled Noto Serif Devanagari TTFs as @font-face."""
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
html, body { margin: 0; padding: 0; font-family: 'NSDev', 'Noto Serif Devanagari', serif; }
.page {
  position: relative;
  overflow: hidden;
}
.blk {
  position: absolute;
  box-sizing: border-box;
  font-feature-settings: "kern", "liga", "calt";
  word-break: keep-all;
  overflow-wrap: normal;
  line-height: 1.35;
  white-space: pre-wrap;
}
.blk[data-align="center"]  { text-align: center; }
.blk[data-align="right"]   { text-align: right; }
.blk[data-align="justify"] { text-align: justify; }
.blk.bold      { font-weight: 700; }
.blk.italic    { font-style: italic; }
.blk.underline { text-decoration: underline; }
.fit-1 { line-height: 1.28; }
.fit-2 { line-height: 1.22; font-size: calc(var(--fs) - 1pt); }
.fit-3 { line-height: 1.18; font-size: calc(var(--fs) - 2pt); }
.fit-wrap { word-break: break-word; overflow-wrap: anywhere; }
"""

_AUTOFIT_JS = """
(function(){
  var tiers = ['fit-1','fit-2','fit-3'];
  var blocks = document.querySelectorAll('.blk');
  for (var i = 0; i < blocks.length; i++) {
    var el = blocks[i];
    var step = 0;
    while (el.scrollHeight > el.clientHeight + 1 && step < tiers.length) {
      el.classList.add(tiers[step++]);
    }
    if (el.scrollHeight > el.clientHeight + 1) {
      el.classList.add('fit-wrap');
    }
  }
})();
"""


def _block_div(block: Block, page_w_mm: float, page_h_mm: float) -> str:
    x0, y0, x1, y1 = block.bbox_norm
    left_mm = x0 * page_w_mm
    top_mm = y0 * page_h_mm
    width_mm = max(2.0, (x1 - x0) * page_w_mm)
    min_height_mm = max(2.0, (y1 - y0) * page_h_mm)

    classes = ["blk"]
    if block.weight.value == "bold":
        classes.append("bold")
    if block.italic:
        classes.append("italic")
    if block.underline:
        classes.append("underline")

    text = block.text_hi or block.text_en
    body = _sanitize_inline(text)
    fs = block.font_size_pt

    style = (
        f"left:{left_mm:.2f}mm;top:{top_mm:.2f}mm;"
        f"width:{width_mm:.2f}mm;min-height:{min_height_mm:.2f}mm;"
        f"--fs:{fs:.2f}pt;font-size:{fs:.2f}pt;"
    )
    return (
        f'<div class="{" ".join(classes)}" data-align="{block.align.value}" '
        f'data-id="{block.id}" style="{style}">{body}</div>'
    )


def render_page_html(
    page: TranslatedPage,
    page_w_mm: float,
    page_h_mm: float,
    font_face_css: str | None = None,
) -> str:
    """Render one page → one HTML document (zero-margin, exact page size)."""
    font_css = font_face_css if font_face_css is not None else load_font_face_css()
    body_blocks = "\n".join(
        _block_div(b, page_w_mm, page_h_mm)
        for b in sorted(page.blocks, key=lambda b: b.reading_order)
    )
    page_style = (
        f"@page {{ size: {page_w_mm:.2f}mm {page_h_mm:.2f}mm; margin: 0; }}\n"
        f".page {{ width: {page_w_mm:.2f}mm; height: {page_h_mm:.2f}mm; }}\n"
    )
    return (
        '<!doctype html>\n<html lang="hi"><head><meta charset="utf-8">'
        "<title>translation_v2</title><style>\n"
        f"{font_css}{page_style}{_BASE_CSS}"
        "</style></head><body>\n"
        f'<div class="page" data-page="{page.page_no}">\n{body_blocks}\n</div>\n'
        f"<script>{_AUTOFIT_JS}</script>\n"
        "</body></html>\n"
    )
