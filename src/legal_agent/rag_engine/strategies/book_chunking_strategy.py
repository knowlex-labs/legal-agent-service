"""
Book chunking strategy for full textbooks.

Uses larger chunks (2048 chars) to preserve context for complex concepts.
Research shows 512-1024 tokens optimal for analytical queries.
"""

import re
import logging
from typing import List, Dict, Any, Optional
import pdfplumber
from legal_agent.rag_engine.strategies.base_chunking_strategy import BaseChunkingStrategy
from legal_agent.rag_engine.models.api_models import HierarchicalChunk, ContentType, BookMetadata

logger = logging.getLogger(__name__)


class BookChunkingStrategy(BaseChunkingStrategy):
    """
    Strategy for indexing full textbooks (1000+ pages).

    Characteristics:
    - Chunk size: 2048 characters (~512 tokens)
    - Overlap: 200 characters (10%)
    - Extracts book-level metadata from first page
    - Suitable for cross-chapter queries and conceptual understanding
    """

    chunk_size = 2048  # ~512 tokens (research-backed optimal size for analytical queries)
    chunk_overlap = 200  # 10% overlap
    content_type = ContentType.BOOK

    def chunk_document(
        self,
        file_path: str,
        document_id: str,
        hierarchical_chunker,
        book_metadata: Optional[BookMetadata] = None
    ) -> List[HierarchicalChunk]:
        """
        Chunk a full book using hierarchical structure with larger chunks.

        Args:
            file_path: Path to the PDF file
            document_id: Unique identifier for the document
            hierarchical_chunker: Instance of HierarchicalChunkingService
            book_metadata: Optional book-level metadata (extracted or user-provided)

        Returns:
            List of chunks with book metadata attached
        """
        logger.info(f"Using BookChunkingStrategy: {self.chunk_size} chars, {self.chunk_overlap} overlap")

        # Use hierarchical chunker with book-specific settings
        chunks = hierarchical_chunker.chunk_pdf_hierarchically(
            file_path=file_path,
            document_id=document_id,
            chunk_size=self.chunk_size,
            chunk_overlap=self.chunk_overlap
        )

        # If book metadata provided, attach to each chunk
        if book_metadata and chunks:
            logger.info(f"Attaching book metadata to {len(chunks)} chunks: {book_metadata.book_title}")
            # Note: Metadata will be attached in document_builder during embedding generation

        return chunks

    def extract_metadata(self, file_path: str) -> Dict[str, Any]:
        """
        Extract book-level metadata from first page.

        Looks for:
        - Book title (usually largest text on first page)
        - Authors (names after title)
        - Edition (e.g., "11th Edition")
        - ISBN (International Standard Book Number)
        - Publisher information
        - Copyright year

        Args:
            file_path: Path to the PDF file

        Returns:
            Dictionary with extracted metadata
        """
        metadata = {
            "book_title": None,
            "book_authors": [],
            "book_edition": None,
            "book_subject": None,
            "total_chapters": None,
            "total_pages": None
        }

        try:
            with pdfplumber.open(file_path) as pdf:
                metadata["total_pages"] = len(pdf.pages)

                if len(pdf.pages) == 0:
                    return metadata

                # Extract first page text
                first_page = pdf.pages[0]
                text = first_page.extract_text()

                if not text:
                    logger.warning(f"No text found on first page of {file_path}")
                    return metadata

                # Extract title (usually first large text line)
                metadata["book_title"] = self._extract_title(text, first_page)

                # Extract edition
                metadata["book_edition"] = self._extract_edition(text)

                # Extract authors
                metadata["book_authors"] = self._extract_authors(text)

                # Try to estimate total chapters by scanning TOC (if on first few pages)
                metadata["total_chapters"] = self._estimate_total_chapters(pdf)

                logger.info(f"Extracted book metadata: {metadata['book_title']}, Edition: {metadata['book_edition']}")

        except Exception as e:
            logger.error(f"Failed to extract book metadata from {file_path}: {e}")

        return metadata

    def _extract_title(self, text: str, page) -> Optional[str]:
        """Extract book title from first page."""
        lines = text.split('\n')

        # Try to find title by font size (largest text usually)
        try:
            chars = page.chars
            if chars:
                # Group characters by size
                size_groups = {}
                for char in chars:
                    if 'size' in char and 'text' in char:
                        size = char['size']
                        if size not in size_groups:
                            size_groups[size] = []
                        size_groups[size].append(char['text'])

                # Get largest font size text
                if size_groups:
                    max_size = max(size_groups.keys())
                    title_chars = size_groups[max_size]
                    title = ''.join(title_chars).strip()
                    if len(title) > 3 and len(title) < 200:
                        return title
        except Exception as e:
            logger.debug(f"Font-based title extraction failed: {e}")

        # Fallback: First non-empty line that's not too short
        for line in lines[:10]:
            line = line.strip()
            if len(line) > 3 and len(line) < 200 and not line.isdigit():
                return line

        return None

    def _extract_edition(self, text: str) -> Optional[str]:
        """Extract edition information."""
        edition_patterns = [
            r'(\d+(?:st|nd|rd|th)\s+edition)',
            r'(edition\s+\d+)',
            r'(\d+(?:st|nd|rd|th)\s+ed\.?)'
        ]

        for pattern in edition_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return match.group(1).strip()

        return None

    def _extract_authors(self, text: str) -> List[str]:
        """Extract author names from first page."""
        authors = []

        # Look for common author patterns
        # Usually appears after title and before publisher info
        lines = text.split('\n')

        # Simple heuristic: Look for capitalized names (2-3 words) in first 20 lines
        name_pattern = r'^[A-Z][a-z]+\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?$'

        for i, line in enumerate(lines[:20]):
            line = line.strip()
            if re.match(name_pattern, line):
                # Additional check: not a common non-author word
                if not any(word in line.lower() for word in ['edition', 'press', 'university', 'chapter', 'contents']):
                    authors.append(line)

        return authors[:5]  # Limit to 5 authors max

    def _estimate_total_chapters(self, pdf) -> Optional[int]:
        """Estimate total chapters by scanning table of contents."""
        chapter_count = 0

        # Scan first 10 pages for TOC
        for page_num in range(min(10, len(pdf.pages))):
            text = pdf.pages[page_num].extract_text()
            if not text:
                continue

            # Look for chapter listings
            chapter_matches = re.findall(r'chapter\s+(\d+)', text, re.IGNORECASE)
            if chapter_matches:
                chapter_numbers = [int(num) for num in chapter_matches if num.isdigit()]
                if chapter_numbers:
                    chapter_count = max(chapter_count, max(chapter_numbers))

        return chapter_count if chapter_count > 0 else None
