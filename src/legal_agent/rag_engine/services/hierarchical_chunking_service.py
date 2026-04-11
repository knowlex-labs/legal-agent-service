import re
import uuid
import logging
import os
from typing import List, Any
from legal_agent.rag_engine.models.api_models import (
    HierarchicalChunk,
    ChunkType,
    TopicMetadata,
    ChunkMetadata
)
from legal_agent.rag_engine.parsers.models import ParsedContent, ContentSection
from legal_agent.rag_engine.parsers.pdf_parser import PDFParser

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Legal paragraph detection patterns
# ---------------------------------------------------------------------------

# Numbered / lettered list item that starts at the beginning of a line:
#   "1. ", "2) ", "(a) ", "a. ", "a) "
_INLINE_ITEM_RE = re.compile(
    r'\n[ \t]*(\d{1,2}[\.\)]\s+|[a-z][\.\)]\s+|\([a-zA-Z0-9]{1,2}\)\s+)'
)

# Legal keyword markers for chunk-type classification
_PROVISION_RE = re.compile(
    r'\b(shall|must|hereby|pursuant|notwithstanding|in accordance|herein|therein|thereof)\b',
    re.IGNORECASE,
)
_DEFINITION_RE = re.compile(
    r'\b(means|includes|shall mean|defined as|refers to|is defined)\b',
    re.IGNORECASE,
)
_PREAMBLE_RE = re.compile(
    r'(?im)^(whereas\b|to\s*:|from\s*:|subject\s*:|f\.?no\.?\s*:|ref\.?\s*:|sir\s*,|dear\b)',
)

# Sentence boundary for splitting oversized paragraphs (avoids "no.", "etc." issues)
_SENTENCE_END_RE = re.compile(r'(?<=[.!?])\s+(?=[A-Z(])')


