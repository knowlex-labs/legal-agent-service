"""IR (intermediate representation) models for the translation pipeline.

PyMuPDF dict extraction → these models → HTML renderer.
Keeps layout semantics (alignment, split rows) that survive script-metric expansion.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class Span(BaseModel):
    text: str
    bold: bool = False
    italic: bool = False


class TextBlock(BaseModel):
    type: Literal["heading", "paragraph", "bullet"]
    level: int = 0
    align: Literal["left", "center", "right"] = "left"
    spans: list[Span]


class RowBlock(BaseModel):
    """Two columns on one visual row — company|location, role|date, Place:|Date:."""
    type: Literal["row"] = "row"
    left: list[Span]
    right: list[Span]


class Page(BaseModel):
    width_pt: float
    height_pt: float
    blocks: list[TextBlock | RowBlock]


class Document(BaseModel):
    pages: list[Page]
