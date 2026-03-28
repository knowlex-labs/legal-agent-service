import logging
from typing import Union
from pathlib import Path

from .base_parser import BaseParser
from .models import ParsedContent, ParsedMetadata, ContentSection
from legal_agent.rag_engine.config import Config

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.webp'}

MIME_TYPES = {
    '.png': 'image/png',
    '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.webp': 'image/webp',
}

IMAGE_DESCRIPTION_PROMPT = """Describe this image in comprehensive detail. Include:
- All visible text, labels, and captions
- Any diagrams, charts, tables, or visual elements
- The structure and layout of information
- Key data points or figures shown
- Any relationships or hierarchies depicted

Be thorough and precise. Your description will be used for search and retrieval."""


class ImageParser(BaseParser):

    def __init__(self):
        from google import genai
        self.client = genai.Client(api_key=Config.llm.GEMINI_API_KEY)
        self.vision_model = Config.llm.GEMINI_MODEL

    def can_handle(self, source: Union[str, Path]) -> bool:
        ext = Path(str(source)).suffix.lower()
        return ext in SUPPORTED_EXTENSIONS

    def parse(self, source: Union[str, Path]) -> ParsedContent:
        self.validate_source(source)
        source_path = Path(str(source))

        if not source_path.exists():
            raise FileNotFoundError(f"Image file not found: {source}")

        ext = source_path.suffix.lower()
        if ext not in SUPPORTED_EXTENSIONS:
            raise ValueError(f"Unsupported image format: {ext}")

        mime_type = MIME_TYPES[ext]
        image_bytes = source_path.read_bytes()
        logger.info(f"Read image: {source_path.name} ({len(image_bytes)} bytes, {mime_type})")

        description = self._describe_image(image_bytes, mime_type)
        logger.info(f"Generated description: {len(description)} chars")

        title = source_path.stem
        metadata = ParsedMetadata(
            title=title,
            page_count=1,
        )

        section = ContentSection(
            level=1,
            text=description,
            title=title,
            page_number=1,
        )

        return ParsedContent(
            text=description,
            metadata=metadata,
            sections=[section],
            source_type="image",
            has_diagrams=True,
            image_data=image_bytes,
            image_path=str(source_path),
        )

    def _describe_image(self, image_bytes: bytes, mime_type: str) -> str:
        from google.genai import types

        image_part = types.Part.from_bytes(data=image_bytes, mime_type=mime_type)

        response = self.client.models.generate_content(
            model=self.vision_model,
            contents=[image_part, IMAGE_DESCRIPTION_PROMPT],
            config=types.GenerateContentConfig(
                max_output_tokens=Config.llm.GEMINI_MAX_TOKENS,
                temperature=0.1,
            ),
        )
        return response.text.strip()
