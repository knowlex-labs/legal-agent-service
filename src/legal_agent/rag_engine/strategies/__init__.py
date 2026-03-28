"""
Chunking strategy pattern implementation.

This module provides adaptive chunking strategies based on content type:
- BookChunkingStrategy: For full textbooks (2048 chars)
- ChapterChunkingStrategy: For individual chapters (1024 chars)
- DocumentChunkingStrategy: For small documents (512 chars)
- ContentStrategySelector: Auto-detects content type and selects strategy
"""

from legal_agent.rag_engine.strategies.base_chunking_strategy import BaseChunkingStrategy
from legal_agent.rag_engine.strategies.book_chunking_strategy import BookChunkingStrategy
from legal_agent.rag_engine.strategies.chapter_chunking_strategy import ChapterChunkingStrategy
from legal_agent.rag_engine.strategies.document_chunking_strategy import DocumentChunkingStrategy
from legal_agent.rag_engine.strategies.content_strategy_selector import ContentStrategySelector

__all__ = [
    'BaseChunkingStrategy',
    'BookChunkingStrategy',
    'ChapterChunkingStrategy',
    'DocumentChunkingStrategy',
    'ContentStrategySelector'
]
