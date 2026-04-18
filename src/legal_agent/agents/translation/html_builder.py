"""Convert translated markdown to styled HTML for PDF rendering via WeasyPrint.

The HTML output uses a legal document stylesheet with proper support for:
- All Indian scripts via Noto Sans font family
- Appropriate line heights for Devanagari and other complex scripts
- A4 page layout with legal margins
- Tables with borders
- Page numbers
"""

import markdown as md


def markdown_to_html(md_text: str, target_language: str | None = None) -> str:
    """Convert markdown text to a complete HTML document with legal styling.

    Args:
        md_text: Markdown-formatted translated document.
        target_language: Target language name (e.g. "hindi") for font optimization.
            If None, uses a generic multi-script font stack.

    Returns:
        Complete HTML document string ready for WeasyPrint rendering.
    """
    html_body = md.markdown(
        md_text,
        extensions=["tables", "fenced_code", "sane_lists"],
    )
    return _wrap_html(html_body, target_language)


# CSS for legal document PDF rendering.
# Noto Sans covers Latin + all Indic scripts when the full fonts-noto package is installed.
# We use unicode-range to prefer script-specific Noto fonts for best shaping.
_LEGAL_CSS = """\
@page {
    size: A4;
    margin: 2.5cm 2cm 2.5cm 2cm;
    @bottom-center {
        content: counter(page);
        font-family: "Noto Sans", sans-serif;
        font-size: 9pt;
        color: #666;
    }
}

/* Generic Noto Sans for Latin + fallback */
@font-face {
    font-family: "DocFont";
    src: local("Noto Sans"), local("NotoSans"), local("FreeSans");
    font-weight: normal;
}
@font-face {
    font-family: "DocFont";
    src: local("Noto Sans Bold"), local("NotoSans-Bold"), local("FreeSans Bold");
    font-weight: bold;
}

/* Devanagari (Hindi, Marathi, Sanskrit, Nepali, etc.) */
@font-face {
    font-family: "DocFont";
    src: local("Noto Sans Devanagari"), local("NotoSansDevanagari-Regular");
    unicode-range: U+0900-097F, U+A8E0-A8FF, U+1CD0-1CFF;
    font-weight: normal;
}
@font-face {
    font-family: "DocFont";
    src: local("Noto Sans Devanagari Bold"), local("NotoSansDevanagari-Bold");
    unicode-range: U+0900-097F, U+A8E0-A8FF, U+1CD0-1CFF;
    font-weight: bold;
}

/* Bengali */
@font-face {
    font-family: "DocFont";
    src: local("Noto Sans Bengali"), local("NotoSansBengali-Regular");
    unicode-range: U+0980-09FF;
    font-weight: normal;
}
@font-face {
    font-family: "DocFont";
    src: local("Noto Sans Bengali Bold"), local("NotoSansBengali-Bold");
    unicode-range: U+0980-09FF;
    font-weight: bold;
}

/* Tamil */
@font-face {
    font-family: "DocFont";
    src: local("Noto Sans Tamil"), local("NotoSansTamil-Regular");
    unicode-range: U+0B80-0BFF;
    font-weight: normal;
}
@font-face {
    font-family: "DocFont";
    src: local("Noto Sans Tamil Bold"), local("NotoSansTamil-Bold");
    unicode-range: U+0B80-0BFF;
    font-weight: bold;
}

/* Telugu */
@font-face {
    font-family: "DocFont";
    src: local("Noto Sans Telugu"), local("NotoSansTelugu-Regular");
    unicode-range: U+0C00-0C7F;
    font-weight: normal;
}

/* Gujarati */
@font-face {
    font-family: "DocFont";
    src: local("Noto Sans Gujarati"), local("NotoSansGujarati-Regular");
    unicode-range: U+0A80-0AFF;
    font-weight: normal;
}

/* Kannada */
@font-face {
    font-family: "DocFont";
    src: local("Noto Sans Kannada"), local("NotoSansKannada-Regular");
    unicode-range: U+0C80-0CFF;
    font-weight: normal;
}

/* Malayalam */
@font-face {
    font-family: "DocFont";
    src: local("Noto Sans Malayalam"), local("NotoSansMalayalam-Regular");
    unicode-range: U+0D00-0D7F;
    font-weight: normal;
}

/* Gurmukhi (Punjabi) */
@font-face {
    font-family: "DocFont";
    src: local("Noto Sans Gurmukhi"), local("NotoSansGurmukhi-Regular");
    unicode-range: U+0A00-0A7F;
    font-weight: normal;
}

/* Odia */
@font-face {
    font-family: "DocFont";
    src: local("Noto Sans Oriya"), local("NotoSansOriya-Regular");
    unicode-range: U+0B00-0B7F;
    font-weight: normal;
}

/* Arabic/Urdu */
@font-face {
    font-family: "DocFont";
    src: local("Noto Sans Arabic"), local("NotoSansArabic-Regular");
    unicode-range: U+0600-06FF, U+FB50-FDFF, U+FE70-FEFF;
    font-weight: normal;
}

body {
    font-family: "DocFont", "Noto Sans", sans-serif;
    font-size: 11pt;
    line-height: 1.6;
    color: #1a1a1a;
    text-align: justify;
    hyphens: auto;
}

h1, h2, h3, h4 {
    color: #000;
    page-break-after: avoid;
    margin-top: 1.2em;
    margin-bottom: 0.4em;
    line-height: 1.4;
}

h1 { font-size: 18pt; border-bottom: 1px solid #333; padding-bottom: 4pt; }
h2 { font-size: 15pt; }
h3 { font-size: 13pt; }
h4 { font-size: 11pt; }

p {
    margin: 0.4em 0;
    orphans: 3;
    widows: 3;
}

strong, b {
    font-weight: bold;
}

/* Lists */
ul, ol {
    margin: 0.3em 0 0.3em 1.5em;
    padding: 0;
}
li {
    margin-bottom: 0.2em;
}

/* Tables */
table {
    width: 100%;
    border-collapse: collapse;
    margin: 0.8em 0;
    font-size: 10pt;
    page-break-inside: avoid;
}
th, td {
    border: 1px solid #555;
    padding: 6pt 8pt;
    text-align: left;
    vertical-align: top;
}
th {
    background-color: #f0f0f0;
    font-weight: bold;
}

/* Horizontal rules */
hr {
    border: none;
    border-top: 1px solid #999;
    margin: 1em 0;
}

/* Block quotes (sometimes used for legal citations) */
blockquote {
    border-left: 3px solid #666;
    margin: 0.5em 0 0.5em 1em;
    padding-left: 1em;
    color: #333;
    font-style: italic;
}

/* Prevent page breaks inside clauses */
li, tr {
    page-break-inside: avoid;
}
"""


