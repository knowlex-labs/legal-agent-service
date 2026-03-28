import pdfplumber
import logging
from typing import Optional, List, Dict, Any

logger = logging.getLogger(__name__)

class PDFHelper:
    @staticmethod
    def extract_first_page_text(file_path: str) -> Optional[str]:
        try:
            with pdfplumber.open(file_path) as pdf:
                if len(pdf.pages) == 0:
                    return None
                first_page = pdf.pages[0]
                return first_page.extract_text()
        except Exception as e:
            logger.error(f"Failed to read first page from {file_path}: {e}")
            return None

    @staticmethod
    def get_page_count(file_path: str) -> int:
        try:
            with pdfplumber.open(file_path) as pdf:
                return len(pdf.pages)
        except Exception:
            return 0

    @staticmethod
    def extract_lines_with_font_info(page) -> List[Dict[str, Any]]:
        chars = page.chars
        if not chars:
            return []

        lines_dict = {}
        for char in chars:
            if 'text' not in char or not char['text'].strip():
                continue

            y0 = round(char.get('y0', 0))
            if y0 not in lines_dict:
                lines_dict[y0] = {
                    'chars': [],
                    'font_sizes': [],
                    'y0': y0
                }

            lines_dict[y0]['chars'].append(char['text'])
            if 'size' in char:
                lines_dict[y0]['font_sizes'].append(char['size'])

        lines = []
        for y0, line_data in sorted(lines_dict.items()):
            text = ''.join(line_data['chars'])
            avg_font_size = (
                sum(line_data['font_sizes']) / len(line_data['font_sizes'])
                if line_data['font_sizes'] else 12
            )

            lines.append({
                'text': text,
                'font_size': avg_font_size,
                'y0': y0
            })

        return lines

    @staticmethod
    def extract_content_between_pages(pdf, start_page: int, end_page: int, header_text: str = None, next_header_text: str = None) -> str:
        content_parts = []

        for page_idx in range(start_page, min(end_page + 1, len(pdf.pages))):
            page = pdf.pages[page_idx]
            text = page.extract_text() or ""

            if page_idx == start_page and header_text:
                lines = text.split('\n')
                header_found = False
                for i, line in enumerate(lines):
                    if header_text in line:
                        header_found = True
                        content_parts.append('\n'.join(lines[i+1:]))
                        break
                if not header_found:
                    content_parts.append(text)

            elif page_idx == end_page and next_header_text:
                lines = text.split('\n')
                for i, line in enumerate(lines):
                    if next_header_text in line:
                        content_parts.append('\n'.join(lines[:i]))
                        break
                else:
                    content_parts.append(text)

            else:
                content_parts.append(text)

        return '\n'.join(content_parts)