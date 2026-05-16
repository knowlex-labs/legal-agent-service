"""Translation v2 — Gemini 2.5 Pro vision-first pipeline.

Per-page HTML rebuild with absolute-positioned semantic blocks, autofit ladder
for Hindi expansion, bundled Noto Serif Devanagari for clean HarfBuzz shaping.
Page count = source page count by construction.
"""

from legal_agent.agents.translation_v2.pipeline import translate_pdf_v2

__all__ = ["translate_pdf_v2"]
