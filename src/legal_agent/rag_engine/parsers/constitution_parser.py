"""
Constitution of India specific parser using LlamaParse for high-quality structure extraction.
Maps to the legal ontology: Constitution -> Statute, Articles -> Provisions
"""

import logging
import re
import asyncio
from typing import List, Dict, Any, Optional, Union
from pathlib import Path

from llama_parse import LlamaParse
from .base_parser import BaseParser
from .legal_models import LegalParsedContent, LegalDocument, LegalHierarchy, LegalProvision, ParsedMetadata, ContentSection
from legal_agent.config import get_settings

logger = logging.getLogger(__name__)


class ConstitutionParser(BaseParser):
    """
    Parser specifically designed for the Constitution of India.
    Uses LlamaParse for premium parsing quality and extracts constitutional hierarchy.
    """

    def __init__(self):
        """Initialize with LlamaParse configuration."""
        self.llamaparse = LlamaParse(
            result_type="markdown",  # Better for structure extraction
            api_key=get_settings().llama_cloud_api_key,
            verbose=True,
            # Premium parsing settings for legal documents - using new parameter name
            content_guideline_instruction="Extract the complete hierarchical structure of the Constitution including Parts, Articles, Schedules with their titles and full text. Preserve cross-references between articles."
        )

    def can_handle(self, source: Union[str, Path]) -> bool:
        """Check if this is a Constitution document."""
        source_str = str(source).lower()
        constitution_indicators = [
            "constitution", "constitutional", "भारत का संविधान", "भारतीय संविधान",
            "20240716890312078", "samvidhan", "bharatiya", "fundamental_rights"
        ]
        return any(indicator in source_str for indicator in constitution_indicators)

    async def parse_async(self, source: Union[str, Path]) -> LegalParsedContent:
        """
        Async version of parse for Constitution document with hierarchical structure extraction.

        Returns:
            LegalParsedContent with constitutional structure mapped to legal ontology
        """
        try:
            logger.info(f"Starting Constitution parsing with LlamaParse: {source}")

            # Validate source
            self.validate_source(source)

            # Parse with LlamaParse (use await instead of asyncio.run)
            docs = await self.llamaparse.aload_data(str(source))
            full_text = "\n".join([doc.text for doc in docs])

            logger.info(f"LlamaParse extracted {len(full_text)} characters from Constitution")

            # Extract constitutional structure
            legal_document = self._extract_constitutional_structure(full_text)

            # Create sections for compatibility with existing system
            sections = self._create_content_sections(legal_document)

            # Create metadata
            metadata = ParsedMetadata(
                title="Constitution of India",
                page_count=len(docs),
                extracted_at=legal_document.parsed_at
            )

            return LegalParsedContent(
                text=full_text,
                metadata=metadata,
                sections=sections,
                source_type="constitution",
                legal_document=legal_document,
                has_equations=False,
                has_diagrams=True,  # Constitution has tables/schedules
                has_code_blocks=False
            )

        except Exception as e:
            logger.error(f"Constitution parsing failed: {e}", exc_info=True)
            raise

    def parse(self, source: Union[str, Path]) -> LegalParsedContent:
        """
        Sync wrapper for async parse method. Required by BaseParser interface.
        """
        try:
            # Try to get existing event loop
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # If we're in an async context, we need to create a new event loop
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as executor:
                    future = executor.submit(asyncio.run, self.parse_async(source))
                    return future.result()
            else:
                # No running loop, safe to use asyncio.run
                return asyncio.run(self.parse_async(source))
        except RuntimeError:
            # Fallback: create new event loop in thread
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(asyncio.run, self.parse_async(source))
                return future.result()

    def _extract_constitutional_structure(self, text: str) -> LegalDocument:
        """
        Extract the hierarchical structure of the Constitution.
        Maps to our legal ontology.
        """
        logger.info("Extracting constitutional structure...")

        # Initialize structure containers
        hierarchy = LegalHierarchy()
        provisions = []
        internal_references = {}

        # Extract Parts (I-XXV)
        parts = self._extract_parts(text)
        hierarchy.parts = parts
        logger.info(f"Extracted {len(parts)} Parts")

        # Extract Articles (1-448)
        articles = self._extract_articles(text, parts)
        provisions.extend(articles)
        logger.info(f"Extracted {len(articles)} Articles")

        # Extract Schedules (1-12)
        schedules = self._extract_schedules(text)
        hierarchy.schedules = schedules
        provisions.extend(schedules)
        logger.info(f"Extracted {len(schedules)} Schedules")

        # Extract cross-references
        internal_references = self._extract_cross_references(provisions)
        logger.info(f"Extracted {len(internal_references)} cross-reference mappings")

        hierarchy.provisions = provisions

        return LegalDocument(
            name="Constitution of India",
            document_type="CONSTITUTIONAL",
            year=1950,
            hierarchy=hierarchy,
            total_provisions=len(provisions),
            parsing_method="llamaparse",
            internal_references=internal_references
        )

    def _extract_parts(self, text: str) -> List[Dict[str, Any]]:
        """Extract constitutional parts with their structure."""
        parts = []

        # Known constitutional parts structure
        part_patterns = [
            (r'PART\s+([IVX]+)[\s\-–—]*(.+?)(?=PART|$)', "roman"),
            (r'भाग\s+([IVX]+)[\s\-–—]*(.+?)(?=भाग|$)', "roman_hindi"),
        ]

        for pattern, type_name in part_patterns:
            matches = re.finditer(pattern, text, re.IGNORECASE | re.DOTALL)
            for match in matches:
                part_number = match.group(1).strip()
                part_title = match.group(2).strip()

                # Clean up title (remove extra whitespace, newlines)
                part_title = re.sub(r'\s+', ' ', part_title)
                part_title = part_title.split('\n')[0]  # Take first line only

                parts.append({
                    'number': part_number,
                    'title': part_title,
                    'pattern_type': type_name
                })

        # If automatic extraction fails, use known structure
        if len(parts) < 20:  # Constitution has 25 parts, should find most
            logger.warning("Part extraction incomplete, using known structure")
            parts = self._get_known_constitutional_parts()

        return parts

    def _extract_articles(self, text: str, parts: List[Dict]) -> List[LegalProvision]:
        """Extract constitutional articles with deduplication."""
        # Use dict to deduplicate by article number
        # Store tuple of (priority, article) - higher priority wins
        articles_dict: Dict[str, tuple] = {}

        # Find where actual article content starts (after Table of Contents)
        # Look for "PART I" followed by "THE UNION AND ITS TERRITORY" and article content
        content_start = 0
        part1_match = re.search(r'#\s*PART\s+I\s*\n.*?#\s*THE\s+UNION', text, re.IGNORECASE | re.DOTALL)
        if part1_match and part1_match.start() > 50000:
            # Found Part I content section (not TOC), start searching from there
            content_start = part1_match.start()
            logger.info(f"Skipping TOC, extracting articles from position {content_start}")

        # Work on text after TOC
        search_text = text[content_start:]

        # Article patterns for Constitution - ordered by priority
        article_patterns = [
            # Markdown header format: # 1. Name and territory of the Union.
            (r'#\s*(\d+[A-Z]*)\.?\s+([^\n#]+?)(?=\n|$)', 2),
            # Explicit Article format (rare in this document)
            (r'Article\s+(\d+[A-Z]*)\.\s*([^\n]+?)(?=\n|\.|—)', 3),
        ]

        for pattern, priority in article_patterns:
            matches = re.finditer(pattern, search_text, re.IGNORECASE | re.MULTILINE)
            for match in matches:
                article_num = match.group(1).strip()
                article_title = match.group(2).strip()

                # Skip invalid article numbers (Constitution articles are 1-448+)
                try:
                    num = int(''.join(c for c in article_num if c.isdigit()))
                    if num < 1 or num > 500:
                        continue
                except ValueError:
                    continue

                # Skip if title is too short
                if len(article_title) < 15:
                    continue

                # Skip table content
                if article_title.startswith('|') or '|' in article_title[:10]:
                    continue

                # Check if this is in a Schedule section (skip schedule list items)
                # Use absolute position in original text
                abs_pos = content_start + match.start()
                context_start = max(0, abs_pos - 500)
                context = text[context_start:abs_pos].upper()
                if 'SCHEDULE' in context:
                    continue

                # Extract full article text (next few paragraphs)
                # Use absolute position in original text
                abs_end = content_start + match.end()
                article_text = self._extract_article_text(text, abs_end, article_num)

                # Determine which part this article belongs to
                part_number = self._determine_article_part(article_num)

                # Extract cross-references from article text
                references = self._find_article_references(article_text)

                article = LegalProvision(
                    id=f"Art-{article_num}",
                    number=article_num,
                    title=article_title,
                    text=article_text,
                    part_number=part_number,
                    statute_name="Constitution of India",
                    provision_type="ARTICLE",
                    references=references
                )

                # Deduplicate: prefer higher priority, then longer text
                if article_num not in articles_dict:
                    articles_dict[article_num] = (priority, article)
                else:
                    existing_priority, existing_article = articles_dict[article_num]
                    if priority > existing_priority or (priority == existing_priority and len(article_text) > len(existing_article.text)):
                        articles_dict[article_num] = (priority, article)

        return [article for _, article in articles_dict.values()]

    def _extract_schedules(self, text: str) -> List[LegalProvision]:
        """Extract constitutional schedules with deduplication."""
        # Use dict to deduplicate by schedule number - keep the one with longest text
        schedules_dict: Dict[str, LegalProvision] = {}

        # Known schedule mapping
        schedule_mapping = {
            "FIRST": "1", "SECOND": "2", "THIRD": "3", "FOURTH": "4",
            "FIFTH": "5", "SIXTH": "6", "SEVENTH": "7", "EIGHTH": "8",
            "NINTH": "9", "TENTH": "10", "ELEVENTH": "11", "TWELFTH": "12"
        }

        # Schedule patterns - more specific to avoid false matches
        schedule_patterns = [
            r'(?:THE\s+)?(FIRST|SECOND|THIRD|FOURTH|FIFTH|SIXTH|SEVENTH|EIGHTH|NINTH|TENTH|ELEVENTH|TWELFTH)\s+SCHEDULE\s*(?:\[.*?\])?\s*\n(.*?)(?=(?:THE\s+)?(?:FIRST|SECOND|THIRD|FOURTH|FIFTH|SIXTH|SEVENTH|EIGHTH|NINTH|TENTH|ELEVENTH|TWELFTH)\s+SCHEDULE|\n\n\n|$)',
        ]

        for pattern in schedule_patterns:
            matches = re.finditer(pattern, text, re.IGNORECASE | re.DOTALL)
            for match in matches:
                schedule_name = match.group(1).strip().upper()
                schedule_content = match.group(2).strip()

                schedule_number = schedule_mapping.get(schedule_name, schedule_name)
                schedule_title = f"{schedule_name.title()} Schedule"

                schedule = LegalProvision(
                    id=f"Schedule-{schedule_number}",
                    number=schedule_number,
                    title=schedule_title,
                    text=schedule_content,
                    statute_name="Constitution of India",
                    provision_type="SCHEDULE"
                )

                # Deduplicate: keep the schedule with the longest text
                if schedule_number not in schedules_dict or len(schedule_content) > len(schedules_dict[schedule_number].text):
                    schedules_dict[schedule_number] = schedule

        return list(schedules_dict.values())

    def _extract_cross_references(self, provisions: List[LegalProvision]) -> Dict[str, List[str]]:
        """Extract cross-references between provisions."""
        references = {}

        for provision in provisions:
            refs = self._find_article_references(provision.text)
            if refs:
                references[provision.id] = refs

        return references

    def _find_article_references(self, text: str) -> List[str]:
        """Find references to other articles in text."""
        references = []

        # Patterns for article references
        ref_patterns = [
            r'Article\s+(\d+[A-Z]*)',
            r'अनुच्छेद\s+(\d+[A-Z]*)',
            r'clause\s+\(.*?\)\s+of\s+Article\s+(\d+[A-Z]*)',
            r'sub-article\s+\(.*?\)\s+of\s+Article\s+(\d+[A-Z]*)',
        ]

        for pattern in ref_patterns:
            matches = re.finditer(pattern, text, re.IGNORECASE)
            for match in matches:
                ref_num = match.group(1)
                ref_id = f"Art-{ref_num}"
                if ref_id not in references:
                    references.append(ref_id)

        return references

    def _extract_article_text(self, full_text: str, start_pos: int, article_num: str) -> str:
        """Extract the complete text of an article."""
        # Look ahead for next article or major section
        text_segment = full_text[start_pos:start_pos + 2000]  # Look ahead 2000 chars

        # Find natural break points
        break_patterns = [
            r'Article\s+\d+[A-Z]*\.',  # Next article (handles 51A, 21A, etc.)
            r'PART\s+[IVX]+',
            r'\n\n\n',  # Multiple line breaks
            r'SCHEDULE'
        ]

        # Try to find next article number for more precise breaking
        try:
            # Extract numeric part from current article (e.g., "51A" -> 51)
            numeric_part = re.sub(r'[A-Z]', '', article_num)
            if numeric_part:  # Only proceed if we have numeric content
                current_num = int(numeric_part)
                next_article_patterns = [
                    rf'Article\s+{current_num + 1}(?:\.|[^0-9A-Z])',  # Next numbered article
                    rf'Article\s+{article_num}[A-Z](?:\.|[^A-Z])'     # Same number with next letter (51A -> 51B)
                ]
                break_patterns = next_article_patterns + break_patterns
        except (ValueError, TypeError):
            # If we can't parse the number, just use generic patterns
            pass

        for pattern in break_patterns:
            match = re.search(pattern, text_segment, re.IGNORECASE)
            if match:
                return text_segment[:match.start()].strip()

        return text_segment.strip()

    def _determine_article_part(self, article_num: str) -> Optional[str]:
        """Determine which Part an article belongs to based on number."""
        try:
            # Extract numeric part from current article (e.g., "51A" -> "51")
            numeric_part = re.sub(r'[A-Z]', '', article_num)
            if not numeric_part:  # No numeric content
                return None

            num = int(numeric_part)  # Remove letter suffixes

            # Known ranges for constitutional parts
            part_ranges = {
                "I": (1, 4), "II": (5, 11), "III": (12, 35), "IV": (36, 51),
                "IVA": (51, 51), "V": (52, 151), "VI": (152, 237), "VIII": (239, 242),
                "IX": (243, 243), "IXA": (243, 243), "X": (244, 244), "XI": (245, 263),
                "XII": (264, 300), "XIII": (301, 307), "XIV": (308, 323), "XIVA": (323, 323),
                "XV": (324, 329), "XVI": (330, 342), "XVII": (343, 351), "XVIII": (352, 360),
                "XIX": (361, 367), "XX": (368, 368), "XXI": (369, 392), "XXII": (393, 395)
            }

            for part, (start, end) in part_ranges.items():
                if start <= num <= end:
                    return part
        except (ValueError, TypeError):
            pass

        return None

    def _get_known_constitutional_parts(self) -> List[Dict[str, Any]]:
        """Fallback: Return known constitutional parts structure."""
        return [
            {"number": "I", "title": "The Union and Its Territory"},
            {"number": "II", "title": "Citizenship"},
            {"number": "III", "title": "Fundamental Rights"},
            {"number": "IV", "title": "Directive Principles of State Policy"},
            {"number": "IVA", "title": "Fundamental Duties"},
            {"number": "V", "title": "The Union"},
            {"number": "VI", "title": "The States"},
            {"number": "VIII", "title": "Union Territories"},
            {"number": "IX", "title": "The Panchayats"},
            {"number": "IXA", "title": "The Municipalities"},
            {"number": "IXB", "title": "Co-operative Societies"},
            {"number": "X", "title": "Scheduled and Tribal Areas"},
            {"number": "XI", "title": "Union-State Relations"},
            {"number": "XII", "title": "Finance, Property, Contracts"},
            {"number": "XIII", "title": "Trade and Commerce"},
            {"number": "XIV", "title": "Services"},
            {"number": "XIVA", "title": "Tribunals"},
            {"number": "XV", "title": "Elections"},
            {"number": "XVI", "title": "Special Provisions"},
            {"number": "XVII", "title": "Official Language"},
            {"number": "XVIII", "title": "Emergency Provisions"},
            {"number": "XIX", "title": "Miscellaneous"},
            {"number": "XX", "title": "Amendment"},
            {"number": "XXI", "title": "Temporary/Transitional"},
            {"number": "XXII", "title": "Short Title, Commencement, Repeals"}
        ]

    def _create_content_sections(self, legal_document: LegalDocument) -> List[ContentSection]:
        """Create ContentSection objects for compatibility with existing system."""
        sections = []

        # Add parts as level 1 headers
        for part in legal_document.hierarchy.parts:
            sections.append(ContentSection(
                level=1,
                title=f"Part {part['number']}",
                text=part['title']
            ))

        # Add articles as level 2 sections
        for provision in legal_document.hierarchy.provisions:
            if provision.provision_type == "ARTICLE":
                sections.append(ContentSection(
                    level=2,
                    title=f"Article {provision.number}",
                    text=provision.text
                ))

        # Add schedules as level 1 headers
        for provision in legal_document.hierarchy.provisions:
            if provision.provision_type == "SCHEDULE":
                sections.append(ContentSection(
                    level=1,
                    title=f"Schedule {provision.number}",
                    text=provision.text
                ))

        return sections