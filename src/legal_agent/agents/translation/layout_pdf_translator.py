"""Layout-preserving PDF translation for born-digital PDFs.

Extracts text blocks with their bounding boxes, translates plain text only,
then reinserts the translated text into the original rectangles. Page
geometry, drawings, images, and links are kept intact.

Falls back to the markdown renderer for scanned PDFs (detected by
`is_layout_translation_viable`).
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from io import BytesIO
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from legal_agent.models.documents import TranslationLanguage

logger = logging.getLogger(__name__)


_MIN_SPANS_FIRST_PAGE = 8
_BLOCK_LINE_GAP_RATIO = 0.8
_SYMBOL_ONLY_RE = re.compile(r"^[\s\W_]+$")
_MIN_TRANSLATE_CHARS = 2
_SARVAM_CONCURRENCY = 8


@dataclass
class _Span:
    text: str
    bbox: tuple[float, float, float, float]
    size: float
    flags: int
    color: int


@dataclass
class _Line:
    bbox: tuple[float, float, float, float]
    spans: list[_Span]
    text: str = ""


@dataclass
class _Block:
    page_index: int
    bbox: tuple[float, float, float, float]
    text: str
    size: float
    bold: bool
    italic: bool
    color: int
    align: str = "left"
    translated: str | None = field(default=None)


def is_layout_translation_viable(pdf_bytes: bytes) -> bool:
    """Return True if the first page yields enough text spans to translate.

    Scanned/image-only PDFs trip this and callers fall back to markdown.
    """
    if not pdf_bytes or not pdf_bytes.startswith(b"%PDF"):
        return False
    try:
        import fitz
    except Exception as exc:
        logger.warning(f"PyMuPDF unavailable for layout viability check: {exc}")
        return False
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:
        logger.warning(f"Could not open PDF for viability check: {exc}")
        return False
    try:
        if doc.page_count == 0:
            return False
        page = doc[0]
        info = page.get_text("dict")
        span_count = sum(
            1
            for block in info.get("blocks", [])
            if block.get("type", 0) == 0
            for line in block.get("lines", [])
            for span in line.get("spans", [])
            if (span.get("text") or "").strip()
        )
        return span_count >= _MIN_SPANS_FIRST_PAGE
    finally:
        doc.close()


def _extract_blocks(doc) -> list[_Block]:
    blocks: list[_Block] = []
    for page_index, page in enumerate(doc):
        info = page.get_text("dict")
        for raw_block in info.get("blocks", []):
            if raw_block.get("type", 0) != 0:
                continue
            lines = _build_lines(raw_block.get("lines", []))
            for grouped in _group_lines_into_paragraphs(lines):
                block = _line_group_to_block(grouped, page_index, page.rect.width)
                if block:
                    blocks.append(block)
    return blocks


def _spans_to_marked_text(spans: list[_Span]) -> str:
    """Join spans into a single string, wrapping bold/italic runs with ** / *.

    Consecutive spans sharing the same style are grouped before wrapping so
    Sarvam sees natural words rather than `**word****word**` fragments.
    Sarvam-Translate preserves these markers through translation.
    """
    if not spans:
        return ""
    parts: list[str] = []
    i = 0
    while i < len(spans):
        span = spans[i]
        bold = bool(span.flags & 16)
        italic = bool(span.flags & 2) and not bold
        group = span.text
        j = i + 1
        while j < len(spans):
            ns = spans[j]
            nb = bool(ns.flags & 16)
            ni = bool(ns.flags & 2) and not nb
            if nb == bold and ni == italic:
                group += ns.text
                j += 1
            else:
                break
        stripped = group.strip()
        if stripped:
            leading = group[: len(group) - len(group.lstrip())]
            trailing = group[len(group.rstrip()):]
            if bold:
                parts.append(f"{leading}**{stripped}**{trailing}")
            elif italic:
                parts.append(f"{leading}*{stripped}*{trailing}")
            else:
                parts.append(group)
        else:
            parts.append(group)
        i = j
    return "".join(parts)


def _build_lines(raw_lines: list[dict]) -> list[_Line]:
    lines: list[_Line] = []
    for raw_line in raw_lines:
        raw_spans = raw_line.get("spans", []) or []
        spans: list[_Span] = []
        for raw_span in raw_spans:
            text = raw_span.get("text") or ""
            if not text:
                continue
            spans.append(
                _Span(
                    text=text,
                    bbox=tuple(raw_span.get("bbox", (0, 0, 0, 0))),  # type: ignore[arg-type]
                    size=float(raw_span.get("size", 10.0)),
                    flags=int(raw_span.get("flags", 0)),
                    color=int(raw_span.get("color", 0)),
                )
            )
        if not spans:
            continue
        bbox = tuple(raw_line.get("bbox", (0, 0, 0, 0)))  # type: ignore[arg-type]
        text = _spans_to_marked_text(spans)
        if not text.strip():
            continue
        lines.append(_Line(bbox=bbox, spans=spans, text=text))
    return lines


def _group_lines_into_paragraphs(lines: list[_Line]) -> list[list[_Line]]:
    groups: list[list[_Line]] = []
    current: list[_Line] = []
    for line in lines:
        if not current:
            current = [line]
            continue
        prev = current[-1]
        prev_height = max(prev.bbox[3] - prev.bbox[1], 1.0)
        gap = line.bbox[1] - prev.bbox[3]
        if gap <= prev_height * _BLOCK_LINE_GAP_RATIO:
            current.append(line)
        else:
            groups.append(current)
            current = [line]
    if current:
        groups.append(current)
    return groups


def _line_group_to_block(
    group: list[_Line], page_index: int, page_width: float
) -> _Block | None:
    if not group:
        return None
    text = " ".join(line.text for line in group).strip()
    text = re.sub(r"\s+", " ", text)
    if not text:
        return None

    x0 = min(line.bbox[0] for line in group)
    y0 = min(line.bbox[1] for line in group)
    x1 = max(line.bbox[2] for line in group)
    y1 = max(line.bbox[3] for line in group)

    spans = [span for line in group for span in line.spans]
    primary = max(spans, key=lambda s: len(s.text))
    # Bold/italic are now carried as **/** markers in block.text so Sarvam
    # receives and preserves them. Block-level weight is always plain; the
    # renderer converts markers to <strong>/<em> inline HTML.
    bold = False
    italic = False

    align = "left"
    if page_width > 0:
        left_margin = x0
        right_margin = page_width - x1
        width = x1 - x0
        if width < page_width * 0.6:
            if right_margin < page_width * 0.08 and left_margin > page_width * 0.2:
                align = "right"
            elif (
                abs(left_margin - right_margin) < page_width * 0.05
                and left_margin > page_width * 0.15
            ):
                align = "center"

    return _Block(
        page_index=page_index,
        bbox=(x0, y0, x1, y1),
        text=text,
        size=primary.size,
        bold=bold,
        italic=italic,
        color=primary.color,
        align=align,
    )


async def _translate_blocks(
    blocks: list[_Block],
    source_code: str,
    target_code: str,
    api_key: str,
) -> None:
    from legal_agent.agents.translation.generator import (
        _call_sarvam_translate,
        _split_for_sarvam,
    )

    sem = asyncio.Semaphore(_SARVAM_CONCURRENCY)

    async def _translate_one(block: _Block) -> None:
        text = block.text
        if not text or len(text) < _MIN_TRANSLATE_CHARS:
            block.translated = text
            return
        if _SYMBOL_ONLY_RE.match(text):
            block.translated = text
            return

        async with sem:
            try:
                chunks = _split_for_sarvam(text)
                if len(chunks) == 1:
                    block.translated = await _call_sarvam_translate(
                        chunks[0], source_code, target_code, api_key
                    )
                    return
                translated_chunks = await asyncio.gather(*[
                    _call_sarvam_translate(chunk, source_code, target_code, api_key)
                    for chunk in chunks
                ])
                block.translated = " ".join(translated_chunks)
            except Exception as exc:
                logger.warning(
                    f"[layout] Block translation failed on page {block.page_index + 1} "
                    f"({len(text)} chars): {exc}"
                )
                block.translated = text

    await asyncio.gather(*[_translate_one(block) for block in blocks])


_BLOCK_CSS_TEMPLATE = """
* {{ margin: 0; padding: 0; }}
body {{
  font-family: "Noto Sans Devanagari", "Noto Sans Bengali", "Noto Sans Tamil",
               "Noto Sans Telugu", "Noto Sans Gujarati", "Noto Sans Kannada",
               "Noto Sans Malayalam", "Noto Sans Gurmukhi", "Noto Sans Oriya",
               "Noto Sans Arabic", "Noto Sans", "Kohinoor Devanagari",
               "Arial Unicode MS", "Helvetica", sans-serif;
  font-size: {size:.2f}pt;
  color: {color};
  line-height: 1.15;
  text-align: {align};
}}
.body {{ font-weight: normal; font-style: normal; }}
strong {{ font-weight: bold; }}
em {{ font-style: italic; }}
"""


def _color_int_to_css(color_int: int) -> str:
    if color_int <= 0:
        return "#000000"
    try:
        r = (color_int >> 16) & 0xFF
        g = (color_int >> 8) & 0xFF
        b = color_int & 0xFF
        return f"#{r:02x}{g:02x}{b:02x}"
    except Exception:
        return "#000000"


def _md_to_html_inline(text: str) -> str:
    """Escape HTML special chars then convert **bold** / *italic* to inline tags.

    Order matters: escape first so literal < and & in translated text become
    safe entities, then wrap ** / * markers in <strong> / <em> tags which are
    intentional HTML, not user-supplied markup.
    """
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text, flags=re.DOTALL)
    text = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<em>\1</em>", text, flags=re.DOTALL)
    return text


def _insert_block_text(page, block: _Block) -> bool:
    text = (block.translated or "").strip()
    if not text:
        return True

    try:
        import fitz
    except Exception:
        return False

    css = _BLOCK_CSS_TEMPLATE.format(
        size=block.size,
        color=_color_int_to_css(block.color),
        align=block.align,
    )
    html_body = f'<div class="body">{_md_to_html_inline(text)}</div>'

    rect = fitz.Rect(*block.bbox)
    # 2pt flat pad prevents Devanagari descenders from clipping without
    # creating visible gaps between blocks (old 25%-of-font-size was too much).
    rect = fitz.Rect(rect.x0, rect.y0, rect.x1, rect.y1 + 2.0)

    try:
        page.insert_htmlbox(rect, html_body, css=css)
        return True
    except Exception as exc:
        logger.warning(
            f"[layout] insert_htmlbox failed on page {block.page_index + 1}: {exc}"
        )
        return False


def _redact_blocks(page, blocks: list[_Block]) -> None:
    try:
        import fitz
    except Exception:
        return

    for block in blocks:
        rect = fitz.Rect(*block.bbox)
        # White-fill redaction removes the underlying text glyphs *and* paints
        # the box, leaving drawings/images/links untouched.
        page.add_redact_annot(rect, fill=(1, 1, 1))
    try:
        page.apply_redactions()
    except Exception as exc:
        logger.warning(f"[layout] apply_redactions failed: {exc}")


def _render_translated_pdf(doc, blocks: list[_Block]) -> bytes:
    by_page: dict[int, list[_Block]] = {}
    for block in blocks:
        by_page.setdefault(block.page_index, []).append(block)

    for page_index, page_blocks in by_page.items():
        page = doc[page_index]
        _redact_blocks(page, page_blocks)
        for block in page_blocks:
            _insert_block_text(page, block)

    # Subset embedded fonts to only the glyphs actually used, then save with
    # garbage=4 so duplicate font streams from per-block insert_htmlbox calls
    # collapse to one copy. Single pass only; repeated calls corrupt output
    # (PyMuPDF #4727).
    try:
        doc.subset_fonts()
    except Exception as exc:
        logger.warning(f"[layout] subset_fonts skipped: {exc}")

    buf = BytesIO()
    doc.save(buf, garbage=4, deflate=True)
    return buf.getvalue()


async def translate_pdf_layout(
    pdf_bytes: bytes,
    target_language: "TranslationLanguage",
    source_language: "TranslationLanguage | None",
    api_key: str,
) -> bytes:
    """Translate a born-digital PDF while preserving layout.

    Raises on PyMuPDF/Sarvam failure — callers fall back to the markdown path.
    """
    import fitz
    from legal_agent.agents.translation.generator import _SARVAM_LANG_CODES

    target_code = _SARVAM_LANG_CODES.get(target_language.value, "hi-IN")
    source_code = (
        _SARVAM_LANG_CODES.get(source_language.value, "en-IN")
        if source_language
        else "en-IN"
    )

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        blocks = await asyncio.to_thread(_extract_blocks, doc)
        if not blocks:
            raise RuntimeError("No translatable text blocks found in PDF")

        logger.info(
            f"[layout] Translating {len(blocks)} blocks across "
            f"{doc.page_count} page(s) to {target_language.value}"
        )

        await _translate_blocks(blocks, source_code, target_code, api_key)

        pdf_out = await asyncio.to_thread(_render_translated_pdf, doc, blocks)
    finally:
        doc.close()

    logger.info(
        f"[layout] Rendered translated PDF: {len(pdf_out)} bytes "
        f"({len(blocks)} blocks)"
    )
    return pdf_out
