"""
Parsers for file-based content sources (PDF, image).

Usage:
    from legal_agent.rag_engine.parsers import ParserFactory
    parser = ParserFactory.get_parser("pdf")
    parser = ParserFactory.create_parser_for_source(Path("doc.pdf"))
"""

from .models import ParsedContent, ParsedMetadata, ContentSection
from .base_parser import BaseParser
from .pdf_parser import PDFParser
from .image_parser import ImageParser
from .parser_factory import ParserFactory

__all__ = [
    "ParsedContent",
    "ParsedMetadata",
    "ContentSection",
    "BaseParser",
    "PDFParser",
    "ImageParser",
    "ParserFactory",
]
