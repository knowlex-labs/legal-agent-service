"""PDF → Document IR extraction using PyMuPDF dict mode.

Key invariants:
- One PyMuPDF block = one logical paragraph/bullet in the output (preserves multi-line bullets).
- Within a block, lines that share the same y-band and split left/right → RowBlock.
- Adjacent blocks from different PyMuPDF blocks that share y-band and split → RowBlock.
- Heading thresholds tuned for typical resume/legal font ratios (1.08× and 1.3×).
"""

from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass

from legal_agent.agents.translation.layout_ir import Document, Page, RowBlock, Span, TextBlock

logger = logging.getLogger(__name__)

_MIN_TEXT_CHARS_PER_PAGE = 50
_TOL_PT = 12.0       # ~2 Latin char widths; used for split-row and alignment inference
_BULLET_PREFIXES = ("•", "◦", "○", "–", "—", "-", "*", "·")

# Lines that are entirely an embedded image (markdown produced by some OCR backends).
# These are tens-of-kB base64 blobs — never translatable, and they trip Sarvam's 2000-char limit.
_IMG_EMBED_RE = __import__("re").compile(r"!\[[^\]]*\]\(data:[^)]+\)")

# Sarvam OCR (and similar vision OCR backends) emits prose descriptions for image regions
# that contain no readable text — letterhead emblems, stamps, logos, signatures. These
# descriptions ("There is no readable text in this image. It appears to be …") get treated
# as document content and translated, producing nonsense paragraphs in the output PDF.
_OCR_META_RE = __import__("re").compile(
    r"^\s*("
    r"there\s+is\s+no\s+(readable\s+)?(text|writing)\b"
    r"|this\s+image\s+(contains|shows|depicts|appears)"
    r"|the\s+image\s+(contains|shows|depicts|appears)"
    r"|(it|this)\s+appears\s+to\s+be\b"
    r"|in\s+(this|the)\s+image\b"
    r"|no\s+readable\s+(text|content)\b"
    r")",
    __import__("re").IGNORECASE,
)

# Gemini's HTML prompt emits placeholder tags like <em>[STAMP: ...]</em>,
# <em>[SEAL: ...]</em>, <em>[HANDWRITTEN: ...]</em>, [IMAGE/LOGO], [FIGURE: ...].
# These are not document content — drop them before translation.
_PLACEHOLDER_RE = __import__("re").compile(
    r"^\s*\[\s*(stamp|seal|image|logo|handwritten|figure|photo|picture|graphic|sign|emblem|signature)\b",
    __import__("re").IGNORECASE,
)

# Heading font-size thresholds (relative to median body size)
_H1_RATIO = 1.3
_H2_RATIO = 1.08

_PERCENTILE_LO = 5   # margin_left: 5th-pct of line x0 values
_PERCENTILE_HI = 95  # margin_right: 95th-pct of line x1 values


@dataclass
class _Item:
    """Intermediate item before final IR classification."""
    x0: float
    y0: float
    x1: float
    y1: float
    spans: list[Span]
    max_size: float = 0.0
    # If already classified as a RowBlock (within-block split), store it here.
    row: RowBlock | None = None


