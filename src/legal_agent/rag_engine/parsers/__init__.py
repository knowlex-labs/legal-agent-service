"""
Parsers for different content sources (PDF, YouTube, Web).

Usage:
    # Using factory with explicit type
    from parsers import ParserFactory
    parser = ParserFactory.get_parser("pdf")

    # Using factory with auto-detection
    parser = ParserFactory.create_parser_for_source("https://youtube.com/...")

    # Using specific parser directly
    from parsers import PDFParser
    parser = PDFParser()
"""

from .models import ParsedContent, ParsedMetadata, ContentSection
from .base_parser import BaseParser
from .pdf_parser import PDFParser
from .youtube_parser import YouTubeParser
from .web_parser import WebParser
from .image_parser import ImageParser
from .parser_factory import ParserFactory

__all__ = [
    'ParsedContent',
    'ParsedMetadata',
    'ContentSection',
    'BaseParser',
    'PDFParser',
    'YouTubeParser',
    'WebParser',
    'ImageParser',
    'ParserFactory',
]
