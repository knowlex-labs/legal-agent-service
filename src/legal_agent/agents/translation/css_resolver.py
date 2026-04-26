"""Resolve CSS for a translation render: pick the layout stylesheet + a font registry.

A `DocProfile` (see `doc_profiles.py`) names a `css_id` (e.g. `court_filing.css`) and a
`layout_family`. Court-filing / contract / letter layouts get the **serif** font
registry (Shobhika first for Devanagari, Noto Serif fallbacks, Nastaliq for Urdu).
The default layout keeps the legacy **sans** registry for backwards compatibility.

Stylesheets live next to this module under `styles/`. They are read at request time
(no caching) so a hot-reloading dev server picks up CSS edits without restart.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from legal_agent.agents.translation.doc_profiles import DocProfile


_STYLES_DIR = Path(__file__).resolve().parent / "styles"
_SERIF_LAYOUTS = {"court_filing", "contract", "letter"}
_RTL_LANGUAGES = {"urdu", "sindhi", "kashmiri"}


def _read(name: str) -> str:
    path = _STYLES_DIR / name
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def resolve_css(profile: "DocProfile | None", target_language: str | None) -> str:
    """Build the full stylesheet for one translation: font registry + layout CSS.

    `target_language` is currently unused for stylesheet selection (the unicode-range
    blocks in the font registry already script-route per character). Kept on the
    signature so future work can prune fonts to the script actually present.
    """
    del target_language
    css_id = profile.css_id if profile else "default.css"
    layout = profile.layout_family if profile else "default"
    base_css = _read(css_id) or _read("default.css")
    fonts_css = _read("_fonts_serif.css") if layout in _SERIF_LAYOUTS else _read("_fonts_sans.css")
    return fonts_css + "\n" + base_css


def is_rtl(target_language: str | None) -> bool:
    if not target_language:
        return False
    return target_language.lower().strip() in _RTL_LANGUAGES
