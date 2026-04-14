"""
PDF parser using PyMuPDF with font-based header detection and legal document support.
"""

import re
import logging
from typing import List, Dict, Any, Optional
from pathlib import Path
import fitz

from .base_parser import BaseParser
from .models import ParsedContent, ParsedMetadata, ContentSection

logger = logging.getLogger(__name__)


class PDFParser(BaseParser):
    """
    PDF parser that extracts hierarchical structure using font size analysis.

    Methodology:
    1. Analyze font sizes across entire document
    2. Identify headers based on font size thresholds:
       - Chapter headers: 1.5x median font size
       - Section headers: 1.2x median font size
    3. Detect bold/italic formatting (legal citations)
    4. Extract case and statute citations
    5. Fallback to regex-based header detection if font info unavailable
    6. Extract content sections with page numbers and styling metadata
    """

    def __init__(self):
        super().__init__()

        self.chapter_pattern = re.compile(
            r'^(?:chapter|ch\.?)\s*(\d+)[:\-\s]*(.+?)$',
            re.IGNORECASE
        )
        self.section_pattern = re.compile(
            r'^(\d+(?:\.\d+)+)[:\-\s]+(.+?)$',
            re.IGNORECASE
        )

        self.equation_pattern = re.compile(r'[=+\-*/]\s*[A-Za-z0-9]|[A-Za-z]\s*=')
        self.formula_pattern = re.compile(r'([A-Z][a-z]?\s*=|∑|∫|√|π|α|β|γ|Δ)')

        self.diagram_keywords = ['figure', 'diagram', 'illustration', 'graph', 'chart', 'image']

        self.case_citation_pattern = re.compile(
            r'([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\s+v\.?\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\s*(?:\((\d{4})\))?',
            re.IGNORECASE
        )
        self.statute_pattern = re.compile(
            r'(?:Article|Art\.|Section|Sec\.)\s+(\d+[A-Z]?(?:\([a-z]\))?)',
            re.IGNORECASE
        )

    def can_handle(self, source: str | Path) -> bool:
        """Check if source is a PDF file."""
        if isinstance(source, str):
            source = Path(source)

        return source.exists() and source.suffix.lower() == '.pdf'

    def parse(self, source: str | Path) -> ParsedContent:
        """
        Parse PDF and extract hierarchical structure.

        Args:
            source: Path to PDF file

        Returns:
            ParsedContent with sections and metadata

        Raises:
            FileNotFoundError: If PDF file doesn't exist
            ValueError: If PDF cannot be parsed
        """
        if isinstance(source, str):
            source = Path(source)

        self.validate_source(source)

        if not self.can_handle(source):
            raise ValueError(f"Cannot handle file: {source}. Must be a PDF file.")

        logger.info(f"Parsing PDF: {source}")

        try:
            doc = fitz.open(source)

            metadata = self._extract_metadata(doc, source)

            full_text = ""
            for page in doc:
                full_text += page.get_text() + "\n"

            # Detect scanned PDF: less than 100 chars per page means no text layer
            chars_per_page = len(full_text.strip()) / max(doc.page_count, 1)
            if chars_per_page < 100:
                logger.info(f"Scanned PDF detected ({chars_per_page:.0f} chars/page) — using Gemini Vision OCR")
                from legal_agent.utils.ocr import ocr_pdf_with_gemini
                pdf_bytes = source.read_bytes()
                full_text = ocr_pdf_with_gemini(pdf_bytes, output_format="plain")
                doc.close()
                sections = [ContentSection(level=1, text=full_text, title=metadata.title, page_number=1)]
                return ParsedContent(
                    text=full_text,
                    metadata=metadata,
                    sections=sections,
                    source_type='pdf',
                    has_diagrams=True,
                )

            headers = self._extract_headers_with_font_sizes(doc)
            logger.info(f"Extracted {len(headers)} headers from PDF")

            sections = self._build_sections_from_headers(doc, headers)

            has_equations = bool(self.equation_pattern.search(full_text) or self.formula_pattern.search(full_text))
            has_diagrams = any(keyword in full_text.lower() for keyword in self.diagram_keywords)

            doc.close()

            return ParsedContent(
                text=full_text,
                metadata=metadata,
                sections=sections,
                source_type='pdf',
                has_equations=has_equations,
                has_diagrams=has_diagrams,
                has_code_blocks=False
            )

        except FileNotFoundError:
            logger.error(f"PDF file not found: {source}")
            raise
        except Exception as e:
            logger.error(f"Error parsing PDF {source}: {e}", exc_info=True)
            raise ValueError(f"Failed to parse PDF: {e}")

    def _extract_metadata(self, doc, file_path: Path) -> ParsedMetadata:
        pdf_metadata = doc.metadata or {}

        title = pdf_metadata.get('title') or file_path.stem
        page_count = doc.page_count

        return ParsedMetadata(
            title=title,
            url=None,
            page_count=page_count
        )

    def _extract_headers_with_font_sizes(self, doc) -> List[Dict[str, Any]]:
        headers = []
        font_sizes = []

        for page in doc:
            text_dict = page.get_text("dict")
            for block in text_dict.get("blocks", []):
                if block.get("type") == 0:
                    for line in block.get("lines", []):
                        for span in line.get("spans", []):
                            font_sizes.append(span["size"])

        if not font_sizes:
            logger.warning("No font information found, using text-based header detection")
            return self._extract_headers_text_based(doc)

        font_sizes.sort()
        median_size = font_sizes[len(font_sizes) // 2]

        header_threshold = median_size * 1.2
        chapter_threshold = median_size * 1.5

        logger.info(f"Font size thresholds - Median: {median_size:.1f}, Section: {header_threshold:.1f}, Chapter: {chapter_threshold:.1f}")

        current_chapter = None

        for page_num, page in enumerate(doc, start=1):
            lines = self._extract_lines_with_font_info(page)

            for line_data in lines:
                text = line_data['text'].strip()
                font_size = line_data['font_size']
                is_bold = line_data['is_bold']

                if not text or len(text) < 3 or len(text) > 200:
                    continue

                is_chapter_header = font_size >= chapter_threshold
                is_section_header = header_threshold <= font_size < chapter_threshold

                if is_bold and font_size > median_size:
                    is_section_header = True

                if is_chapter_header:
                    chapter_match = self.chapter_pattern.match(text)
                    if chapter_match:
                        chapter_num = int(chapter_match.group(1))
                        chapter_title = chapter_match.group(2).strip()
                    else:
                        chapter_num = None
                        chapter_title = text

                    current_chapter = {
                        'type': 'chapter',
                        'level': 1,
                        'chapter_num': chapter_num,
                        'chapter_title': chapter_title,
                        'section_num': None,
                        'section_title': None,
                        'page': page_num,
                        'y_position': line_data['y0'],
                        'text': text,
                        'font_size': font_size,
                        'is_bold': is_bold,
                        'is_italic': line_data['is_italic']
                    }
                    headers.append(current_chapter)

                elif is_section_header:
                    section_match = self.section_pattern.match(text)
                    if section_match:
                        section_num = section_match.group(1)
                        section_title = section_match.group(2).strip()
                    else:
                        section_num = None
                        section_title = text

                    headers.append({
                        'type': 'section',
                        'level': 2,
                        'chapter_num': current_chapter['chapter_num'] if current_chapter else None,
                        'chapter_title': current_chapter['chapter_title'] if current_chapter else None,
                        'section_num': section_num,
                        'section_title': section_title,
                        'page': page_num,
                        'y_position': line_data['y0'],
                        'text': text,
                        'font_size': font_size,
                        'is_bold': is_bold,
                        'is_italic': line_data['is_italic']
                    })

        if not headers:
            headers.append({
                'type': 'chapter',
                'level': 1,
                'chapter_num': None,
                'chapter_title': 'Document Content',
                'section_num': None,
                'section_title': None,
                'page': 1,
                'y_position': 0,
                'text': 'Document Content',
                'font_size': 12,
                'is_bold': False,
                'is_italic': False
            })

        return headers

    def _extract_lines_with_font_info(self, page) -> List[Dict[str, Any]]:
        text_dict = page.get_text("dict")
        lines_dict = {}

        for block in text_dict.get("blocks", []):
            if block.get("type") != 0:
                continue

            for line in block.get("lines", []):
                y0 = round(line["bbox"][1])

                if y0 not in lines_dict:
                    lines_dict[y0] = {'spans': [], 'y0': y0}

                lines_dict[y0]['spans'].extend(line.get("spans", []))

        lines = []
        for y0, line_data in sorted(lines_dict.items()):
            spans = line_data['spans']
            if not spans:
                continue

            text = ''.join(span['text'] for span in spans)
            font_sizes = [span['size'] for span in spans]
            avg_font_size = sum(font_sizes) / len(font_sizes)

            is_bold = any(self._is_bold(span) for span in spans)
            is_italic = any(self._is_italic(span) for span in spans)

            font_names = [span['font'] for span in spans]
            font_name = max(set(font_names), key=font_names.count)

            lines.append({
                'text': text,
                'font_size': avg_font_size,
                'y0': y0,
                'is_bold': is_bold,
                'is_italic': is_italic,
                'font_name': font_name
            })

        return lines

    def _is_bold(self, span: dict) -> bool:
        flags = span.get("flags", 0)
        font_name = span.get("font", "").lower()
        is_bold_flag = bool(flags & (1 << 4))
        is_bold_name = any(kw in font_name for kw in ['bold', 'heavy', 'black'])
        return is_bold_flag or is_bold_name

    def _is_italic(self, span: dict) -> bool:
        flags = span.get("flags", 0)
        font_name = span.get("font", "").lower()
        is_italic_flag = bool(flags & (1 << 1))
        is_italic_name = any(kw in font_name for kw in ['italic', 'oblique'])
        return is_italic_flag or is_italic_name

    def _extract_headers_text_based(self, doc) -> List[Dict[str, Any]]:
        headers = []
        current_chapter = None

        for page_num, page in enumerate(doc, start=1):
            text = page.get_text() or ""
            lines = text.split('\n')

            for line in lines:
                line = line.strip()
                if not line:
                    continue

                # Try to match chapter pattern
                chapter_match = self.chapter_pattern.match(line)
                if chapter_match:
                    chapter_num = int(chapter_match.group(1))
                    chapter_title = chapter_match.group(2).strip()
                    current_chapter = {
                        'type': 'chapter',
                        'level': 1,
                        'chapter_num': chapter_num,
                        'chapter_title': chapter_title,
                        'section_num': None,
                        'section_title': None,
                        'page': page_num,
                        'y_position': 0,
                        'text': line,
                        'font_size': 16
                    }
                    headers.append(current_chapter)
                    continue

                # Try to match section pattern
                section_match = self.section_pattern.match(line)
                if section_match:
                    section_num = section_match.group(1)
                    section_title = section_match.group(2).strip()
                    headers.append({
                        'type': 'section',
                        'level': 2,
                        'chapter_num': current_chapter['chapter_num'] if current_chapter else None,
                        'chapter_title': current_chapter['chapter_title'] if current_chapter else None,
                        'section_num': section_num,
                        'section_title': section_title,
                        'page': page_num,
                        'y_position': 0,
                        'text': line,
                        'font_size': 14
                    })

        # Default header if none found
        if not headers:
            headers.append({
                'type': 'chapter',
                'level': 1,
                'chapter_num': None,
                'chapter_title': 'Document Content',
                'section_num': None,
                'section_title': None,
                'page': 1,
                'y_position': 0,
                'text': 'Document Content',
                'font_size': 12
            })

        return headers

    def _build_sections_from_headers(self, doc, headers: List[Dict[str, Any]]) -> List[ContentSection]:
        sections = []

        for i, header in enumerate(headers):
            next_header = headers[i + 1] if i + 1 < len(headers) else None

            content = self._extract_content_between_headers(doc, header, next_header)

            if not content.strip():
                continue

            section = ContentSection(
                level=header['level'],
                text=content,
                title=header.get('chapter_title') or header.get('section_title'),
                parent_id=None,
                page_number=header['page'],
                timestamp=None,
                section_id=f"section_{i}",
                font_size=header.get('font_size'),
                is_bold=header.get('is_bold'),
                is_italic=header.get('is_italic')
            )

            sections.append(section)

        return sections

    def _extract_content_between_headers(
        self,
        doc,
        header: Dict[str, Any],
        next_header: Optional[Dict[str, Any]]
    ) -> str:
        start_page = header['page'] - 1
        start_y = header.get('y_position', 0)

        if next_header:
            end_page = next_header['page'] - 1
            end_y = next_header.get('y_position', float('inf'))
        else:
            end_page = doc.page_count - 1
            end_y = float('inf')

        content = ""

        for page_idx in range(start_page, min(end_page + 1, doc.page_count)):
            page = doc[page_idx]
            page_text = page.get_text() or ""

            if page_idx == start_page:
                lines = page_text.split('\n')
                page_text = '\n'.join(lines[1:]) if len(lines) > 1 else ""

            if page_idx == end_page and next_header:
                if page_idx < end_page:
                    content += page_text + "\n"
                else:
                    content += page_text + "\n"
                break

            content += page_text + "\n"

        return content.strip()

    def validate_source(self, source: str | Path) -> None:
        """Validate PDF source exists."""
        super().validate_source(source)

        if isinstance(source, str):
            source = Path(source)

        if not source.exists():
            raise FileNotFoundError(f"PDF file not found: {source}")

        if not source.is_file():
            raise ValueError(f"Source is not a file: {source}")
