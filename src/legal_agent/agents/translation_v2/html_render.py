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

# Devanagari glyphs at the same point size as Latin look perceptibly smaller
# because their x-height is lower and the bundled Noto Serif Devanagari runs
# slightly tight. A small bump keeps body text legible. Kept conservative
# (1.05) because larger scales accumulate vertical drift across the page and
# push footers off the bottom — the autofit ladder's per-block shrink is too
# local to undo a cumulative cascade.
_HINDI_FONT_SCALE = 1.05


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
html, body {
  margin: 0; padding: 0;
  font-family: 'NSDev', 'Noto Serif Devanagari', serif;
  /* Hard-cap document height to one page so Chromium's print engine cannot
     emit an extra physical page when an absolute-positioned block lands past
     the page boundary after Hindi expansion. Combined with page_ranges:"1"
     in compose.py, this makes the page-count mismatch failure impossible. */
  overflow: hidden;
  height: 100%;
}
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
  /* 1.2 matches typeset legal documents (Microsoft Office defaults to 1.35
     which is too loose — multiplied across a page it accumulates ~5-10mm of
     unnecessary downward drift, pushing footer content off the page). */
  line-height: 1.2;
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
/* Autofit ladder — each step strictly tighter than the base 1.2. */
.fit-1 { line-height: 1.16; }
.fit-2 { line-height: 1.14; font-size: calc(var(--fs) - 1pt); }
.fit-3 { line-height: 1.12; font-size: calc(var(--fs) - 2pt); }
.fit-4 { line-height: 1.10; font-size: calc(var(--fs) - 3pt); }
.fit-5 { line-height: 1.08; font-size: calc(var(--fs) - 4pt); }
/* fit-wrap is the last resort: prefer breaking at word boundaries first
   (overflow-wrap: break-word). Only if a single Devanagari word is longer
   than its cell will the browser fall back to mid-word breaks — and at
   that point fit-5 has already shrunk the font, so cases that hit this
   are rare. The previous overflow-wrap:anywhere was too aggressive and
   produced ugly mid-syllable breaks like "अनुक्रमणि / का" and split
   Latin names like "Rajiv / Sharma" even when a whole-name break would fit. */
.fit-wrap { word-break: normal; overflow-wrap: break-word; }
"""

_AUTOFIT_JS = (
    """
