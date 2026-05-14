"""Document IR → HTML renderer for the translation pipeline.

Produces flow HTML (not absolute-positioned) so Hindi text expansion doesn't
cause collisions. Flexbox split rows keep left/right columns aligned after
Devanagari text grows ~30% wider than its English source.
"""

from __future__ import annotations

import html as _html

from legal_agent.agents.translation.layout_ir import Document, ImageBlock, Page, RowBlock, Span, TextBlock

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

# Typography for scanned-PDF structured vision output (.vt-* inside .vision-structured).
_VISION_STRUCTURED_CSS = """
/* Structured vision translation blocks */
.vision-structured {
  margin-bottom: 4pt;
}
.vision-structured .vt-block {
  margin: 0;
  text-indent: 0;
}
/* Size scale */
.vision-structured .vt-sz-xs { font-size: 8.5pt; }
.vision-structured .vt-sz-small { font-size: 9.75pt; }
.vision-structured .vt-sz-normal { font-size: 11pt; }
.vision-structured .vt-sz-large { font-size: 13pt; }
.vision-structured .vt-sz-xlarge { font-size: 15.5pt; }
/* Weight */
.vision-structured .vt-w-normal { font-weight: 400; }
.vision-structured .vt-w-semibold { font-weight: 600; }
.vision-structured .vt-w-bold { font-weight: 700; }
/* Line spacing */
.vision-structured .vt-lh-tight { line-height: 1.22; }
.vision-structured .vt-lh-normal { line-height: 1.38; }
.vision-structured .vt-lh-relaxed { line-height: 1.52; }
/* Alignment */
.vision-structured .vt-align-left { text-align: left; }
.vision-structured .vt-align-center { text-align: center; }
.vision-structured .vt-align-right { text-align: right; }
.vision-structured .vt-align-justify { text-align: justify; }
/* Region rhythm */
.vision-structured .vt-role-letterhead {
  margin: 1pt 0 2pt 0;
}
.vision-structured .vt-role-meta_row {
  margin: 3pt 0 5pt 0;
}
.vision-structured .vt-role-subject {
  margin: 10pt 0 8pt 0;
}
.vision-structured .vt-role-body_clause {
  margin: 6pt 0;
}
.vision-structured .vt-role-general {
  margin: 4pt 0;
}
.vision-structured .vt-role-signature_block {
  margin: 16pt 0 6pt 0;
}
.vision-structured .vt-role-footer {
  margin: 14pt 0 4pt 0;
  font-size: 10pt;
  color: #222;
}
/* Academic / journal roles */
.vision-structured .vt-role-title {
  margin: 14pt 0 6pt 0;
  text-align: center;
}
.vision-structured .vt-role-author {
  margin: 4pt 0 14pt 0;
  text-align: center;
  font-style: italic;
}
.vision-structured .vt-role-page_header {
  margin: 0 0 6pt 0;
  font-size: 9pt;
  color: #444;
}
.vision-structured .vt-role-page_number {
  margin: 6pt 0;
  text-align: center;
  font-size: 9pt;
  color: #444;
}
.vision-structured .vt-role-body {
  margin: 4pt 0;
  text-align: justify;
}
.vision-structured .vt-role-footnote {
  margin: 2pt 0;
  font-size: 9pt;
  border-top: 0.5pt solid #888;
  padding-top: 4pt;
}
.vision-structured .vt-role-footnote + .vt-role-footnote {
  border-top: none;
  padding-top: 0;
}
.vision-structured .vt-role-block_quote {
  margin: 6pt 18pt;
  font-size: 10pt;
  border-left: 2pt solid #888;
  padding-left: 8pt;
}
.vision-structured .vt-role-caption {
  margin: 4pt 0;
  font-size: 9pt;
  font-style: italic;
  text-align: center;
}
/* Rows inside structured sections */
.vision-structured .vt-row {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  gap: 1em;
}
.vision-structured .vt-row .col-left {
  flex: 1 1 auto;
  text-align: left;
  white-space: normal;
}
.vision-structured .vt-row .col-right {
  flex: 0 0 auto;
  white-space: nowrap;
  text-align: right;
}
.vision-structured .vt-row .vt-cell {
  font-weight: inherit;
  font-size: inherit;
  line-height: inherit;
}
"""


