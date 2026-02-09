"""Configuration constants for the legal retrieval module."""

from legal_agent.config import get_settings

EMBEDDING_MODEL = "BAAI/bge-m3"
EMBEDDING_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "
RERANKER_MODEL = "BAAI/bge-reranker-v2-m3"
MODEL_DEVICE = "cpu"

# RRF weights
SEMANTIC_WEIGHT = 0.7
FTS_WEIGHT = 0.3
K = 60

# Pipeline
HYBRID_LIMIT = 50
RERANK_CANDIDATES = 20
DEFAULT_TOP_K = 5
CITATION_BOOST = 0.05

# Pool
POOL_MIN_SIZE = 1
POOL_MAX_SIZE = 5


def get_legal_db_url() -> str:
    """Build PostgreSQL URL from settings, or use LEGAL_DB_URL override."""
    settings = get_settings()
    if settings.legal_db_url:
        return settings.legal_db_url
    return (
        f"postgresql://{settings.postgres_username}:{settings.postgres_password}"
        f"@{settings.postgres_host}:{settings.postgres_port}/{settings.postgres_db}"
        f"?sslmode=require&connect_timeout=10"
        f"&keepalives=1&keepalives_idle=30&keepalives_interval=10&keepalives_count=5"
    )
