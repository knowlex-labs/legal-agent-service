"""
Document chunking strategy for small documents.

Uses smaller chunks (512 chars) for precise fact-based retrieval.
This is the current default strategy.
"""

import logging
from typing import List, Dict, Any, Optional
import pdfplumber
from legal_agent.rag_engine.strategies.base_chunking_strategy import BaseChunkingStrategy
from legal_agent.rag_engine.models.api_models import HierarchicalChunk, ContentType, BookMetadata

logger = logging.getLogger(__name__)


class DocumentChunkingStrategy(BaseChunkingStrategy):
    """
    Strategy for indexing small documents (<10 pages).

    Characteristics:
    - Chunk size: 512 characters (~128 tokens)
    - Overlap: 50 characters (10%)
    - Minimal metadata extraction
    - Optimized for precise, fact-based queries
    - This is the default strategy for backward compatibility
    """

    chunk_size = 512  # ~128 tokens (optimal for factoid queries)
    chunk_overlap = 50  # 10% overlap
    content_type = ContentType.DOCUMENT

    def chunk_document(
        self,
        file_path: str,
        document_id: str,
        hierarchical_chunker,
        book_metadata: Optional[BookMetadata] = None
    ) -> List[HierarchicalChunk]:
        """
        Chunk a small document using smaller, precise chunks.

        Args:
            file_path: Path to the PDF file
            document_id: Unique identifier for the document
            hierarchical_chunker: Instance of HierarchicalChunkingService
            book_metadata: Optional book-level metadata (rarely used for documents)

        Returns:
            List of small chunks optimized for fact retrieval
        """
        logger.info(f"Using DocumentChunkingStrategy: {self.chunk_size} chars, {self.chunk_overlap} overlap")

        # Use hierarchical chunker with document-specific settings
        chunks = hierarchical_chunker.chunk_pdf_hierarchically(
            file_path=file_path,
            document_id=document_id,
            chunk_size=self.chunk_size,
            chunk_overlap=self.chunk_overlap
        )

        return chunks

    def extract_metadata(self, file_path: str) -> Dict[str, Any]:
        """
        Extract basic metadata from small document.

        For small documents, we only extract:
        - Total page count
        - Basic document info from PDF metadata

        Args:
            file_path: Path to the PDF file

        Returns:
            Dictionary with minimal metadata
        """
        metadata = {
            "total_pages": None,
            "pdf_title": None,
            "pdf_author": None
        }

        try:
            with pdfplumber.open(file_path) as pdf:
                metadata["total_pages"] = len(pdf.pages)

                # Try to get PDF metadata
                if pdf.metadata:
                    metadata["pdf_title"] = pdf.metadata.get('Title')
                    metadata["pdf_author"] = pdf.metadata.get('Author')

                logger.info(f"Extracted document metadata: {len(pdf.pages)} pages")

        except Exception as e:
            logger.error(f"Failed to extract document metadata from {file_path}: {e}")

        return metadata
