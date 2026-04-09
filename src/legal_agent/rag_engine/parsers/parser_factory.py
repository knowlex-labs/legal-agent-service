
import logging
from typing import Union
from pathlib import Path
from urllib.parse import urlparse

from .base_parser import BaseParser
from .pdf_parser import PDFParser
from .image_parser import ImageParser

logger = logging.getLogger(__name__)


class ParserFactory:
    @staticmethod
    def get_parser(source_type: str, **kwargs) -> BaseParser:
        logger.info(f"ParserFactory.get_parser: source_type={source_type}")
        source_type = source_type.lower()

        if source_type == "pdf":
            logger.info("ParserFactory: using PDFParser")
            return PDFParser()

        elif source_type == "file":
            logger.info("ParserFactory: using PDFParser (default for file)")
            return PDFParser()

        elif source_type == "image":
            logger.info("ParserFactory: using ImageParser")
            return ImageParser()

        else:
            logger.error(f"ParserFactory: unsupported source_type={source_type}")
            raise ValueError(f"Unsupported source type: {source_type}")

    @staticmethod
    def create_parser_for_source(source: Union[str, Path], **kwargs) -> BaseParser:
        source_type = ParserFactory.detect_source_type(source)
        logger.info(f"Detected source type: {source_type} for source: {source}")
        return ParserFactory.get_parser(source_type, **kwargs)

    @staticmethod
    def detect_source_type(source: Union[str, Path]) -> str:
        if isinstance(source, Path):
            ext = source.suffix.lower()
            if ext == ".pdf":
                return "pdf"
            elif ext in {".png", ".jpg", ".jpeg", ".webp"}:
                return "image"
            else:
                raise ValueError(f"Unsupported file type: {source.suffix}")

        source_str = str(source)

        try:
            parsed = urlparse(source_str)

            if not parsed.scheme:
                path = Path(source_str)
                ext = path.suffix.lower()
                if ext == ".pdf":
                    return "pdf"
                elif ext in {".png", ".jpg", ".jpeg", ".webp"}:
                    return "image"
                else:
                    raise ValueError(f"Unsupported file type: {path.suffix}")

            if parsed.scheme in ("http", "https"):
                raise ValueError(
                    "URL sources (web and YouTube) are not supported; use file upload with storage_url."
                )

            raise ValueError(f"Unsupported URL scheme: {parsed.scheme}")

        except ValueError:
            raise
        except Exception as e:
            raise ValueError(f"Cannot determine source type for: {source}. Error: {e}") from e

    @staticmethod
    def get_available_parsers() -> dict:
        return {
            "pdf": {
                "class": "PDFParser",
                "description": "Parses PDF documents",
                "supports": [".pdf"],
                "required_params": [],
            },
            "image": {
                "class": "ImageParser",
                "description": "Parses images using Gemini Vision and multimodal embeddings",
                "supports": [".png", ".jpg", ".jpeg", ".webp"],
                "required_params": [],
            },
        }
