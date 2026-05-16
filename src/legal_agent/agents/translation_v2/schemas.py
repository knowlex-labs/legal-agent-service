"""Pydantic v2 schemas for the translation_v2 pipeline.

The vision extractor emits VisionPage objects. Translation populates `text_hi`
on each Block, producing TranslatedPage. The HTML renderer consumes
TranslatedPage and produces one HTML document per source page.
"""

from __future__ import annotations

import unicodedata
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class BlockRole(str, Enum):
    title = "title"
    heading = "heading"
    subheading = "subheading"
    paragraph = "paragraph"
    clause = "clause"
    list_item = "list_item"
    signature = "signature"
    footer = "footer"
    header = "header"
    page_number = "page_number"
    table_cell = "table_cell"
    caption = "caption"
    other = "other"


class BlockAlign(str, Enum):
    left = "left"
    center = "center"
    right = "right"
    justify = "justify"


class BlockWeight(str, Enum):
    normal = "normal"
    bold = "bold"


class Block(BaseModel):
    """One semantic region on a page.

    bbox_norm is (x0, y0, x1, y1) in [0, 1] relative to page width/height.
    font_size_pt is continuous — never bucketed.
    """

    id: str
    role: BlockRole = BlockRole.paragraph
    align: BlockAlign = BlockAlign.left
    weight: BlockWeight = BlockWeight.normal
    italic: bool = False
    underline: bool = False
    font_size_pt: float = Field(default=11.0, gt=0, le=200)
    reading_order: int = Field(default=0, ge=0)
    bbox_norm: tuple[float, float, float, float]
    text_en: str
    text_hi: str | None = None

    @field_validator("bbox_norm")
    @classmethod
    def _clamp_bbox(cls, v: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
        x0, y0, x1, y1 = v
        x0 = max(0.0, min(1.0, x0))
        y0 = max(0.0, min(1.0, y0))
        x1 = max(0.0, min(1.0, x1))
        y1 = max(0.0, min(1.0, y1))
        if x1 <= x0:
            x1 = min(1.0, x0 + 0.01)
        if y1 <= y0:
            y1 = min(1.0, y0 + 0.01)
        return (x0, y0, x1, y1)

    @field_validator("text_hi")
    @classmethod
    def _nfc_text_hi(cls, v: str | None) -> str | None:
        if v is None:
            return None
        return unicodedata.normalize("NFC", v)


class VisionPage(BaseModel):
    """Output of stage 2 (vision extract) per page."""

    page_no: int = Field(..., ge=1)
    width_pt: float = Field(..., gt=0)
    height_pt: float = Field(..., gt=0)
    blocks: list[Block]


class TranslatedPage(VisionPage):
    """Same shape; `text_hi` populated on every block after stage 4."""


class PageRaster(BaseModel):
    """Output of stage 1 (rasterize)."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    page_no: int = Field(..., ge=1)
    png: bytes
    width_pt: float
    height_pt: float
    width_mm: float
    height_mm: float


class Document(BaseModel):
    """End-of-pipeline aggregate (used for debug dumps + metadata)."""

    source_filename: str
    pages: list[TranslatedPage]
    glossary: dict[str, str] = Field(default_factory=dict)
