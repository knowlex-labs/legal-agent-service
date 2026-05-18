"""OCR-agnostic layout heuristics shared by translation_v3 OCR backends.

Kept independent of any specific OCR engine so the same alignment / font-size /
page-number rules apply whether the upstream extractor is PaddleOCR, Azure
Document Intelligence, Google Document AI, or anything else.
"""

from __future__ import annotations

import re

from legal_agent.agents.translation_v2.schemas import BlockAlign

# Short numeric-only block sitting near the bottom of the page → page number.
PAGE_NUMBER_RE = re.compile(
    r"^[-\s]*\d{1,4}[-\s]*$|^Page\s+\d+\s*(of\s+\d+)?$",
    re.IGNORECASE,
)

# A block whose bottom edge is in the bottom N% of the page is eligible for
# page-number promotion. Tuned empirically against Paddle and Azure outputs.
PAGE_NUMBER_BOTTOM_THRESHOLD = 0.88


def infer_align(bbox_norm: tuple[float, float, float, float]) -> BlockAlign:
    """Guess alignment from a normalised bbox. Narrow + centred → center,
    right-anchored → right, else left."""
    x0, _, x1, _ = bbox_norm
    centre = (x0 + x1) / 2.0
    width = max(0.001, x1 - x0)
    if 0.4 < centre < 0.6 and width < 0.7:
        return BlockAlign.center
    if x1 > 0.92 and x0 > 0.55:
        return BlockAlign.right
    return BlockAlign.left


def estimate_font_size(
    bbox_norm: tuple[float, float, float, float],
    page_height_pt: float,
    line_count: int = 1,
) -> float:
    """Approximate font size in points from a per-line bbox height.

    Azure-style paragraph bboxes span every line in the paragraph, so callers
    pass `line_count` to convert paragraph height into per-line height. Default
    of 1 is correct for line-level bboxes (PaddleOCR, table cells)."""
    _, y0, _, y1 = bbox_norm
    block_height_pt = max(0.0, (y1 - y0) * page_height_pt)
    line_height_pt = block_height_pt / max(1, line_count)
    # 0.7 factor accounts for typical line-leading (font-size ≈ 70% of line-box).
    fs = 0.7 * line_height_pt
    # Cap at 20pt — anything above is almost always a misread of a multi-line
    # block. Body text on legal scans is 10–12pt; titles 14–18pt.
    return max(8.0, min(20.0, fs))


def is_page_number(text: str, bbox_norm: tuple[float, float, float, float]) -> bool:
    """True if `text` looks like a page number and sits near the bottom edge."""
    if not PAGE_NUMBER_RE.match(text):
        return False
    return bbox_norm[3] > PAGE_NUMBER_BOTTOM_THRESHOLD
