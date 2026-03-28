"""
Chapter chunking strategy for individual book chapters.

Uses medium-sized chunks (1024 chars) balanced between precision and context.
"""

import re
import logging
from typing import List, Dict, Any, Optional
import pdfplumber
from legal_agent.rag_engine.strategies.base_chunking_strategy import BaseChunkingStrategy
from legal_agent.rag_engine.models.api_models import HierarchicalChunk, ContentType, BookMetadata

logger = logging.getLogger(__name__)


class ChapterChunkingStrategy(BaseChunkingStrategy):
    """
    Strategy for indexing individual chapters (10-50 pages).

    Characteristics:
    - Chunk size: 1024 characters (~256 tokens)
    - Overlap: 100 characters (10%)
    - Extracts chapter number and title
    - Balanced approach for single-chapter queries
    """

    chunk_size = 1024  # ~256 tokens (balanced for factoid and analytical queries)
    chunk_overlap = 100  # 10% overlap
    content_type = ContentType.CHAPTER

    def chunk_document(
        self,
        file_path: str,
        document_id: str,
        hierarchical_chunker,
        book_metadata: Optional[BookMetadata] = None
    ) -> List[HierarchicalChunk]:
        """
        Chunk a single chapter using medium-sized chunks.

        Args:
            file_path: Path to the PDF file
            document_id: Unique identifier for the document
            hierarchical_chunker: Instance of HierarchicalChunkingService
            book_metadata: Optional book-level metadata

        Returns:
            List of chunks optimized for chapter-level queries
        """
        logger.info(f"Using ChapterChunkingStrategy: {self.chunk_size} chars, {self.chunk_overlap} overlap")

        # Use hierarchical chunker with chapter-specific settings
        chunks = hierarchical_chunker.chunk_pdf_hierarchically(
            file_path=file_path,
            document_id=document_id,
            chunk_size=self.chunk_size,
            chunk_overlap=self.chunk_overlap
        )

        return chunks

    def extract_metadata(self, file_path: str) -> Dict[str, Any]:
        """
        Extract chapter-level metadata from first page.

        Looks for:
        - Chapter number (e.g., "Chapter 5", "5", "Ch. 5")
        - Chapter title (e.g., "Force and Motion")
        - Page range

        Args:
            file_path: Path to the PDF file

        Returns:
            Dictionary with extracted metadata
        """
        metadata = {
            "chapter_num": None,
            "chapter_title": None,
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

                # Extract chapter number and title
                chapter_info = self._extract_chapter_info(text)
                if chapter_info:
                    metadata["chapter_num"] = chapter_info.get("chapter_num")
                    metadata["chapter_title"] = chapter_info.get("chapter_title")

                logger.info(f"Extracted chapter metadata: Ch {metadata['chapter_num']}: {metadata['chapter_title']}")

        except Exception as e:
            logger.error(f"Failed to extract chapter metadata from {file_path}: {e}")

        return metadata

    def _extract_chapter_info(self, text: str) -> Optional[Dict[str, Any]]:
        """Extract chapter number and title from text."""
        lines = text.split('\n')

        # Patterns for chapter headers
        patterns = [
            r'^chapter\s+(\d+)[:\-\s]*(.+?)$',  # "Chapter 5: Force and Motion"
            r'^ch\.?\s*(\d+)[:\-\s]*(.+?)$',    # "Ch. 5: Force and Motion"
            r'^(\d+)\.\s+([A-Z].+?)$',          # "5. Force and Motion"
            r'^(\d+)[:\-\s]+([A-Z].+?)$'        # "5 - Force and Motion"
        ]

        for i, line in enumerate(lines[:20]):  # Check first 20 lines
            line = line.strip()

            for pattern in patterns:
                match = re.match(pattern, line, re.IGNORECASE)
                if match:
                    chapter_num = match.group(1)
                    chapter_title = match.group(2).strip() if len(match.groups()) > 1 else None

                    # Validate chapter number is reasonable (1-99)
                    try:
                        num = int(chapter_num)
                        if 1 <= num <= 99:
                            return {
                                "chapter_num": num,
                                "chapter_title": chapter_title
                            }
                    except ValueError:
                        continue

        return None
