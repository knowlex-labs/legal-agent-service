"""Data module for legal document examples and templates."""

from legal_agent.data.examples_loader import (
    get_examples_for_document_type,
    format_as_prompt_section,
    load_examples,
)

__all__ = [
    "get_examples_for_document_type",
    "format_as_prompt_section",
    "load_examples",
]
