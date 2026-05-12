"""Convert translated markdown to styled HTML for PDF rendering via WeasyPrint.

Stylesheet selection is delegated to `css_resolver.resolve_css(profile, lang)` so each
document type can ship its own typography (serif / clause-numbered / etc.) without
this module knowing about it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import markdown as md

from legal_agent.agents.translation.css_resolver import is_rtl, resolve_css

if TYPE_CHECKING:
    from legal_agent.agents.translation.doc_profiles import DocProfile


def markdown_to_html(
    md_text: str,
    target_language: str | None = None,
    profile: "DocProfile | None" = None,
) -> str:
    """Convert markdown text to a complete HTML document with legal styling.

    Args:
        md_text: Markdown-formatted translated document.
        target_language: Target language name (e.g. "hindi") for font/lang/dir.
        profile: Doc-type profile selecting layout CSS. None → default layout.
    """
    html_body = md.markdown(
        md_text,
        extensions=["tables", "fenced_code", "sane_lists"],
    )
    return _wrap_html(html_body, target_language, profile)


def wrap_translated_html(
    body_html: str,
    target_language: str | None = None,
    profile: "DocProfile | None" = None,
) -> str:
    """Wrap pre-built translated HTML (from PyMuPDF extraction) with CSS and HTML5 boilerplate.

    Unlike markdown_to_html(), skips markdown conversion — input is the raw positioned
    HTML produced by the Sarvam HTML translation path. The font-family declarations
    in the CSS provide Indic script fallbacks since PyMuPDF's original font-family
    values (ArialMT, Helvetica-Bold, etc.) were stripped during translation preprocessing.
    """
    return _wrap_html(body_html, target_language, profile)


def _wrap_html(
    body: str,
    target_language: str | None,
    profile: "DocProfile | None",
) -> str:
    lang_attr = _lang_code(target_language) if target_language else "en"
    dir_attr = 'dir="rtl"' if is_rtl(target_language) else ""
    css = resolve_css(profile, target_language)

    return f"""<!DOCTYPE html>
<html lang="{lang_attr}" {dir_attr}>
<head>
<meta charset="utf-8">
<style>
{css}
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