def _noto_family(lang: str) -> str:
    lang = lang.lower()
    if lang in _SOUTH_INDIC:
        return f"'{_SOUTH_INDIC[lang]}', 'Noto Sans', sans-serif"
    if lang in _RTL_LANGS:
        return "'Noto Nastaliq Urdu', 'Noto Kufi Arabic', sans-serif"
    if lang in _DEVANAGARI_LANGS:
        # Formal legal notices look closer to the source scan with a serif
        # Devanagari face; keep common OS/container fallbacks.
        return (
            "'Noto Serif Devanagari', 'Kokila', 'Mangal', 'Shobhika', 'Sahadeva', "
            "'Nirmala UI', 'Noto Sans Devanagari', serif"
        )
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


def _render_block(block: TextBlock | RowBlock | ImageBlock) -> str:
    if isinstance(block, RowBlock):
        left = _render_spans(block.left)
        right = _render_spans(block.right)
        return f'<div class="row"><div class="col-left">{left}</div><div class="col-right">{right}</div></div>\n'

    if isinstance(block, ImageBlock):
        alt = _html.escape(block.alt_text or block.image_id)
        if block.image_base64:
            src = _html.escape(block.image_base64, quote=True)
            return f'<figure class="ocr-image"><img src="{src}" alt="{alt}"/><figcaption>{alt}</figcaption></figure>\n'
        return f'<figure class="ocr-image placeholder"><div>{alt}</div></figure>\n'

    # TextBlock
    inner = _render_spans(block.spans)
    if block.type == "heading":
        tag = "h1" if block.level <= 1 else "h2"
        cls = f' class="{block.align}"' if block.align != "left" else ""
        return f"<{tag}{cls}>{inner}</{tag}>\n"
    if block.type == "bullet":
        return f"<li>{inner}</li>\n"
    if block.type == "numbered":
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
    list_type: str | None = None

    for block in page.blocks:
        current_list = None
        if isinstance(block, TextBlock) and block.type in {"bullet", "numbered"}:
            current_list = "ol" if block.type == "numbered" else "ul"
            if list_type != current_list:
                if list_type:
                    parts.append(f"</{list_type}>\n")
                parts.append(f"<{current_list}>\n")
                list_type = current_list
        else:
            if list_type:
                parts.append(f"</{list_type}>\n")
                list_type = None

        parts.append(_render_block(block))

    if list_type:
        parts.append(f"</{list_type}>\n")

    return "".join(parts)


def render_to_html(doc: Document, lang: str) -> str:
    """Render a translated Document IR to a complete HTML document."""
    return wrap_pages_html([_render_page(page) for page in doc.pages], lang)