class HierarchicalChunkingService:

    # Paragraph size bounds (characters)
    _MIN_PARA_CHARS = 80
    _MAX_PARA_CHARS = 1200

    def __init__(self):
        self.pdf_parser = PDFParser()

        # Header text patterns for chunk type classification
        self.example_header_patterns = [
            'example', 'sample', 'worked', 'demonstration',
            'illustration', 'case study'
        ]
        self.question_header_patterns = [
            'exercise', 'problem', 'question', 'checkpoint',
            'practice', 'review', 'test yourself'
        ]

        # Equation patterns
        self.equation_pattern = re.compile(r'[=+\-*/]\s*[A-Za-z0-9]|[A-Za-z]\s*=')
        self.formula_pattern = re.compile(r'([A-Z][a-z]?\s*=|∑|∫|√|π|α|β|γ|Δ)')

    def chunk_pdf_hierarchically(
        self,
        file_path: str,
        document_id: str,
        chunk_size: int = 800,
        chunk_overlap: int = 100
    ) -> List[HierarchicalChunk]:

        chunks = []

        logger.info("Chunk PDF Hierarchically called")
        logger.info(f"DEBUG: Attempting to open file_path: '{file_path}'")
        logger.info(f"DEBUG: File path exists check: {os.path.exists(file_path) if file_path else 'N/A'}")

        try:
            parsed_content = self.pdf_parser.parse(file_path)
            logger.info(f"Processing PDF with {parsed_content.metadata.page_count} pages for document {document_id}")

            if not parsed_content.sections or len(parsed_content.sections) == 0:
                logger.warning(f"No sections found in PDF {file_path}. Falling back to basic text chunking.")
                if parsed_content.text.strip():
                    return self._create_basic_chunks(parsed_content.text, document_id, chunk_size, chunk_overlap)
                else:
                    logger.error(f"No text content extracted from PDF {file_path}")
                    return []

            for section in parsed_content.sections:
                if not section.text or len(section.text.strip()) < 50:
                    continue

                page_start = getattr(section, "page_number", None) or getattr(section, "start_page", None)
                section_chunks = self._create_paragraph_chunks(
                    section.text,
                    document_id,
                    page_start=page_start,
                    section_title=section.title or "Document Content",
                )
                chunks.extend(section_chunks)

            logger.info(f"Successfully created {len(chunks)} paragraph chunks from {len(parsed_content.sections)} sections")

        except FileNotFoundError as e:
            logger.error(f"PDF file not found: {file_path}")
            return []
        except Exception as e:
            logger.error(f"Error processing PDF {file_path}: {e}", exc_info=True)
            return []

        logger.info(f"CHUNKING RESULT: Returning {len(chunks)} chunks for document {document_id}")
        return chunks

    def chunk_parsed_content(self, parsed_content: ParsedContent, file_type: str = "web") -> List[HierarchicalChunk]:
        """
        Chunk ParsedContent preserving hierarchy and metadata.
        """
        if file_type == "image":
            return self._create_image_chunk(parsed_content)

        chunks = []
        document_id = str(uuid.uuid4())
        chunk_num = 0

        for section in parsed_content.sections:
            if not section.text.strip():
                continue

            page_start = getattr(section, "page_number", None)
            section_chunks = self._create_paragraph_chunks(
                section.text,
                document_id,
                page_start=page_start,
                section_title=section.title or "Document Content",
            )
            for sub_chunk in section_chunks:
                chunk_num += 1
                chunks.append(sub_chunk)

        if not chunks and parsed_content.text:
            return self.chunk_text(parsed_content.text, file_type)

        return chunks

    def chunk_text(self, text: str, file_type: str = "text", book_metadata: Any = None) -> List[HierarchicalChunk]:
        """Wrapper for basic text chunking to support generic text"""
        document_id = str(uuid.uuid4())
        return self._create_basic_chunks(text, document_id)

    def _create_basic_chunks(
        self,
        text: str,
        document_id: str,
        chunk_size: int = 800,
        chunk_overlap: int = 100
    ) -> List[HierarchicalChunk]:

        chunks = []
        text = text.strip()

        if not text:
            return chunks

        sentences = re.split(r'(?<=[.!?])\s+', text)

        current_chunk = ""
        chunk_num = 0

        for sentence in sentences:
            if len(current_chunk) + len(sentence) > chunk_size and current_chunk:
                chunk_num += 1
                chunk_id = f"{document_id}_chunk_{chunk_num}"

                chunk = HierarchicalChunk(
                    chunk_id=chunk_id,
                    document_id=document_id,
                    text=current_chunk.strip(),
                    topic_metadata=TopicMetadata(
                        chapter_num=None,
                        chapter_title="Document Content",
                        section_num=f"Part {chunk_num}",
                        section_title=f"Content Part {chunk_num}",
                        page_start=None,
                        page_end=None
                    ),
                    chunk_metadata=ChunkMetadata(
                        chunk_type=ChunkType.CONCEPT,  # Default to concept
                        topic_id=document_id,
                        key_terms=self._extract_key_terms(current_chunk),
                        equations=self._extract_equations(current_chunk),
                        has_equations=bool(self._extract_equations(current_chunk)),
                        has_diagrams=False
                    )
                )
                chunks.append(chunk)

                words = current_chunk.split()
                overlap_words = words[-chunk_overlap:] if len(words) > chunk_overlap else words
                current_chunk = ' '.join(overlap_words) + ' ' + sentence
            else:
                current_chunk += ' ' + sentence if current_chunk else sentence

        if current_chunk.strip():
            chunk_num += 1
            chunk_id = f"{document_id}_chunk_{chunk_num}"

            chunk = HierarchicalChunk(
                chunk_id=chunk_id,
                document_id=document_id,
                text=current_chunk.strip(),
                topic_metadata=TopicMetadata(
                    chapter_num=None,
                    chapter_title="Document Content",
                    section_num=f"Part {chunk_num}",
                    section_title=f"Content Part {chunk_num}",
                    page_start=None,
                    page_end=None
                ),
                chunk_metadata=ChunkMetadata(
                    chunk_type=ChunkType.CONCEPT,
                    topic_id=document_id,
                    key_terms=self._extract_key_terms(current_chunk),
                    equations=self._extract_equations(current_chunk),
                    has_equations=bool(self._extract_equations(current_chunk)),
                    has_diagrams=False
                )
            )
            chunks.append(chunk)

        logger.info(f"Created {len(chunks)} basic chunks from text")
        return chunks

    def _create_image_chunk(self, parsed_content: ParsedContent) -> List[HierarchicalChunk]:
        document_id = str(uuid.uuid4())
        title = parsed_content.metadata.title or "Image"

        chunk = HierarchicalChunk(
            chunk_id=f"{document_id}_image_1",
            document_id=document_id,
            text=parsed_content.text,
            topic_metadata=TopicMetadata(
                chapter_title=title,
                section_title="Image Content",
                page_start=1,
                page_end=1,
            ),
            chunk_metadata=ChunkMetadata(
                chunk_type=ChunkType.IMAGE,
                topic_id=document_id,
                key_terms=self._extract_key_terms(parsed_content.text),
                has_diagrams=True,
            ),
        )
        logger.info(f"Created 1 image chunk for document {document_id}")
        return [chunk]

    def _classify_chunk_type_from_header(self, header_text: str) -> ChunkType:
        header_lower = header_text.lower()

        for pattern in self.example_header_patterns:
            if pattern in header_lower:
                return ChunkType.EXAMPLE

        for pattern in self.question_header_patterns:
            if pattern in header_lower:
                return ChunkType.QUESTION

        return ChunkType.CONCEPT


    def _extract_key_terms(self, text: str) -> List[str]:
        terms = []
        quoted = re.findall(r'"([^"]+)"', text)
        terms.extend(quoted)
        capitalized = re.findall(r'(?<!^)([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)', text)
        terms.extend(capitalized)
        return list(set(terms))[:10] 

    def _extract_equations(self, text: str) -> List[str]:
        equations = []
        lines = text.split('\n')
        for line in lines:
            if self.equation_pattern.search(line) or self.formula_pattern.search(line):
                eq = line.strip()
                if len(eq) < 100:
                    equations.append(eq)

        return equations[:5]

    def _has_diagram_reference(self, text: str) -> bool:
        diagram_keywords = ['figure', 'diagram', 'fig.', 'illustration', 'graph', 'chart']
        text_lower = text.lower()
        return any(keyword in text_lower for keyword in diagram_keywords)

chunking_service = HierarchicalChunkingService()