def _percentile(vals: list[float], p: float) -> float:
    if not vals:
        return 0.0
    sorted_vals = sorted(vals)
    idx = (p / 100) * (len(sorted_vals) - 1)
    lo, hi = int(idx), min(int(idx) + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (idx - lo)


def _infer_align(x0: float, x1: float, m_left: float, m_right: float) -> str:
    left_gap = x0 - m_left
    right_gap = m_right - x1
    if abs(left_gap - right_gap) < _TOL_PT and left_gap > _TOL_PT:
        return "center"
    if right_gap < _TOL_PT and left_gap > 2 * _TOL_PT:
        return "right"
    return "left"


def _is_bullet(text: str) -> bool:
    t = text.lstrip()
    return any(t.startswith(p) for p in _BULLET_PREFIXES)


def _strip_bullet_prefix(spans: list[Span]) -> list[Span]:
    """Remove the leading bullet character from the first span."""
    if not spans:
        return spans
    first = spans[0]
    stripped = first.text.lstrip()
    for prefix in _BULLET_PREFIXES:
        if stripped.startswith(prefix):
            stripped = stripped[len(prefix):].lstrip()
            break
    rest = spans[1:]
    if not stripped:
        return rest  # first span was only the bullet char — skip it
    return [Span(text=stripped, bold=first.bold, italic=first.italic)] + rest


def _classify(item: _Item, m_left: float, m_right: float, median_size: float) -> TextBlock:
    full_text = "".join(s.text for s in item.spans)
    align = _infer_align(item.x0, item.x1, m_left, m_right)

    if median_size:
        if item.max_size >= _H1_RATIO * median_size:
            return TextBlock(type="heading", level=1, align=align, spans=item.spans)
        if item.max_size >= _H2_RATIO * median_size:
            return TextBlock(type="heading", level=2, align=align, spans=item.spans)

    if _is_bullet(full_text):
        return TextBlock(type="bullet", align="left", spans=_strip_bullet_prefix(item.spans))

    return TextBlock(type="paragraph", align=align, spans=item.spans)


def _y_center(it: _Item) -> float:
    return (it.y0 + it.y1) / 2


def _y_overlaps(a: _Item, b: _Item, tol: float = 6.0) -> bool:
    """Use center-based comparison so large-font lines don't bleed into the next row."""
    return abs(_y_center(a) - _y_center(b)) < tol


def _y_range_overlaps(a: _Item, b: _Item, gap: float = 3.0) -> bool:
    """True if the vertical extents of a and b overlap or are within gap pt of each other.

    Used for cross-block grouping: items whose bboxes touch/overlap share a visual row
    even if their centers differ due to varying icon heights. A physical gap of 3pt still
    keeps consecutive body-text lines (whose bboxes don't overlap) in separate groups.
    """
    return a.y0 <= b.y1 + gap and b.y0 <= a.y1 + gap


def _is_split(left_items: list[_Item], right_items: list[_Item]) -> bool:
    if not left_items or not right_items:
        return False
    return max(it.x1 for it in left_items) < min(it.x0 for it in right_items)


def _merge_spans(items: list[_Item]) -> tuple[list[Span], float]:
    """Concatenate spans from multiple items; return (spans, max_size).

    Inserts a space between items when PyMuPDF's line-break strips it,
    so multi-line bullets don't produce 'enablingnon-technical'.
    """
    spans: list[Span] = []
    max_sz = 0.0
    for it in items:
        if spans and it.spans:
            last_text = spans[-1].text
            first_text = it.spans[0].text
            if not last_text.endswith(" ") and not first_text.startswith(" "):
                spans.append(Span(text=" "))
        spans.extend(it.spans)
        max_sz = max(max_sz, it.max_size)
    return spans, max_sz


def _process_block(block: dict, page_width: float) -> list[_Item]:
    """Convert one PyMuPDF text block into intermediate _Items.

    Lines within the block that share a y-band and form a two-column split
    become a single _Item with `row` set. Sequential non-split lines are
    accumulated into one _Item (preserving multi-line bullets/paragraphs).
    """
    lines = block.get("lines", [])
    if not lines:
        return []

    # Build per-line _Items from raw spans
    line_items: list[_Item] = []
    for line in lines:
        spans: list[Span] = []
        max_sz = 0.0
        for s in line.get("spans", []):
            txt = s.get("text", "")
            if not txt:
                continue
            flags = s.get("flags", 0)
            spans.append(Span(
                text=txt,
                bold=bool(flags & (1 << 4)),
                italic=bool(flags & (1 << 1)),
            ))
            max_sz = max(max_sz, s.get("size", 0.0))
        if not spans:
            continue
        bb = line["bbox"]
        line_items.append(_Item(x0=bb[0], y0=bb[1], x1=bb[2], y1=bb[3],
                                spans=spans, max_size=max_sz))

    if not line_items:
        return []

    # Group line_items that share the same visual row (center-based, 6pt tolerance).
    # Center-based avoids larger-font lines bleeding into the next row via bbox overlap.
    y_groups: list[list[_Item]] = []
    for li in line_items:
        placed = False
        for group in reversed(y_groups):
            gc = sum((it.y0 + it.y1) / 2 for it in group) / len(group)
            if abs((li.y0 + li.y1) / 2 - gc) < 6.0:
                group.append(li)
                placed = True
                break
        if not placed:
            y_groups.append([li])

    result: list[_Item] = []
    pending: list[_Item] = []  # sequential non-split line_items → one TextBlock

    def flush_pending() -> None:
        if not pending:
            return
        spans, max_sz = _merge_spans(pending)
        result.append(_Item(
            x0=min(it.x0 for it in pending),
            y0=min(it.y0 for it in pending),
            x1=max(it.x1 for it in pending),
            y1=max(it.y1 for it in pending),
            spans=spans,
            max_size=max_sz,
        ))
        pending.clear()

    for group in y_groups:
        if len(group) >= 2:
            left = [it for it in group if it.x1 <= page_width / 2 + _TOL_PT]
            right = [it for it in group if it.x0 >= page_width / 2 - _TOL_PT]
            if _is_split(left, right):
                flush_pending()
                l_spans, _ = _merge_spans(left)
                r_spans, _ = _merge_spans(right)
                item = _Item(
                    x0=min(it.x0 for it in group),
                    y0=min(it.y0 for it in group),
                    x1=max(it.x1 for it in group),
                    y1=max(it.y1 for it in group),
                    spans=[],  # not used for row
                    row=RowBlock(left=l_spans, right=r_spans),
                )
                result.append(item)
                continue
        # Not a split row — accumulate into the pending TextBlock
        for it in group:
            pending.append(it)

    flush_pending()
    return result


def _median_font_size(data: bytes) -> float:
    import fitz
    doc = fitz.open(stream=data, filetype="pdf")
    sizes: list[float] = []
    for page in doc:
        for block in page.get_text("dict").get("blocks", []):
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    sz = span.get("size", 0.0)
                    if sz > 0:
                        sizes.append(sz)
    doc.close()
    return statistics.median(sizes) if sizes else 11.0


def _extract_page(page, median_size: float) -> list[TextBlock | RowBlock]:
    page_dict = page.get_text("dict", sort=True)
    page_width = page_dict["width"]

    all_items: list[_Item] = []
    all_x0: list[float] = []
    all_x1: list[float] = []

    for block in page_dict.get("blocks", []):
        if block.get("type") != 0:
            continue
        items = _process_block(block, page_width)
        all_items.extend(items)
        for it in items:
            if it.row is None:  # row items have degenerate bboxes
                all_x0.append(it.x0)
                all_x1.append(it.x1)

    if not all_items:
        return []

    m_left = _percentile(all_x0, _PERCENTILE_LO)
    m_right = _percentile(all_x1, _PERCENTILE_HI)

    # Cross-block split-row detection: group adjacent text _Items by y-overlap,
    # check if they form a two-column layout spanning different PyMuPDF blocks.
    groups: list[list[_Item]] = []
    for it in all_items:
        placed = False
        for group in reversed(groups):
            last = group[-1]
            if _y_range_overlaps(it, last):
                group.append(it)
                placed = True
                break
        if not placed:
            groups.append([it])

    result: list[TextBlock | RowBlock] = []
    for group in groups:
        # Already-classified row items pass through
        row_items = [it for it in group if it.row is not None]
        text_items = [it for it in group if it.row is None]

        for ri in row_items:
            result.append(ri.row)  # type: ignore[arg-type]

        if not text_items:
            continue

        if len(text_items) >= 2:
            left = [it for it in text_items if it.x1 <= page_width / 2 + _TOL_PT]
            right = [it for it in text_items if it.x0 >= page_width / 2 - _TOL_PT]
            if _is_split(left, right):
                l_spans, _ = _merge_spans(left)
                r_spans, _ = _merge_spans(right)
                result.append(RowBlock(left=l_spans, right=r_spans))
                continue
            # Multiple items on the same visual row but not a two-column split (e.g. three
            # inline social links) — merge into one block so they render on a single line.
            spans, max_sz = _merge_spans(text_items)
            merged = _Item(
                x0=min(it.x0 for it in text_items),
                y0=min(it.y0 for it in text_items),
                x1=max(it.x1 for it in text_items),
                y1=max(it.y1 for it in text_items),
                spans=spans,
                max_size=max_sz,
            )
            result.append(_classify(merged, m_left, m_right, median_size))
            continue

        # Single item
        for it in text_items:
            result.append(_classify(it, m_left, m_right, median_size))

    return result


def extract_document(data: bytes, *, ocr_language: str | None = None) -> Document:
    """Extract a Document IR from PDF bytes using PyMuPDF dict mode.

    `ocr_language` is a Sarvam-style BCP-47 hint (e.g. "hi-IN") passed to the
    OCR fallback when image-only pages are detected. None falls back to the
    `SARVAM_OCR_LANGUAGE` env default.
    """
    import fitz

    if data[:4] != b"%PDF":
        raise ValueError("Input is not a PDF")

    median_size = _median_font_size(data)
    doc = fitz.open(stream=data, filetype="pdf")

    # Pre-scan: figure out which pages have usable native text vs need OCR.
    # OCR returns the full document as one blob with no page boundaries, so we
    # run it once and dump all OCR'd blocks onto the first OCR-needed page.
    page_char_counts: list[int] = []
    for page in doc:
        page_dict = page.get_text("dict", sort=True)
        page_char_counts.append(sum(
            len(s.get("text", ""))
            for b in page_dict.get("blocks", [])
            if b.get("type") == 0
            for ln in b.get("lines", [])
            for s in ln.get("spans", [])
        ))

    ocr_pages = [i for i, n in enumerate(page_char_counts) if n < _MIN_TEXT_CHARS_PER_PAGE]
    ocr_blocks: list[TextBlock | RowBlock] = []
    if ocr_pages:
        logger.info(
            "Pages %s have <%d chars — routing to OCR (single pass)",
            ocr_pages, _MIN_TEXT_CHARS_PER_PAGE,
        )
        ocr_blocks = _ocr_fallback_blocks(data, language=ocr_language)

    first_ocr_idx = ocr_pages[0] if ocr_pages else None
    pages: list[Page] = []
    for idx, page in enumerate(doc):
        if idx in ocr_pages:
            blocks = ocr_blocks if idx == first_ocr_idx else []
        else:
            blocks = _extract_page(page, median_size)
        pages.append(Page(
            width_pt=page.rect.width,
            height_pt=page.rect.height,
            blocks=blocks,
        ))

    doc.close()
    return Document(pages=pages)


def _ocr_fallback_blocks(
    data: bytes,
    *,
    language: str | None = None,
) -> list[TextBlock | RowBlock]:
    """OCR the whole PDF (Sarvam Vision) and produce IR blocks.

    Asks the OCR backend for HTML output so structural tags (`<h1>`, `<p>`,
    `<li>`, `<table>`) and image regions (`<img>`, `<figure>`) are exposed
    discretely — letting us drop image regions cleanly instead of regex-filtering
    image-description prose. Falls back to markdown line-parsing if HTML parsing
    yields nothing (e.g. cached doc from before the format switch).
    """
    try:
        from legal_agent.utils.ocr import ocr_pdf
        text = ocr_pdf(data, output_format="html", provider="gemini", language=language)
    except Exception as exc:
        logger.warning("OCR fallback failed: %s", exc)
        return []

    if "<" in text and ">" in text:
        blocks = _html_to_blocks(text)
        if blocks:
            return blocks
        logger.warning("OCR returned HTML but yielded no blocks; falling back to line-parsing")

    return _markdown_lines_to_blocks(text)


def _html_to_blocks(html: str) -> list[TextBlock | RowBlock]:
    """Parse Sarvam Vision HTML output into Translation IR blocks.

    Drops `<img>` and `<figure>` outright — that's the whole reason we ask for
    HTML in the first place. Tables render as one paragraph per row with `|`
    separators (good enough for translation; revisit if a customer doc needs
    real table layout).
    """
    from html.parser import HTMLParser

    blocks: list[TextBlock | RowBlock] = []

    class _Builder(HTMLParser):
        def __init__(self) -> None:
            super().__init__(convert_charrefs=True)
            self._stack: list[str] = []
            self._buf: list[str] = []
            self._skip_depth = 0  # inside <img>/<figure>/<svg>/<script>/<style>
            self._cell_buf: list[str] = []
            self._row_buf: list[str] = []
            self._in_cell = False
            self._in_row = False

        def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
            if tag in ("img", "figure", "svg", "script", "style"):
                self._skip_depth += 1
                return
            if self._skip_depth:
                return
            if tag == "tr":
                self._in_row = True
                self._row_buf = []
            elif tag in ("td", "th"):
                self._in_cell = True
                self._cell_buf = []
            elif tag in ("h1", "h2", "h3", "h4", "h5", "h6", "p", "li"):
                self._flush_text()
                self._stack.append(tag)
                self._buf = []

        def handle_endtag(self, tag: str) -> None:
            if tag in ("img", "figure", "svg", "script", "style"):
                self._skip_depth = max(0, self._skip_depth - 1)
                return
            if self._skip_depth:
                return
            if tag in ("td", "th"):
                self._row_buf.append("".join(self._cell_buf).strip())
                self._cell_buf = []
                self._in_cell = False
                return
            if tag == "tr":
                row_text = " | ".join(c for c in self._row_buf if c)
                if row_text and not _OCR_META_RE.match(row_text) and not _PLACEHOLDER_RE.match(row_text):
                    blocks.append(TextBlock(type="paragraph", spans=[Span(text=row_text)]))
                self._row_buf = []
                self._in_row = False
                return
            if self._stack and self._stack[-1] == tag:
                self._stack.pop()
                self._flush_text(closing=tag)

        def handle_data(self, data: str) -> None:
            if self._skip_depth:
                return
            if self._in_cell:
                self._cell_buf.append(data)
            else:
                self._buf.append(data)

        def _flush_text(self, closing: str | None = None) -> None:
            text = "".join(self._buf).strip()
            self._buf = []
            if not text or _OCR_META_RE.match(text) or _PLACEHOLDER_RE.match(text):
                return
            if closing in ("h1", "h2", "h3", "h4", "h5", "h6"):
                level = 1 if closing == "h1" else 2
                blocks.append(TextBlock(type="heading", level=level, spans=[Span(text=text)]))
            elif closing == "li":
                blocks.append(TextBlock(type="bullet", spans=[Span(text=text)]))
            elif closing == "p" or closing is None:
                blocks.append(TextBlock(type="paragraph", spans=[Span(text=text)]))

    builder = _Builder()
    builder.feed(html)
    return blocks


def _markdown_lines_to_blocks(text: str) -> list[TextBlock | RowBlock]:
    """Fallback parser for markdown OCR output (pre-HTML cached docs)."""
    text = _IMG_EMBED_RE.sub("", text)
    blocks: list[TextBlock | RowBlock] = []
    paragraph_buf: list[str] = []

    def flush_paragraph() -> None:
        if paragraph_buf:
            blocks.append(TextBlock(
                type="paragraph",
                spans=[Span(text=" ".join(paragraph_buf))],
            ))
            paragraph_buf.clear()

    for line in text.splitlines():
        line = line.strip()
        if not line:
            flush_paragraph()
            continue
        if line.startswith("#"):
            flush_paragraph()
            hashes = len(line) - len(line.lstrip("#"))
            heading_text = line.lstrip("#").strip()
            if heading_text and not _OCR_META_RE.match(heading_text) and not _PLACEHOLDER_RE.match(heading_text):
                blocks.append(TextBlock(
                    type="heading",
                    level=1 if hashes <= 1 else 2,
                    spans=[Span(text=heading_text)],
                ))
            continue
        if _is_bullet(line):
            flush_paragraph()
            for prefix in _BULLET_PREFIXES:
                if line.startswith(prefix):
                    line = line[len(prefix):].lstrip()
                    break
            if _OCR_META_RE.match(line) or _PLACEHOLDER_RE.match(line):
                continue
            blocks.append(TextBlock(type="bullet", spans=[Span(text=line)]))
            continue
        if _OCR_META_RE.match(line) or _PLACEHOLDER_RE.match(line):
            flush_paragraph()
            continue
        paragraph_buf.append(line)

    flush_paragraph()
    return blocks
