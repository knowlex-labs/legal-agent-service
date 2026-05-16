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

from legal_agent.agents.translation_v2.schemas import Block, BlockRole, TranslatedPage

_ASSETS = Path(__file__).parent / "assets"

# Allow only inline emphasis tags in translated text; everything else gets escaped.
_INLINE_ALLOWED = re.compile(r"<(/?)(b|i|u|strong|em)\s*>", re.IGNORECASE)

# Short numeric/punctuation strings (page ranges, dates, clause numbers) that
# CSS would otherwise wrap at a hyphen — e.g. "12-166" becoming "12-16" + "6".
# When matched, we apply white-space: nowrap so the token stays on one line.
_NUMERIC_COMPACT_RE = re.compile(r"^[\d\s\-–—/.,:()]+$")
_NUMERIC_COMPACT_MAX_LEN = 16

# Extra horizontal breathing room (mm) we add to a compact block's width so the
# slightly-wider serif rendering of digits doesn't collide with the right neighbour.
_COMPACT_PAD_MM = 2.0

# Horizontal rules (separator blocks) are rendered as filled divs. The vision
# model often emits a generous vertical bbox for what is visually a 1px line on
# the source — without a cap the output would be a thick black bar. Cap at the
# nearest equivalent of a typical printer rule (~1px at 72dpi ≈ 0.35mm).
_SEPARATOR_MAX_HEIGHT_MM = 0.4

# Minimum vertical gap (mm) the reflow pass enforces between two blocks that
# would otherwise overlap after text wrap. Just enough to keep wrapped rows
# from visually touching the row below.
_REFLOW_MIN_GAP_MM = 1.0


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
/* Compact: short numeric/punctuation tokens (page ranges, dates) that must not
   wrap at hyphens. Horizontal overflow is preferable to row-to-row collision. */
.blk.compact   { white-space: nowrap; }
/* Separator: a horizontal or vertical rule. No text body — the div itself is
   the line, filled with currentColor and sized by its bbox. */
.blk.separator {
  white-space: normal;
  background: currentColor;
  color: #000;
}
.fit-1 { line-height: 1.28; }
.fit-2 { line-height: 1.22; font-size: calc(var(--fs) - 1pt); }
.fit-3 { line-height: 1.18; font-size: calc(var(--fs) - 2pt); }
.fit-wrap { word-break: break-word; overflow-wrap: anywhere; }
"""

_AUTOFIT_JS = (
    """
(function(){
  /* ── Step 1: autofit ladder (existing) ── */
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

  /* ── Step 2: vertical reflow for wrapped blocks ──
     When a text block wraps to more lines than its source bbox accommodated,
     it grows downward and visually crashes into the row below. Walk blocks in
     reading order; for each one, find any prior block that overlaps it
     horizontally, and push the current block down so its top sits at least
     MIN_GAP below that prior block's rendered bottom. Separators don't shift
     anything — they're table rules and should stay anchored. */
  var MIN_GAP_PX = """
    + f"{_REFLOW_MIN_GAP_MM} * (96 / 25.4)"
    + """;
  var textBlocks = Array.prototype.slice.call(
    document.querySelectorAll('.blk:not(.separator)')
  );
  textBlocks.sort(function(a, b) {
    return a.getBoundingClientRect().top - b.getBoundingClientRect().top;
  });
  for (var i = 0; i < textBlocks.length; i++) {
    var cur = textBlocks[i];
    var curRect = cur.getBoundingClientRect();
    var maxBottom = -Infinity;
    for (var j = 0; j < i; j++) {
      var prev = textBlocks[j];
      var prevRect = prev.getBoundingClientRect();
      var horizSeparate = (curRect.right <= prevRect.left + 1) ||
                          (prevRect.right <= curRect.left + 1);
      if (horizSeparate) continue;
      if (prevRect.bottom > maxBottom) maxBottom = prevRect.bottom;
    }
    if (maxBottom > -Infinity && curRect.top < maxBottom + MIN_GAP_PX) {
      var shiftPx = (maxBottom + MIN_GAP_PX) - curRect.top;
      var shiftMm = shiftPx / (96 / 25.4);
      var currentTopMm = parseFloat(cur.style.top) || 0;
      cur.style.top = (currentTopMm + shiftMm).toFixed(2) + 'mm';
    }
  }
})();
"""
)


def _is_numeric_compact(text: str) -> bool:
    """Short string made entirely of digits / punctuation — must not wrap on '-'."""
    if not text or len(text) > _NUMERIC_COMPACT_MAX_LEN:
        return False
    return bool(_NUMERIC_COMPACT_RE.fullmatch(text))


def _block_div(block: Block, page_w_mm: float, page_h_mm: float) -> str:
    x0, y0, x1, y1 = block.bbox_norm
    left_mm = x0 * page_w_mm
    top_mm = y0 * page_h_mm
    width_mm = max(2.0, (x1 - x0) * page_w_mm)
    min_height_mm = max(2.0, (y1 - y0) * page_h_mm)

    classes = ["blk"]

    # Separator: rule line, no text body. Cap height so the line renders as a
    # thin line, not a thick black bar — the vision model's bbox height is the
    # visible region around the rule, not the rule's stroke width.
    if block.role == BlockRole.separator:
        classes.append("separator")
        sep_height_mm = min(min_height_mm, _SEPARATOR_MAX_HEIGHT_MM)
        style = (
            f"left:{left_mm:.2f}mm;top:{top_mm:.2f}mm;"
            f"width:{width_mm:.2f}mm;height:{sep_height_mm:.2f}mm;"
        )
        return f'<div class="{" ".join(classes)}" data-id="{block.id}" style="{style}"></div>'

    if block.weight.value == "bold":
        classes.append("bold")
    if block.italic:
        classes.append("italic")
    if block.underline:
        classes.append("underline")

    text = block.text_hi or block.text_en
    body = _sanitize_inline(text)
    fs = block.font_size_pt

    # Numeric/short tokens: extend width slightly and mark compact so they don't
    # wrap at hyphens. The wider serif rendering plus the +2mm pad keeps the
    # token on one row without colliding with the next block to the right.
    if _is_numeric_compact(text):
        classes.append("compact")
        width_mm = min(page_w_mm - left_mm, width_mm + _COMPACT_PAD_MM)

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
