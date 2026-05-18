"""OCR-agnostic layout heuristics shared by translation_v3 OCR backends.

After the flow-layout rewrite (renderer no longer uses bbox-derived font
sizes), only the alignment + page-number heuristics remain — both still
useful for tagging block role/align from Azure bbox positions.
"""

from __future__ import annotations

import re

from legal_agent.agents.translation_v2.schemas import BlockAlign

# Short numeric-only block sitting near the bottom of the page → page number.
PAGE_NUMBER_RE = re.compile(
    r"^[-\s]*\d{1,4}[-\s]*$|^Page\s+\d+\s*(of\s+\d+)?$",
    re.IGNORECASE,
)

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


def is_page_number(text: str, bbox_norm: tuple[float, float, float, float]) -> bool:
    """True if `text` looks like a page number and sits near the bottom edge."""
    if not PAGE_NUMBER_RE.match(text):
        return False
    return bbox_norm[3] > PAGE_NUMBER_BOTTOM_THRESHOLD
