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
from legal_agent.rag_engine.parsers.models import ParsedContent
from legal_agent.rag_engine.parsers.pdf_parser import PDFParser

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Legal paragraph detection patterns
# ---------------------------------------------------------------------------

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
                    return self._create_paragraph_chunks(parsed_content.text, document_id)
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
        """Wrapper for paragraph-aware chunking to support generic text."""
        document_id = str(uuid.uuid4())
        return self._create_paragraph_chunks(text, document_id)

    # ------------------------------------------------------------------
    # Paragraph-aware chunking (replaces sliding-window _create_basic_chunks)
    # ------------------------------------------------------------------

    def _split_legal_paragraphs(self, text: str) -> List[str]:
        """Split text into legal paragraphs preserving numbered clauses intact.

        Strategy:
        1. Split on blank lines (double newlines) — the primary paragraph break.
        2. Within each block, further split on inline numbered/lettered items
           that start on their own line (e.g. "\\n1. ", "\\n(a) ").
        3. Merge fragments shorter than _MIN_PARA_CHARS into the next block.
        4. Split blocks longer than _MAX_PARA_CHARS at sentence boundaries.
        """
        # Step 1 — split on blank lines, then within each block split on
        # numbered/lettered items that start on their own line.
        paragraphs: List[str] = []
        for block in re.split(r'\n\s*\n', text):
            block = block.strip()
            if not block:
                continue
            # Split on lines starting with numbered/lettered items
            parts = re.split(r'(?m)(?=^\s*(?:\d{1,2}[\.\)]\s|[a-z][\.\)]\s|\([a-zA-Z0-9]{1,2}\)\s))', block)
            for part in parts:
                part = part.strip()
                if part:
                    paragraphs.append(part)

        # Step 3 — merge short fragments into the next paragraph
        merged: List[str] = []
        carry = ""
        for para in paragraphs:
            combined = (carry + " " + para).strip() if carry else para
            if len(combined) < self._MIN_PARA_CHARS:
                carry = combined
            else:
                merged.append(combined)
                carry = ""
        if carry:
            if merged:
                merged[-1] = (merged[-1] + " " + carry).strip()
            else:
                merged.append(carry)

        # Step 4 — split oversized paragraphs at sentence boundaries
        final: List[str] = []
        for para in merged:
            if len(para) <= self._MAX_PARA_CHARS:
                final.append(para)
            else:
                sentences = _SENTENCE_END_RE.split(para)
                current = ""
                for sent in sentences:
                    if len(current) + len(sent) + 1 > self._MAX_PARA_CHARS and current:
                        final.append(current.strip())
                        current = sent
                    else:
                        current = (current + " " + sent).strip() if current else sent
                if current:
                    final.append(current.strip())

        return final

    def _classify_legal_chunk_type(self, text: str) -> ChunkType:
        """Classify a paragraph into a legal ChunkType based on its content."""
        if _PREAMBLE_RE.search(text):
            return ChunkType.PREAMBLE
        if _DEFINITION_RE.search(text):
            return ChunkType.DEFINITION
        if _PROVISION_RE.search(text):
            return ChunkType.PROVISION
        # Numbered paragraph at the start → treat as a provision by default
        if re.match(r'^\s*\d{1,2}[\.\)]\s', text):
            return ChunkType.PROVISION
        return ChunkType.CONCEPT

    def _create_paragraph_chunks(
        self,
        text: str,
        document_id: str,
        page_start: int | None = None,
        section_title: str = "Document Content",
    ) -> List[HierarchicalChunk]:
        """Create chunks by splitting text on paragraph/clause boundaries.

        Each legal paragraph or numbered clause becomes its own chunk so that
        specific facts (account numbers, dates, names) are never split across
        chunk boundaries.
        """
        chunks: List[HierarchicalChunk] = []
        text = text.strip()
        if not text:
            return chunks

        paragraphs = self._split_legal_paragraphs(text)

        for idx, para in enumerate(paragraphs, start=1):
            if not para:
                continue
            chunk_type = self._classify_legal_chunk_type(para)
            key_terms = self._extract_key_terms(para)
            equations = self._extract_equations(para)
            chunk_id = f"{document_id}_p{idx}"

            chunk = HierarchicalChunk(
                chunk_id=chunk_id,
                document_id=document_id,
                text=para,
                topic_metadata=TopicMetadata(
                    chapter_num=None,
                    chapter_title=section_title,
                    section_num=f"Para {idx}",
                    section_title=section_title,
                    page_start=page_start,
                    page_end=page_start,
                ),
                chunk_metadata=ChunkMetadata(
                    chunk_type=chunk_type,
                    topic_id=document_id,
                    key_terms=key_terms,
                    equations=equations,
                    has_equations=bool(equations),
                    has_diagrams=self._has_diagram_reference(para),
                ),
            )
            chunks.append(chunk)

        logger.info(f"Created {len(chunks)} paragraph chunks from {len(paragraphs)} paragraphs")
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
