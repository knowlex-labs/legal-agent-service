"""
Web article parser using BeautifulSoup and readability-lxml.
"""

import logging
import requests
from typing import List, Optional
from pathlib import Path
from urllib.parse import urlparse
from bs4 import BeautifulSoup
from readability import Document

from .base_parser import BaseParser
from .models import ParsedContent, ParsedMetadata, ContentSection

logger = logging.getLogger(__name__)


class WebParser(BaseParser):
    """
    Web article parser for static HTML content.

    Features:
    - Uses readability-lxml for article extraction
    - Extracts heading hierarchy (h1, h2, h3)
    - Supports: Medium, Wikipedia, blogs, and general articles
    - Does NOT support JavaScript-rendered content

    Limitations:
    - No JavaScript execution (use Playwright/Selenium if needed)
    - Static HTML only
    """

    def __init__(
        self,
        user_agent: str = "RAG-Engine/1.0",
        timeout: int = 30
    ):
        super().__init__()
        self.user_agent = user_agent
        self.timeout = timeout

    def can_handle(self, source: str | Path) -> bool:
        """Check if source is a valid HTTP/HTTPS URL."""
        if isinstance(source, Path):
            return False

        try:
            parsed = urlparse(str(source))
            return parsed.scheme in ['http', 'https'] and bool(parsed.netloc)
        except Exception:
            return False

    def parse(self, source: str | Path) -> ParsedContent:
        """
        Parse web article and extract content.

        Args:
            source: Web URL

        Returns:
            ParsedContent with hierarchical sections

        Raises:
            ValueError: If URL is invalid or content cannot be fetched
        """
        if isinstance(source, Path):
            raise ValueError(f"Web parser requires a URL, not a file path: {source}")

        self.validate_source(source)

        url = str(source)

        try:
            html = self._fetch_html(url)
            doc = Document(html)
            title = doc.title()
            content_html = doc.summary()
            soup = BeautifulSoup(content_html, 'lxml')
            metadata = self._extract_metadata(soup, url, title)
            sections = self._extract_sections(soup)
            full_text = soup.get_text(separator='\n', strip=True)
            has_code = self._has_code_blocks(soup)

            return ParsedContent(
                text=full_text,
                metadata=metadata,
                sections=sections,
                source_type='web',
                has_equations=False,
                has_diagrams=False,
                has_code_blocks=has_code
            )

        except requests.exceptions.HTTPError as e:
            logger.error(f"HTTP Error fetching {url}: {e.response.status_code} {e.response.reason}")
            raise ValueError(f"Failed to fetch content: HTTP {e.response.status_code} {e.response.reason} for URL: {url}")
        except requests.RequestException as e:
            logger.error(f"Failed to fetch URL {url}: {e}")
            raise ValueError(f"Failed to fetch web page: {str(e)}")
        except Exception as e:
            logger.error(f"Error parsing web page {url}: {e}", exc_info=True)
            raise ValueError(f"Failed to parse web page: {e}")

    def _fetch_html(self, url: str) -> str:
        headers = {
            'User-Agent': self.user_agent
        }
        response = requests.get(url, headers=headers, timeout=self.timeout)
        response.raise_for_status()
        return response.text

    def _extract_metadata(self, soup: BeautifulSoup, url: str, title: str) -> ParsedMetadata:
        """Extract web page metadata."""

        # Try to extract author
        author = None
        author_meta = soup.find('meta', attrs={'name': 'author'})
        if author_meta and author_meta.get('content'):
            author = author_meta.get('content')

        # Try to extract publish date
        publish_date = None
        date_meta = soup.find('meta', attrs={'property': 'article:published_time'})
        if date_meta and date_meta.get('content'):
            publish_date = date_meta.get('content')

        # Extract domain
        parsed_url = urlparse(url)
        domain = parsed_url.netloc

        return ParsedMetadata(
            title=title,
            url=url,
            domain=domain,
            author=author,
            publish_date=publish_date
        )

    def _extract_sections(self, soup: BeautifulSoup) -> List[ContentSection]:
        """
        Extract hierarchical sections based on headings (h1, h2, h3).

        Strategy:
        - h1: Level 1 (main sections)
        - h2: Level 2 (subsections)
        - h3: Level 3 (sub-subsections)
        - Paragraphs between headings are grouped with their parent heading
        """
        sections = []

        # Find all headings and content
        elements = soup.find_all(['h1', 'h2', 'h3', 'p', 'ul', 'ol'])

        current_h1 = None
        current_h2 = None
        current_h3 = None
        current_content = []

        def save_section(level, title, content_list):
            """Helper to save accumulated content as a section."""
            if not content_list:
                return

            text = '\n'.join(content_list)
            if not text.strip():
                return

            sections.append(ContentSection(
                level=level,
                text=text.strip(),
                title=title,
                parent_id=None,  # Could enhance to track parent relationships
                page_number=None,
                timestamp=None,
                section_id=f"section_{len(sections)}"
            ))

        for elem in elements:
            tag_name = elem.name

            if tag_name == 'h1':
                # Save previous h3 section if exists
                if current_h3:
                    save_section(3, current_h3, current_content)
                    current_content = []
                    current_h3 = None

                # Save previous h2 section if exists
                if current_h2:
                    save_section(2, current_h2, current_content)
                    current_content = []
                    current_h2 = None

                # Save previous h1 section if exists
                if current_h1:
                    save_section(1, current_h1, current_content)
                    current_content = []

                # Start new h1 section
                current_h1 = elem.get_text(strip=True)

            elif tag_name == 'h2':
                # Save previous h3 section
                if current_h3:
                    save_section(3, current_h3, current_content)
                    current_content = []
                    current_h3 = None

                # Save previous h2 section
                if current_h2:
                    save_section(2, current_h2, current_content)
                    current_content = []

                # Start new h2 section
                current_h2 = elem.get_text(strip=True)

            elif tag_name == 'h3':
                # Save previous h3 section
                if current_h3:
                    save_section(3, current_h3, current_content)
                    current_content = []

                # Start new h3 section
                current_h3 = elem.get_text(strip=True)

            else:
                # It's content (p, ul, ol)
                text = elem.get_text(separator=' ', strip=True)
                if text:
                    current_content.append(text)

        # Save final section
        if current_h3:
            save_section(3, current_h3, current_content)
        elif current_h2:
            save_section(2, current_h2, current_content)
        elif current_h1:
            save_section(1, current_h1, current_content)
        elif current_content:
            # Content without any headings
            save_section(1, "Article Content", current_content)

        logger.info(f"Extracted {len(sections)} sections from web page")
        return sections

    def _has_code_blocks(self, soup: BeautifulSoup) -> bool:
        """Check if article contains code blocks."""
        code_tags = soup.find_all(['code', 'pre'])
        return len(code_tags) > 0

    def validate_source(self, source: str | Path) -> None:
        """Validate web URL."""
        super().validate_source(source)

        if isinstance(source, Path):
            raise ValueError("Web parser requires a URL, not a file path")

        if not self.can_handle(source):
            raise ValueError(f"Invalid web URL: {source}")