(function(){
  /* ── Step 1: autofit ladder ──
     Triggers on EITHER vertical or horizontal overflow. Horizontal-overflow
     handling is what keeps Hindi-expanded table cells (e.g. an index row's
     "Annexure P/1" cell rendering as "संलग्नक P/1") from spilling into the
     adjacent column — fit-2/fit-3 shrink the font, fit-wrap allows mid-word
     breaks as a last resort. */
  var tiers = ['fit-1','fit-2','fit-3','fit-4','fit-5'];
  var blocks = document.querySelectorAll('.blk');
  function overflows(el) {
    return (el.scrollHeight > el.clientHeight + 1) ||
           (el.scrollWidth > el.clientWidth + 1);
  }
  for (var i = 0; i < blocks.length; i++) {
    var el = blocks[i];
    var step = 0;
    while (overflows(el) && step < tiers.length) {
      el.classList.add(tiers[step++]);
    }
    if (overflows(el)) {
      el.classList.add('fit-wrap');
    }
  }

  /* ── Step 2: vertical reflow for wrapped blocks ──
     When a text block wraps to more lines than its source bbox accommodated,
     it grows downward and visually crashes into the row below. Walk blocks in
     reading order; for each one, find any prior block (text OR separator)
     that overlaps it horizontally, and push the current block down so its top
     sits at least MIN_GAP below that prior block's rendered bottom.

     Separators stay anchored — they're table rules — but they DO act as
     collision sources. Otherwise a horizontal rule under a letterhead can end
     up visually crossing through an expanded Hindi line below it (the
     "strikethrough on email" artifact). */
  var MIN_GAP_PX = """
    + f"{_REFLOW_MIN_GAP_MM} * (96 / 25.4)"
    + """;
  var textBlocks = Array.prototype.slice.call(
    document.querySelectorAll('.blk:not(.separator)')
  );
  var separators = Array.prototype.slice.call(
    document.querySelectorAll('.blk.separator')
  );

  /* ── Row-mate grouping for table_cell blocks ──
     Cluster all table_cell blocks by their ORIGINAL style.top into rows
     (within ROW_THRESHOLD_MM). When the reflow pushes any cell down, we
     apply the same shift to every cell in its row so the s.no. column
     stays aligned with the content column. Without this, a wrapped cell
     in column B drifts down while column A's s.no. stays put, producing
     the "1, 2, 3" labels mis-pointing at the wrong content row. */
  var ROW_THRESHOLD_MM = 3.0;
  var cellBlocks = Array.prototype.slice.call(
    document.querySelectorAll('.blk[data-role="table_cell"]')
  );
  cellBlocks.sort(function(a, b) {
    return parseFloat(a.style.top) - parseFloat(b.style.top);
  });
  var rowMatesById = {};
  if (cellBlocks.length > 0) {
    var currentRow = [cellBlocks[0]];
    var rowAnchorTop = parseFloat(cellBlocks[0].style.top);
    function commitRow(row) {
      for (var c = 0; c < row.length; c++) {
        rowMatesById[row[c].dataset.id] = row;
      }
    }
    for (var i = 1; i < cellBlocks.length; i++) {
      var cellTopMm = parseFloat(cellBlocks[i].style.top);
      if (Math.abs(cellTopMm - rowAnchorTop) <= ROW_THRESHOLD_MM) {
        currentRow.push(cellBlocks[i]);
      } else {
        commitRow(currentRow);
        currentRow = [cellBlocks[i]];
        rowAnchorTop = cellTopMm;
      }
    }
    commitRow(currentRow);
  }

  textBlocks.sort(function(a, b) {
    return a.getBoundingClientRect().top - b.getBoundingClientRect().top;
  });
  function horizOverlap(a, b) {
    return !((a.right <= b.left + 1) || (b.right <= a.left + 1));
  }
  function applyShiftMm(el, shiftMm) {
    var currentTopMm = parseFloat(el.style.top) || 0;
    el.style.top = (currentTopMm + shiftMm).toFixed(2) + 'mm';
  }
  for (var i = 0; i < textBlocks.length; i++) {
    var cur = textBlocks[i];
    var curRect = cur.getBoundingClientRect();
    var maxBottom = -Infinity;
    for (var j = 0; j < i; j++) {
      var prev = textBlocks[j];
      var prevRect = prev.getBoundingClientRect();
      if (!horizOverlap(curRect, prevRect)) continue;
      if (prevRect.bottom > maxBottom) maxBottom = prevRect.bottom;
    }
    for (var k = 0; k < separators.length; k++) {
      var sep = separators[k];
      var sepRect = sep.getBoundingClientRect();
      if (!horizOverlap(curRect, sepRect)) continue;
      /* Two cases push the current block down:
         (a) separator is above the block (sepRect.bottom <= curRect.top)
             AND close enough that MIN_GAP isn't satisfied — handled by the
             same maxBottom logic below.
         (b) separator is INSIDE the block's rendered rect — i.e. the block
             expanded vertically (Hindi text wrapped to more lines) and now
             the rule is drawing through the block. Detect by checking
             vertical overlap: sepRect.top < curRect.bottom AND
             sepRect.bottom > curRect.top. */
      var vertOverlap = (sepRect.top < curRect.bottom) &&
                        (sepRect.bottom > curRect.top);
      var separatorAbove = sepRect.bottom <= curRect.top;
      if (!vertOverlap && !separatorAbove) continue;
      if (sepRect.bottom > maxBottom) maxBottom = sepRect.bottom;
    }
    if (maxBottom > -Infinity && curRect.top < maxBottom + MIN_GAP_PX) {
      var shiftPx = (maxBottom + MIN_GAP_PX) - curRect.top;
      var shiftMm = shiftPx / (96 / 25.4);
      /* If this is a table_cell that has row-mates, shift the whole row
         together. Otherwise just shift this block. */
      var mates = rowMatesById[cur.dataset.id];
      if (mates && mates.length > 1) {
        for (var m = 0; m < mates.length; m++) {
          applyShiftMm(mates[m], shiftMm);
        }
      } else {
        applyShiftMm(cur, shiftMm);
      }
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
    # Apply the Hindi bump only when we're actually rendering Devanagari
    # content — fallback-to-source (text_en) blocks keep their original size.
    fs = block.font_size_pt * _HINDI_FONT_SCALE if block.text_hi else block.font_size_pt

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
        f'data-role="{block.role.value}" data-id="{block.id}" '
        f'style="{style}">{body}</div>'
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
