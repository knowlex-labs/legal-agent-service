"""Pydantic models for RAG Engine API."""

from pydantic import BaseModel


class RetrieveFilters(BaseModel):
    collection_ids: list[str] | None = None
    file_ids: list[str] | None = None
    content_type: str | None = "legal"


class RetrieveRequest(BaseModel):
    query: str
    filters: RetrieveFilters | None = None
    top_k: int = 5


class EnrichedChunk(BaseModel):
    chunk_id: str
    chunk_text: str
    relevance_score: float
    file_id: str
    page_number: int | None = None
    timestamp: str | None = None
    concepts: list[str] = []


class RetrieveResponse(BaseModel):
    success: bool
    results: list[EnrichedChunk]