def _wrap_html(body: str, target_language: str | None = None) -> str:
    """Wrap HTML body in a complete document with CSS."""
    lang_attr = _lang_code(target_language) if target_language else "en"
    # Set text direction for Urdu (RTL)
    dir_attr = 'dir="rtl"' if target_language and target_language.lower() == "urdu" else ""

    return f"""<!DOCTYPE html>
<html lang="{lang_attr}" {dir_attr}>
<head>
<meta charset="utf-8">
<style>
{_LEGAL_CSS}
</style>
</head>
<body>
{body}
</body>
</html>"""


def _lang_code(language: str | None) -> str:
    """Map language name to BCP-47 language code."""
    if not language:
        return "en"
    mapping = {
        "english": "en",
        "hindi": "hi",
        "bengali": "bn",
        "tamil": "ta",
        "telugu": "te",
        "marathi": "mr",
        "gujarati": "gu",
        "kannada": "kn",
        "malayalam": "ml",
        "punjabi": "pa",
        "odia": "or",
        "urdu": "ur",
        "assamese": "as",
        "maithili": "mai",
        "santali": "sat",
        "kashmiri": "ks",
        "nepali": "ne",
        "sindhi": "sd",
        "dogri": "doi",
        "konkani": "kok",
        "manipuri": "mni",
        "bodo": "brx",
        "sanskrit": "sa",
    }
    return mapping.get(language.lower(), "en")
