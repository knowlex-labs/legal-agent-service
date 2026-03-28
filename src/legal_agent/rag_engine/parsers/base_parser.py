"""
Abstract base parser interface.
"""

from abc import ABC, abstractmethod
from typing import Union
from pathlib import Path
from .models import ParsedContent


class BaseParser(ABC):
    """
    Abstract base class for all content parsers.

    All parser implementations (PDF, YouTube, Web) must inherit from this class
    and implement the required methods.
    """

    @abstractmethod
    def can_handle(self, source: Union[str, Path]) -> bool:
        """
        Check if this parser can handle the given source.

        Args:
            source: File path, URL, or other source identifier

        Returns:
            True if parser can handle this source, False otherwise
        """
        pass

    @abstractmethod
    def parse(self, source: Union[str, Path]) -> ParsedContent:
        """
        Parse the source and return structured content.

        Args:
            source: File path, URL, or other source to parse

        Returns:
            ParsedContent object with standardized structure

        Raises:
            ValueError: If source cannot be parsed
            FileNotFoundError: If source file doesn't exist
            Exception: For other parsing errors
        """
        pass

    def validate_source(self, source: Union[str, Path]) -> None:
        """
        Validate that the source is accessible and can be parsed.

        Args:
            source: Source to validate

        Raises:
            ValueError: If source is invalid
            FileNotFoundError: If source file doesn't exist
        """
        if not source:
            raise ValueError("Source cannot be empty")

    def __repr__(self) -> str:
        """String representation of the parser."""
        return f"{self.__class__.__name__}()"
