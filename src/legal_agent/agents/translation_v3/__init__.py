"""Translation v3: cheap, non-Gemini pipeline.

Azure Document Intelligence `prebuilt-layout` for OCR + layout
(~₹0.13/page, 309 languages) + Anthropic Haiku or Sarvam REST for
translation. Targets ≤5₹/scanned page vs v2's ~50₹.

Reuses v2's schemas, rasterize, reflow, html_render, compose stages unchanged
— only the OCR and translate stages are swapped.
"""

from legal_agent.agents.translation_v3.pipeline import translate_pdf_v3

__all__ = ["translate_pdf_v3"]
