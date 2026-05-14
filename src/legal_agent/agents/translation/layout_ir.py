"""IR (intermediate representation) models for the translation pipeline.

PyMuPDF dict extraction → these models → HTML renderer.
Keeps layout semantics (alignment, split rows) that survive script-metric expansion.

Vision/scanned translation adds optional structured blocks with typography hints
(region role, weight, size, line spacing) → renderer applies matching CSS classes.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class Span(BaseModel):
    text: str
    bold: bool = False
    italic: bool = False


class TextBlock(BaseModel):
    type: Literal["heading", "paragraph", "bullet", "numbered"]
    level: int = 0
    align: Literal["left", "center", "right"] = "left"
    spans: list[Span]


class RowBlock(BaseModel):
    """Two columns on one visual row — company|location, role|date, Place:|Date:."""
    type: Literal["row"] = "row"
    left: list[Span]
    right: list[Span]


class ImageBlock(BaseModel):
    """Image placeholder extracted by document OCR."""
    type: Literal["image"] = "image"
    image_id: str
    alt_text: str = ""
    image_base64: str | None = None
    width_px: float | None = None
    height_px: float | None = None


class Page(BaseModel):
    width_pt: float
    height_pt: float
    blocks: list[TextBlock | RowBlock | ImageBlock]


class Document(BaseModel):
    pages: list[Page]


# --- Vision structured layout (scanned PDF translation) ----------------------------

VisionRegionRole = Literal[
    # Indian govt / legal document roles.
    "letterhead",
    "meta_row",
    "subject",
    "body_clause",
    "signature_block",
    "footer",
    # Academic / journal / general document roles.
    "title",
    "author",
    "page_header",
    "page_number",
    "body",
    "footnote",
    "block_quote",
    "caption",
    "general",
]

VisionAlign = Literal["left", "center", "right", "justify"]

VisionWeight = Literal["normal", "semibold", "bold"]

VisionSizeBucket = Literal["xs", "small", "normal", "large", "xlarge"]

VisionLineSpacing = Literal["tight", "normal", "relaxed"]


class VisionStyledTextBlock(BaseModel):
    """Single translated paragraph / title line with visual-style hints."""

    model_config = ConfigDict(extra="ignore")

    type: Literal["text"] = "text"
    role: VisionRegionRole = "general"
    align: VisionAlign = "left"
    weight: VisionWeight = "normal"
    size: VisionSizeBucket = "normal"
    line_spacing: VisionLineSpacing = "normal"
    # Inline markup allowed after sanitization: strong, em, u, br only.
    html: str = ""


class VisionStyledRowBlock(BaseModel):
    """Same-line left/right (e.g. F.NO vs Date, DIN flush-right rows)."""

    model_config = ConfigDict(extra="ignore")

    type: Literal["row"] = "row"
    role: VisionRegionRole = "meta_row"
    weight: VisionWeight = "normal"
    size: VisionSizeBucket = "normal"
    left_html: str = ""
    right_html: str = ""


VisionStyledBlock = Annotated[
    VisionStyledTextBlock | VisionStyledRowBlock,
    Field(discriminator="type"),
]


class VisionStructuredPage(BaseModel):
    """One page of structured vision translation output."""

    model_config = ConfigDict(extra="ignore")

    page: int = 1
    blocks: list[VisionStyledTextBlock | VisionStyledRowBlock] = Field(default_factory=list)
