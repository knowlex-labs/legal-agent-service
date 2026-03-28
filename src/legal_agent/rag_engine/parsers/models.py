"""
Data models for parsed content.
"""

from dataclasses import dataclass, field
from typing import List, Optional, Union
from datetime import datetime


@dataclass
class ParsedMetadata:
    """Source-specific metadata extracted during parsing."""

    title: str
    url: Optional[str] = None

    # For PDFs
    page_count: Optional[int] = None

    # For YouTube
    duration: Optional[str] = None  # "HH:MM:SS" format
    channel: Optional[str] = None
    video_id: Optional[str] = None

    # For Web articles
    domain: Optional[str] = None
    author: Optional[str] = None
    publish_date: Optional[str] = None

    # Common
    extracted_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())


@dataclass
class ContentSection:
    """
    Hierarchical section representing parsed content structure.

    Examples:
    - Level 1: Chapter header
    - Level 2: Section header
    - Level 3: Paragraph or subsection
    """

    level: int  # 1=header, 2=section, 3=paragraph
    text: str
    title: Optional[str] = None
    parent_id: Optional[str] = None

    # Location information
    page_number: Optional[int] = None  # For PDFs
    timestamp: Optional[str] = None    # For YouTube ("00:12:45")

    # Structural metadata
    section_id: Optional[str] = None

    # Styling metadata (for PDFs)
    font_size: Optional[float] = None
    is_bold: Optional[bool] = None
    is_italic: Optional[bool] = None


@dataclass
class ParsedContent:
    """
    Standardized output from all parsers.

    This structure allows all parsers (PDF, YouTube, Web) to return
    content in a uniform format that can be processed by chunking strategies.
    """

    text: str  # Full raw text
    metadata: ParsedMetadata
    sections: List[ContentSection]  # Hierarchical structure
    source_type: str  # 'pdf', 'youtube', 'web'

    # Content classification hints
    has_equations: bool = False
    has_code_blocks: bool = False
    has_diagrams: bool = False

    # Image-specific fields
    image_data: Optional[bytes] = None
    image_path: Optional[str] = None

    def __post_init__(self):
        """Validate source type."""
        valid_types = ['pdf', 'youtube', 'web', 'constitution', 'image']
        if self.source_type not in valid_types:
            raise ValueError(f"Invalid source_type: {self.source_type}. Must be one of {valid_types}")

    def get_total_sections(self) -> int:
        """Get total number of sections."""
        return len(self.sections)

    def get_sections_by_level(self, level: int) -> List[ContentSection]:
        """Get all sections at a specific hierarchy level."""
        return [section for section in self.sections if section.level == level]

    def get_headers(self) -> List[ContentSection]:
        """Get all top-level headers (level 1)."""
        return self.get_sections_by_level(1)
