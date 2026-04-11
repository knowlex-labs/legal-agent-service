"""Models for case synopsis generation."""

from pydantic import BaseModel


class DraftContext(BaseModel):
    """Minimal draft context for synopsis generation."""

    title: str
    document_type: str
    content: str | None = None