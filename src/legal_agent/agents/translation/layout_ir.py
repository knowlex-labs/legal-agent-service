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
    underline: bool = False


# Semantic role on a native-PDF block, populated by the heading-detection pass
# in layout_extract. Used by the semantic chunker (footnotes isolated, headings
# 1:1) and the layout renderer (per-role CSS, page-break controls). Distinct
# from `type` which captures structural shape (heading/paragraph/bullet).
NativeBlockRole = Literal[
    "title",
    "author",
    "heading",
    "body",
    "footnote",
    "page_header",
    "page_number",
    "caption",
]


class TextBlock(BaseModel):
    type: Literal["heading", "paragraph", "bullet", "numbered"]
    level: int = 0
    align: Literal["left", "center", "right"] = "left"
    spans: list[Span]
    role: NativeBlockRole | None = None


class RowBlock(BaseModel):
    """Two columns on one visual row — company|location, role|date, Place:|Date:."""
    type: Literal["row"] = "row"
    left: list[Span]
    right: list[Span]
    role: NativeBlockRole | None = None


class ImageBlock(BaseModel):
    """Image placeholder extracted by document OCR."""
    type: Literal["image"] = "image"
    image_id: str
    alt_text: str = ""
    image_base64: str | None = None
    width_px: float | None = None
    height_px: float | None = None


class TableCell(BaseModel):
    spans: list[Span]
    is_header: bool = False


class TableBlock(BaseModel):
    """Multi-column table detected from a native PDF via PyMuPDF find_tables()."""
    type: Literal["table"] = "table"
    rows: list[list[TableCell]]
    role: NativeBlockRole | None = None


class Page(BaseModel):
    width_pt: float
    height_pt: float
    blocks: list[TextBlock | RowBlock | ImageBlock | TableBlock]


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


class VisionTableBlock(BaseModel):
    """Multi-column table extracted from a scanned page.

    rows[0] is the header row when has_header=True. Each cell may contain
    inline HTML (<strong>, <em>) but no block-level tags.
    """

    model_config = ConfigDict(extra="ignore")

    type: Literal["table"] = "table"
    role: VisionRegionRole = "general"
    has_header: bool = False
    rows: list[list[str]] = Field(default_factory=list)


VisionStyledBlock = Annotated[
    VisionStyledTextBlock | VisionStyledRowBlock | VisionTableBlock,
    Field(discriminator="type"),
]


class VisionStructuredPage(BaseModel):
    """One page of structured vision translation output."""

    model_config = ConfigDict(extra="ignore")

    page: int = 1
    blocks: list[VisionStyledTextBlock | VisionStyledRowBlock | VisionTableBlock] = Field(default_factory=list)
