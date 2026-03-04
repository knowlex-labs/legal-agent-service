"""Internal models for case summary."""

from pydantic import BaseModel


class DraftContext(BaseModel):
    title: str
    document_type: str
    content: str | None = None
