"""
Data models specific to legal document parsing.
Extends the base models to support constitutional and statutory structures.
"""

from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
from datetime import datetime
from .models import ParsedContent, ParsedMetadata, ContentSection


@dataclass
class LegalProvision:
    """
    Represents a legal provision (Article, Section, etc.) with hierarchical structure.
    Maps to the Provision node in our Neo4j ontology.
    """
    id: str  # "Art-21", "BNS-302"
    number: str  # "21", "302"
    title: str  # "Protection of life and personal liberty"
    text: str  # Full provision text

    # Hierarchical context
    part_number: Optional[str] = None  # For Constitution Parts
    chapter_number: Optional[str] = None  # For BNS Chapters
    statute_name: Optional[str] = None  # "Constitution of India", "Bharatiya Nyaya Sanhita"

    # Document location
    page_start: Optional[int] = None
    page_end: Optional[int] = None

    # Cross-references extracted from text
    references: List[str] = field(default_factory=list)  # ["Art-14", "Art-19"]

    # Legal classification
    provision_type: str = "ARTICLE"  # ARTICLE, SECTION, SCHEDULE

    # Amendment history (if mentioned in text)
    amendments: List[str] = field(default_factory=list)


@dataclass
class LegalHierarchy:
    """
    Represents the hierarchical structure of legal documents.
    """
    # For Constitution: Parts -> Articles -> Clauses
    # For BNS: Chapters -> Sections -> Subsections

    parts: List[Dict[str, Any]] = field(default_factory=list)  # Parts I-XXV
    chapters: List[Dict[str, Any]] = field(default_factory=list)  # BNS Chapters
    schedules: List[Dict[str, Any]] = field(default_factory=list)  # Constitution Schedules
    provisions: List[LegalProvision] = field(default_factory=list)  # All articles/sections


@dataclass
class LegalDocument:
    """
    Represents a parsed legal document (Constitution, BNS, etc.)
    """
    name: str  # "Constitution of India"
    document_type: str  # "CONSTITUTIONAL", "CRIMINAL"
    year: int

    # Document structure
    hierarchy: LegalHierarchy
    total_provisions: int

    # Document metadata
    total_pages: Optional[int] = None
    parsing_method: str = "llamaparse"  # llamaparse, fallback
    parsed_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    # Cross-references found in document
    internal_references: Dict[str, List[str]] = field(default_factory=dict)  # Art-21 -> [Art-14, Art-19]


@dataclass
class LegalParsedContent(ParsedContent):
    """
    Extension of ParsedContent specifically for legal documents.
    Maintains compatibility with existing parser infrastructure while adding legal-specific data.
    """

    # Legal document structure (with default to avoid dataclass inheritance issues)
    legal_document: Optional[LegalDocument] = None

    # Override source_type validation for legal documents
    def __post_init__(self):
        # Call parent's __post_init__ first
        super().__post_init__()

        # Extend valid types to include legal document types
        valid_types = ['pdf', 'youtube', 'web', 'constitution', 'statute']
        if self.source_type not in valid_types:
            raise ValueError(f"Invalid source_type: {self.source_type}. Must be one of {valid_types}")

        # Ensure legal_document is provided for legal document types
        if self.source_type in ['constitution', 'statute'] and self.legal_document is None:
            raise ValueError(f"legal_document is required for source_type: {self.source_type}")

    def get_provisions_by_part(self, part_number: str) -> List[LegalProvision]:
        """Get all provisions in a specific part."""
        if not self.legal_document:
            return []
        return [p for p in self.legal_document.hierarchy.provisions if p.part_number == part_number]

    def get_provisions_by_chapter(self, chapter_number: str) -> List[LegalProvision]:
        """Get all provisions in a specific chapter."""
        if not self.legal_document:
            return []
        return [p for p in self.legal_document.hierarchy.provisions if p.chapter_number == chapter_number]

    def get_provision_by_id(self, provision_id: str) -> Optional[LegalProvision]:
        """Get a specific provision by ID."""
        if not self.legal_document:
            return None
        return next((p for p in self.legal_document.hierarchy.provisions if p.id == provision_id), None)

    def get_cross_references(self, provision_id: str) -> List[str]:
        """Get all provisions referenced by a specific provision."""
        if not self.legal_document:
            return []
        return self.legal_document.internal_references.get(provision_id, [])

    def get_fundamental_rights_articles(self) -> List[LegalProvision]:
        """Get all Fundamental Rights articles (Part III, Articles 12-35)."""
        if not self.legal_document or self.legal_document.document_type != "CONSTITUTIONAL":
            return []
        return [p for p in self.legal_document.hierarchy.provisions
                if p.part_number == "III" and p.provision_type == "ARTICLE"]

    def get_schedule_provisions(self) -> List[LegalProvision]:
        """Get all Schedule provisions."""
        if not self.legal_document:
            return []
        return [p for p in self.legal_document.hierarchy.provisions if p.provision_type == "SCHEDULE"]