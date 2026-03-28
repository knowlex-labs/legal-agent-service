import re
import logging
import os
from typing import Optional
import pdfplumber
from legal_agent.rag_engine.strategies.base_chunking_strategy import BaseChunkingStrategy
from legal_agent.rag_engine.strategies.book_chunking_strategy import BookChunkingStrategy
from legal_agent.rag_engine.strategies.chapter_chunking_strategy import ChapterChunkingStrategy
from legal_agent.rag_engine.strategies.document_chunking_strategy import DocumentChunkingStrategy
from legal_agent.rag_engine.models.api_models import ContentType

logger = logging.getLogger(__name__)


class ContentStrategySelector:
    FILE_SIZE_THRESHOLD_MB = 5
    MIN_BOOK_INDICATORS = 2
    MIN_CHAPTER_REFERENCES = 3
    CHAPTER_CHECK_LINES = 5

    BOOK_INDICATORS = [
        r'\bedition\b',
        r'\bisbn[\s\-:]*\d',
        r'\bcopyright\s+©?\s*\d{4}',
        r'\bpublished\s+by\b',
        r'\b(?:university|academic)\s+press\b'
    ]

    CHAPTER_PATTERNS = [
        r'^chapter\s+\d+',
        r'^ch\.?\s*\d+',
        r'^\d+\.\s+[A-Z]',
        r'^CHAPTER\s+\d+',
    ]

    def __init__(self):
        self.strategies = {
            ContentType.BOOK: BookChunkingStrategy(),
            ContentType.CHAPTER: ChapterChunkingStrategy(),
            ContentType.DOCUMENT: DocumentChunkingStrategy()
        }

    def detect_content_type(
        self,
        file_path: str,
        user_hint: Optional[ContentType] = None
    ) -> ContentType:
        if user_hint and user_hint != ContentType.AUTO:
            logger.info(f"Using user-specified content type: {user_hint.value}")
            return user_hint

        if self._is_large_file(file_path):
            return ContentType.BOOK

        try:
            first_page_text = self._read_first_page(file_path)

            if not first_page_text:
                logger.warning(f"Could not read first page from {file_path}, defaulting to DOCUMENT")
                return ContentType.DOCUMENT

            if self._is_book_first_page(first_page_text):
                logger.info(f"Detected BOOK content type from first page analysis")
                return ContentType.BOOK

            if self._is_chapter_first_page(first_page_text):
                logger.info(f"Detected CHAPTER content type from first page analysis")
                return ContentType.CHAPTER

            logger.info(f"No clear indicators found, defaulting to DOCUMENT content type")
            return ContentType.DOCUMENT

        except Exception as e:
            logger.error(f"Error detecting content type from {file_path}: {e}")
            logger.info("Defaulting to DOCUMENT strategy")
            return ContentType.DOCUMENT

    def _is_large_file(self, file_path: str) -> bool:
        try:
            file_size_mb = os.path.getsize(file_path) / (1024 * 1024)
            logger.info(f"File size: {file_size_mb:.1f} MB")

            if file_size_mb > self.FILE_SIZE_THRESHOLD_MB:
                logger.info(f"File size ({file_size_mb:.1f} MB) > {self.FILE_SIZE_THRESHOLD_MB} MB, treating as BOOK")
                return True
            return False
        except Exception as e:
            logger.warning(f"Could not get file size for {file_path}: {e}")
            return False

    def get_strategy(self, content_type: ContentType) -> BaseChunkingStrategy:
        if content_type == ContentType.AUTO:
            logger.warning("AUTO type passed to get_strategy, defaulting to DOCUMENT")
            return self.strategies[ContentType.DOCUMENT]

        strategy = self.strategies.get(content_type)
        if not strategy:
            logger.error(f"No strategy found for content type: {content_type}")
            return self.strategies[ContentType.DOCUMENT]

        logger.info(f"Selected strategy: {strategy}")
        return strategy

    def _read_first_page(self, file_path: str) -> Optional[str]:
        try:
            with pdfplumber.open(file_path) as pdf:
                if len(pdf.pages) == 0:
                    return None

                first_page = pdf.pages[0]
                text = first_page.extract_text()

                return text if text else None

        except Exception as e:
            logger.error(f"Failed to read first page from {file_path}: {e}")
            return None

    def _is_book_first_page(self, text: str) -> bool:
        text_lower = text.lower()

        strong_match_count = sum(
            1 for pattern in self.BOOK_INDICATORS
            if re.search(pattern, text_lower)
        )

        if strong_match_count >= self.MIN_BOOK_INDICATORS:
            logger.debug(f"Found {strong_match_count} book indicators")
            return True

        chapter_matches = re.findall(r'chapter\s+\d+', text_lower)

        if len(chapter_matches) >= self.MIN_CHAPTER_REFERENCES:
            logger.debug(f"Found {len(chapter_matches)} chapter references in TOC")
            return True

        return False

    def _is_chapter_first_page(self, text: str) -> bool:
        lines = text.split('\n')
        first_lines = '\n'.join(lines[:self.CHAPTER_CHECK_LINES])

        for pattern in self.CHAPTER_PATTERNS:
            if re.search(pattern, first_lines, re.IGNORECASE | re.MULTILINE):
                logger.debug(f"Found chapter indicator: {pattern}")
                return True

        return False
