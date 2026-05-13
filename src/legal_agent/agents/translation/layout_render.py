"""Document IR → HTML renderer for the translation pipeline.

Produces flow HTML (not absolute-positioned) so Hindi text expansion doesn't
cause collisions. Flexbox split rows keep left/right columns aligned after
Devanagari text grows ~30% wider than its English source.
"""

from __future__ import annotations

import html as _html

from legal_agent.agents.translation.layout_ir import Document, Page, RowBlock, Span, TextBlock

_SOUTH_INDIC: dict[str, str] = {
    "tamil": "Noto Sans Tamil",
    "telugu": "Noto Sans Telugu",
    "kannada": "Noto Sans Kannada",
    "malayalam": "Noto Sans Malayalam",
    "gujarati": "Noto Sans Gujarati",
    "punjabi": "Noto Sans Gurmukhi",
    "odia": "Noto Sans Oriya",
    "bengali": "Noto Sans Bengali",
    "assamese": "Noto Sans Bengali",
}
_DEVANAGARI_LANGS = {
    "hindi", "marathi", "nepali", "sanskrit", "maithili", "dogri", "konkani", "bodo", "manipuri",
}
_RTL_LANGS = {"urdu", "sindhi", "kashmiri"}

_LANG_CODES: dict[str, str] = {
    "hindi": "hi", "bengali": "bn", "telugu": "te", "marathi": "mr",
    "tamil": "ta", "urdu": "ur", "gujarati": "gu", "kannada": "kn",
    "malayalam": "ml", "odia": "or", "punjabi": "pa", "assamese": "as",
    "nepali": "ne", "sanskrit": "sa",
}


def _noto_family(lang: str) -> str:
    lang = lang.lower()
    if lang in _SOUTH_INDIC:
        return f"'{_SOUTH_INDIC[lang]}', 'Noto Sans', sans-serif"
    if lang in _RTL_LANGS:
        return "'Noto Nastaliq Urdu', 'Noto Kufi Arabic', sans-serif"
    if lang in _DEVANAGARI_LANGS:
        return "'Noto Sans Devanagari', 'Noto Serif Devanagari', sans-serif"
    return "'Noto Sans', sans-serif"


def _render_spans(spans: list[Span]) -> str:
    parts: list[str] = []
    for span in spans:
        text = _html.escape(span.text)
        if span.bold and span.italic:
            text = f"<strong><em>{text}</em></strong>"
        elif span.bold:
            text = f"<strong>{text}</strong>"
        elif span.italic:
            text = f"<em>{text}</em>"
        parts.append(text)
    return "".join(parts)


def _render_block(block: TextBlock | RowBlock) -> str:
    if isinstance(block, RowBlock):
        left = _render_spans(block.left)
        right = _render_spans(block.right)
        return f'<div class="row"><div class="col-left">{left}</div><div class="col-right">{right}</div></div>\n'

    # TextBlock
    inner = _render_spans(block.spans)
    if block.type == "heading":
        tag = "h1" if block.level <= 1 else "h2"
        cls = f' class="{block.align}"' if block.align != "left" else ""
        return f"<{tag}{cls}>{inner}</{tag}>\n"
    if block.type == "bullet":
        return f"<li>{inner}</li>\n"

    # paragraph
    cls_parts = []
    if block.align == "center":
        cls_parts.append("center")
    elif block.align == "right":
        cls_parts.append("right")
    cls = f' class="{" ".join(cls_parts)}"' if cls_parts else ""
    return f"<p{cls}>{inner}</p>\n"


def _render_page(page: Page) -> str:
    parts: list[str] = []
    in_list = False

    for block in page.blocks:
        if isinstance(block, TextBlock) and block.type == "bullet":
            if not in_list:
                parts.append("<ul>\n")
                in_list = True
        else:
            if in_list:
                parts.append("</ul>\n")
                in_list = False

        parts.append(_render_block(block))

    if in_list:
        parts.append("</ul>\n")

    return "".join(parts)


def render_to_html(doc: Document, lang: str) -> str:
    """Render a translated Document IR to a complete HTML document."""
    lang_lower = lang.lower()
    lang_code = _LANG_CODES.get(lang_lower, "hi")
    noto = _noto_family(lang_lower)
    direction = "rtl" if lang_lower in _RTL_LANGS else "ltr"

    css = f"""
@page {{ size: A4; margin: 1.8cm 1.6cm; }}
* {{ box-sizing: border-box; }}
body {{
  font-family: {noto};
  font-feature-settings: "kern" 1, "liga" 1, "calt" 1;
  line-height: 1.55;
  color: #111;
  direction: {direction};
}}
h1 {{ font-size: 22pt; font-weight: 700; margin: 0 0 4pt; }}
h2 {{
  font-size: 12pt; font-weight: 700;
  text-transform: uppercase; letter-spacing: 0.5pt;
  border-bottom: 1px solid #999; margin: 14pt 0 6pt;
}}
h1.center, h2.center {{ text-align: center; border-bottom: 0; }}
h1.right,  h2.right  {{ text-align: right; }}
p {{ margin: 3pt 0; }}
p.center {{ text-align: center; }}
p.right  {{ text-align: right; }}
.row {{
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  gap: 1em;
  margin: 1pt 0;
}}
.col-left  {{ flex: 1 1 auto; }}
.col-right {{ flex: 0 0 auto; white-space: nowrap; text-align: right; }}
ul {{ padding-left: 1.4em; margin: 4pt 0 8pt 0; }}
li {{ margin-bottom: 5pt; line-height: 1.6; }}
"""

    page_divs = []
    for i, page in enumerate(doc.pages):
        page_html = _render_page(page)
        # page-break between pages (last page has no break)
        pb = "" if i == len(doc.pages) - 1 else ' style="page-break-after:always"'
        page_divs.append(f'<div{pb}>\n{page_html}</div>')

    body = "\n".join(page_divs)
    return (
        f'<!DOCTYPE html>\n<html lang="{lang_code}" dir="{direction}">\n'
        f'<head>\n<meta charset="utf-8"/>\n<style>{css}</style>\n</head>\n'
        f'<body>\n{body}\n</body>\n</html>'
    )