def wrap_pages_html(per_page_html: list[str], lang: str) -> str:
    """Wrap raw per-page HTML fragments in the shared document chrome.

    Used both by the IR-based native-text path (via render_to_html) and by the
    vision-LLM scanned path which emits raw HTML directly.
    """
    lang_lower = lang.lower()
    lang_code = _LANG_CODES.get(lang_lower, "hi")
    noto = _noto_family(lang_lower)
    direction = "rtl" if lang_lower in _RTL_LANGS else "ltr"

    # @font-face declarations: ensure the family names referenced below resolve to a
    # shaping-capable font even when the host hasn't installed the Noto package.
    # local() walks platform-native aliases first; the file:// URLs are the in-container
    # (Debian fonts-noto-*) fallback. Browsers skip URL sources whose path doesn't exist.
    font_faces = """
@font-face {
  font-family: 'Mangal';
  src: local('Mangal'), local('Nirmala UI');
}
@font-face {
  font-family: 'Lohit Devanagari';
  src: local('Lohit Devanagari'),
       url('file:///usr/share/fonts/truetype/lohit-devanagari/Lohit-Devanagari.ttf') format('truetype');
}
@font-face {
  font-family: 'Sahadeva';
  src: local('Sahadeva'),
       url('file:///usr/share/fonts/truetype/sahadeva/sahadeva.ttf') format('truetype');
}
@font-face {
  font-family: 'Noto Serif Devanagari';
  src: local('Noto Serif Devanagari'), local('Devanagari Sangam MN'),
       url('file:///usr/share/fonts/truetype/noto/NotoSerifDevanagari-Regular.ttf') format('truetype');
}
@font-face {
  font-family: 'Noto Sans Devanagari';
  src: local('Noto Sans Devanagari'), local('Devanagari MT'), local('Nirmala UI'),
       url('file:///usr/share/fonts/truetype/noto/NotoSansDevanagari-Regular.ttf') format('truetype');
}
@font-face {
  font-family: 'Noto Sans Bengali';
  src: local('Noto Sans Bengali'), local('Bangla MN'), local('Nirmala UI'),
       url('file:///usr/share/fonts/truetype/noto/NotoSansBengali-Regular.ttf') format('truetype');
}
@font-face {
  font-family: 'Noto Sans Tamil';
  src: local('Noto Sans Tamil'), local('Tamil MN'), local('Nirmala UI'),
       url('file:///usr/share/fonts/truetype/noto/NotoSansTamil-Regular.ttf') format('truetype');
}
@font-face {
  font-family: 'Noto Sans Telugu';
  src: local('Noto Sans Telugu'), local('Telugu MN'), local('Nirmala UI'),
       url('file:///usr/share/fonts/truetype/noto/NotoSansTelugu-Regular.ttf') format('truetype');
}
@font-face {
  font-family: 'Noto Sans Kannada';
  src: local('Noto Sans Kannada'), local('Kannada MN'), local('Nirmala UI'),
       url('file:///usr/share/fonts/truetype/noto/NotoSansKannada-Regular.ttf') format('truetype');
}
@font-face {
  font-family: 'Noto Sans Malayalam';
  src: local('Noto Sans Malayalam'), local('Malayalam MN'), local('Nirmala UI'),
       url('file:///usr/share/fonts/truetype/noto/NotoSansMalayalam-Regular.ttf') format('truetype');
}
@font-face {
  font-family: 'Noto Sans Gujarati';
  src: local('Noto Sans Gujarati'), local('Gujarati MT'), local('Nirmala UI'),
       url('file:///usr/share/fonts/truetype/noto/NotoSansGujarati-Regular.ttf') format('truetype');
}
@font-face {
  font-family: 'Noto Sans Gurmukhi';
  src: local('Noto Sans Gurmukhi'), local('Gurmukhi MN'), local('Nirmala UI'),
       url('file:///usr/share/fonts/truetype/noto/NotoSansGurmukhi-Regular.ttf') format('truetype');
}
@font-face {
  font-family: 'Noto Sans Oriya';
  src: local('Noto Sans Oriya'), local('Oriya MN'), local('Nirmala UI'),
       url('file:///usr/share/fonts/truetype/noto/NotoSansOriya-Regular.ttf') format('truetype');
}
@font-face {
  font-family: 'Noto Nastaliq Urdu';
  src: local('Noto Nastaliq Urdu'), local('Geeza Pro'),
       url('file:///usr/share/fonts/truetype/noto/NotoNastaliqUrdu-Regular.ttf') format('truetype');
}
"""

    css = f"""
{font_faces}
@page {{ size: A4; margin: 1.5cm 1.4cm; }}
* {{ box-sizing: border-box; }}
body {{
  font-family: {noto};
  font-feature-settings: "kern" 1, "liga" 1, "calt" 1;
  font-size: 11pt;
  line-height: 1.38;
  color: #111;
  direction: {direction};
  text-rendering: optimizeLegibility;
}}
{_VISION_STRUCTURED_CSS}
h1 {{ font-size: 16pt; font-weight: 700; margin: 0 0 5pt; text-align: center; }}
h2 {{
  font-size: 12.5pt; font-weight: 700;
  margin: 9pt 0 4pt;
}}
h1.center, h2.center {{ text-align: center; border-bottom: 0; }}
h1.right,  h2.right  {{ text-align: right; }}
p {{ margin: 4pt 0; }}
p.center {{ text-align: center; }}
p.right  {{ text-align: right; }}
a {{ color: inherit; text-decoration: underline; }}
.row {{
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  gap: 1em;
  margin: 1pt 0;
}}
.col-left  {{ flex: 1 1 auto; }}
.col-right {{ flex: 0 0 auto; white-space: nowrap; text-align: right; }}
ul, ol {{ padding-left: 1.4em; margin: 4pt 0 7pt 0; }}
li {{ margin-bottom: 3pt; line-height: 1.38; }}
strong {{ font-weight: 700; }}
.ocr-image {{
  margin: 8pt 0;
  page-break-inside: avoid;
  text-align: center;
}}
.ocr-image img {{
  max-width: 100%;
  max-height: 180pt;
  object-fit: contain;
}}
.ocr-image figcaption {{
  color: #555;
  font-size: 8pt;
  margin-top: 2pt;
}}
.ocr-image.placeholder {{
  border: 1px dashed #aaa;
  color: #555;
  padding: 8pt;
}}
"""

    # No forced page-breaks — let Playwright paginate naturally by content height.
    # Forcing breaks at source-page boundaries leaves blank space when Hindi text is shorter.
    body = "\n".join(per_page_html)
    return (
        f'<!DOCTYPE html>\n<html lang="{lang_code}" dir="{direction}">\n'
        f'<head>\n<meta charset="utf-8"/>\n<style>{css}</style>\n</head>\n'
        f'<body>\n{body}\n</body>\n</html>'
    )
