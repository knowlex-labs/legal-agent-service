from pydantic import BaseModel, model_validator
from typing import List, Optional
from enum import Enum

# Enums
class ChunkType(str, Enum):
    CONCEPT = "concept"
    EXAMPLE = "example"
    QUESTION = "question"
    OTHER = "other"
    SECTION = "section"
    PROVISION = "provision"
    DEFINITION = "definition"
    SCHEDULE = "schedule"
    PREAMBLE = "preamble"
    CHAPTER = "chapter"
    IMAGE = "image"

class ContentType(str, Enum):
    """Type of content being indexed - determines chunking strategy"""
    BOOK = "book"
    CHAPTER = "chapter"
    DOCUMENT = "document"
    AUTO = "auto"

class DataContentType(str, Enum):
    """Type of data content for filtering and separation"""
    LEGAL = "legal"
    NEWS = "news"

class BookMetadata(BaseModel):
    """Book-level metadata for full textbook indexing"""
    book_id: Optional[str] = None
    book_title: Optional[str] = None
    book_authors: List[str] = []
    book_edition: Optional[str] = None
    book_subject: Optional[str] = None
    total_chapters: Optional[int] = None
    total_pages: Optional[int] = None

class ChunkingStrategy(BaseModel):
    """Dynamic chunking configuration based on content type"""
    chunk_size: int
    chunk_overlap: int
    content_type: ContentType
    description: str

class IndexingStatus(str, Enum):
    PENDING = "INDEXING_PENDING"
    STARTED = "INDEXING_STARTED"
    SUCCESS = "INDEXING_SUCCESS"
    FAILED = "INDEXING_FAILED"
    CANCELLED = "INDEXING_CANCELLED"

class LinkItem(BaseModel):
    type: str # 'file', 'youtube', 'web', 'image'
    file_id: str
    collection_id: Optional[str] = None
    url: Optional[str] = None
    storage_url: Optional[str] = None
    content_type: Optional[DataContentType] = DataContentType.LEGAL

    @model_validator(mode='before')
    @classmethod
    def check_source(cls, values):
        if isinstance(values, dict):
            type_val = values.get('type')
            url = values.get('url')
            storage_url = values.get('storage_url')

            if type_val in ['youtube', 'web'] and not url:
                raise ValueError(f"{type_val} requires 'url'")
            if type_val in ['file', 'image'] and not storage_url:
                raise ValueError(f"{type_val} requires 'storage_url'")
        return values

class BatchLinkRequest(BaseModel):
    items: List[LinkItem]
    use_neo4j: bool = False

class BatchItemResponse(BaseModel):
    file_id: str
    status: str
    error: Optional[str] = None

class IngestionResponse(BaseModel):
    message: str
    batch_id: str
    results: List[BatchItemResponse]

class RetrieveFilters(BaseModel):
    collection_ids: Optional[List[str]] = None
    file_ids: Optional[List[str]] = None
    content_type: Optional[DataContentType] = None
    news_subcategory: Optional[str] = None

class RetrieveRequest(BaseModel):
    query: str
    filters: Optional[RetrieveFilters] = None
    top_k: int = 5
    include_graph_context: bool = True
    use_neo4j: bool = False

class EnrichedChunk(BaseModel):
    chunk_id: str
    chunk_text: str
    relevance_score: float
    file_id: str
    page_number: Optional[int] = None
    timestamp: Optional[str] = None
    concepts: List[str] = []

class RetrieveResponse(BaseModel):
    success: bool
    results: List[EnrichedChunk]

class QueryAnswerRequest(BaseModel):
    query: str
    filters: Optional[RetrieveFilters] = None
    top_k: int = 5
    include_sources: bool = False
    answer_style: Optional[str] = "detailed"
    use_neo4j: bool = False

class DeleteFileRequest(BaseModel):
    file_ids: List[str]

class QueryResponse(BaseModel):
    answer: str
    confidence: float
    is_relevant: bool
    chunks: List['ChunkConfig']
    critic: Optional['CriticEvaluation'] = None

# Internal models for query service

class ChunkConfig(BaseModel):
    source: str
    text: str
    chunk_id: Optional[str] = None
    relevance_score: Optional[float] = None
    file_id: Optional[str] = None
    page_number: Optional[int] = None
    timestamp: Optional[str] = None
    concepts: List[str] = []

class TopicMetadata(BaseModel):
    chapter_num: Optional[int] = None
    chapter_title: Optional[str] = None
    section_num: Optional[str] = None
    section_title: Optional[str] = None
    page_start: Optional[int] = None
    page_end: Optional[int] = None

class ChunkMetadata(BaseModel):
    chunk_type: ChunkType
    topic_id: str
    key_terms: List[str] = []
    equations: List[str] = []
    has_equations: bool = False
    has_diagrams: bool = False
    difficulty_level: Optional[str] = None

class HierarchicalChunk(BaseModel):
    chunk_id: str
    document_id: str
    topic_metadata: TopicMetadata
    chunk_metadata: ChunkMetadata
    text: str
    embedding_vector: Optional[List[float]] = None

class CriticEvaluation(BaseModel):
    confidence: float
    missing_info: str
    enrichment_suggestions: List[str]

# Models moved from collections.py

class CreateCollectionRequest(BaseModel):
    collection_name: str
    use_new_schema: bool = True

class GetChunksRequest(BaseModel):
    file_id: str
    limit: int = 100

class FileStatusRequest(BaseModel):
    file_ids: List[str]
    collection_id: Optional[str] = None

class FileStatusItem(BaseModel):
    file_id: str
    status: str
    chunk_count: int
    indexed_at: Optional[str] = None
