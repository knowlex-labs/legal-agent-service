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

class HierarchicalChunkingService:

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

                chunk_type = self._classify_chunk_type_from_header(section.title)

                topic_metadata = TopicMetadata(
                    chapter_num=None,
                    chapter_title=section.title,
                    section_num=None,
                    section_title=section.title,
                    page_start=section.start_page,
                    page_end=section.end_page
                )

                key_terms = self._extract_key_terms(section.text)
                equations = self._extract_equations(section.text)

                chunk_metadata = ChunkMetadata(
                    chunk_type=chunk_type,
                    topic_id=str(uuid.uuid4()),
                    key_terms=key_terms,
                    equations=equations,
                    has_equations=len(equations) > 0,
                    has_diagrams=self._has_diagram_reference(section.text)
                )

                chunk = HierarchicalChunk(
                    chunk_id=str(uuid.uuid4()),
                    document_id=document_id,
                    topic_metadata=topic_metadata,
                    chunk_metadata=chunk_metadata,
                    text=section.text
                )
                chunks.append(chunk)

            logger.info(f"Successfully created {len(chunks)} chunks from {len(parsed_content.sections)} sections")

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
                 
             sub_chunks = self._create_basic_chunks(section.text, document_id)
             
             for sub_chunk in sub_chunks:
                 chunk_num += 1
                 
                 # Update Topic Metadata with section title
                 sub_chunk.topic_metadata.section_title = section.title
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
