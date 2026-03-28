"""
Base chunking strategy abstract class.

Defines the interface that all concrete chunking strategies must implement.
"""

from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
import logging
from legal_agent.rag_engine.models.api_models import HierarchicalChunk, ChunkingStrategy, ContentType, BookMetadata

logger = logging.getLogger(__name__)


class BaseChunkingStrategy(ABC):
    """
    Abstract base class for chunking strategies.

    Each strategy defines:
    - chunk_size: Number of characters per chunk
    - chunk_overlap: Number of overlapping characters between chunks
    - content_type: Type of content this strategy handles
    """

    chunk_size: int
    chunk_overlap: int
    content_type: ContentType

    @abstractmethod
    def chunk_document(
        self,
        file_path: str,
        document_id: str,
        hierarchical_chunker,
        book_metadata: Optional[BookMetadata] = None
    ) -> List[HierarchicalChunk]:
        """
        Chunk the document using this strategy's settings.

        Args:
            file_path: Path to the PDF file
            document_id: Unique identifier for the document
            hierarchical_chunker: Instance of HierarchicalChunkingService
            book_metadata: Optional book-level metadata to attach to chunks

        Returns:
            List of hierarchical chunks with metadata
        """
        pass

    @abstractmethod
    def extract_metadata(self, file_path: str) -> Dict[str, Any]:
        """
        Extract metadata from the document (typically from first page).

        Args:
            file_path: Path to the PDF file

        Returns:
            Dictionary containing extracted metadata
        """
        pass

    def get_chunk_config(self) -> ChunkingStrategy:
        """
        Return the chunking configuration for this strategy.

        Returns:
            ChunkingStrategy model with configuration details
        """
        return ChunkingStrategy(
            chunk_size=self.chunk_size,
            chunk_overlap=self.chunk_overlap,
            content_type=self.content_type,
            description=f"{self.__class__.__name__}: {self.chunk_size} chars, {self.chunk_overlap} overlap"
        )

    def _calculate_overlap_percentage(self) -> float:
        """Calculate overlap as percentage of chunk size."""
        return (self.chunk_overlap / self.chunk_size) * 100 if self.chunk_size > 0 else 0

    def __repr__(self) -> str:
        """String representation of the strategy."""
        return (
            f"{self.__class__.__name__}("
            f"chunk_size={self.chunk_size}, "
            f"overlap={self.chunk_overlap}, "
            f"type={self.content_type.value})"
        )
