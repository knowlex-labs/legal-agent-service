"""Examples loader for legal document templates and few-shot examples."""

import json
import logging
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def load_examples() -> dict:
    """
    Load examples from JSON file. Cached for performance.

    Returns:
        Dictionary containing all document type examples and structures
    """
    examples_path = Path(__file__).parent / "examples.json"

    try:
        with open(examples_path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        logger.warning(f"Examples file not found at {examples_path}")
        return {}
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse examples.json: {e}")
        return {}


def get_examples_for_document_type(
    document_type: str,
    subtype: str | None = None,
) -> dict:
    """
    Get examples and structure template for a specific document type.

    Args:
        document_type: Type of document (legal_notice, affidavit, petition, etc.)
        subtype: Optional subtype (plaint, interim_application, possession_suit, etc.)

    Returns:
        Dictionary with structure template and examples for the document type
    """
    examples = load_examples()

    # Map document types to example keys
    type_mapping = {
        "legal_notice": "legal_notice",
        "demand_notice": "legal_notice",
        "affidavit": "affidavit",
        "petition": "petition",
        "application": "application",
        "contract": "contract",
        "agreement": "contract",
    }

    example_key = type_mapping.get(document_type, document_type)

    if example_key not in examples:
        logger.debug(f"No examples found for document type: {document_type}")
        return {}

    doc_examples = examples[example_key]

    # If subtype specified and exists, return subtype-specific examples
    if subtype and "subtypes" in doc_examples:
        if subtype in doc_examples["subtypes"]:
            return doc_examples["subtypes"][subtype]
        logger.debug(f"Subtype '{subtype}' not found, using base examples")

    return doc_examples


def format_as_prompt_section(examples_data: dict) -> str:
    """
    Format examples data as a prompt section for the LLM.

    Args:
        examples_data: Dictionary containing structure and examples

    Returns:
        Formatted string to include in the prompt
    """
    if not examples_data:
        return ""

    sections = []

    # Add global critical rules from the examples file
    all_examples = load_examples()
    if "_global_rules" in all_examples:
        global_rules = all_examples["_global_rules"]
        if "critical_format_rules" in global_rules:
            sections.append("## CRITICAL FORMAT RULES")
            for rule in global_rules["critical_format_rules"]:
                sections.append(f"- {rule}")
            sections.append("")

    # Add layout format if available (for precise positioning)
    if "layout_format" in examples_data:
        layout = examples_data["layout_format"]
        sections.append("## EXACT LAYOUT POSITIONING")
        if "description" in layout:
            sections.append(layout["description"])
        if "elements" in layout:
            sections.append("\n### Element-by-Element Layout:")
            for elem in layout["elements"]:
                elem_name = elem.get("element", "unknown")
                position = elem.get("position", "")
                style = elem.get("style", "")
                fmt = elem.get("format", "")
                sections.append(f"\n**{elem_name.upper()}:**")
                if position:
                    sections.append(f"  Position: {position}")
                if style:
                    sections.append(f"  Style: {style}")
                if fmt:
                    sections.append(f"  Format: {fmt}")
                # Add any additional properties
                for key in ["spacing_before", "spacing_after", "right_marker", "columns"]:
                    if key in elem:
                        sections.append(f"  {key.replace('_', ' ').title()}: {elem[key]}")
        sections.append("")

    # Add structure template if available
    if "structure" in examples_data:
        sections.append("## DOCUMENT STRUCTURE TEMPLATE")
        sections.append("Follow this exact structure for the document:\n")

        structure = examples_data["structure"]
        for key, value in structure.items():
            # Format key nicely
            formatted_key = key.replace("_", " ").title()
            sections.append(f"**{formatted_key}:** {value}")

        sections.append("")

    # Add format guidelines if available
    if "format_guidelines" in examples_data:
        sections.append("## FORMAT GUIDELINES")
        for guideline in examples_data["format_guidelines"]:
            sections.append(f"- {guideline}")
        sections.append("")

    # Add content requirements if available
    if "content_requirements" in examples_data:
        sections.append("## CONTENT REQUIREMENTS")
        for req in examples_data["content_requirements"]:
            sections.append(f"- {req}")
        sections.append("")

    # Add full example document if available (preferred over snippet)
    if "example_full_document" in examples_data:
        sections.append("## COMPLETE EXAMPLE DOCUMENT")
        sections.append("Use this as your reference for EXACT formatting and layout:")
        sections.append("```")
        sections.append(examples_data["example_full_document"])
        sections.append("```")
        sections.append("")
    # Fall back to example snippet if no full document
    elif "example_snippet" in examples_data:
        sections.append("## EXAMPLE FORMAT")
        sections.append("```")
        sections.append(examples_data["example_snippet"])
        sections.append("```")
        sections.append("")

    return "\n".join(sections)
