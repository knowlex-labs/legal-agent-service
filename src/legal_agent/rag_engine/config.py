import os
from dotenv import load_dotenv

load_dotenv()

class ParserConfig:
    WEB_SCRAPER_USER_AGENT: str = os.getenv("WEB_SCRAPER_USER_AGENT", "RAG-Engine/1.0")
    WEB_SCRAPER_TIMEOUT: int = int(os.getenv("WEB_SCRAPER_TIMEOUT", "30"))
    YOUTUBE_TRANSCRIPT_FALLBACK: str = os.getenv("YOUTUBE_TRANSCRIPT_FALLBACK", "gemini")

class EmbeddingConfig:
    MODEL_NAME: str = os.getenv("EMBEDDING_MODEL", "gemini-embedding-2-preview")
    PROVIDER: str = os.getenv("EMBEDDING_PROVIDER", "gemini")
    VECTOR_SIZE: int = int(os.getenv("VECTOR_SIZE", "1536"))
    DISTANCE_METRIC: str = os.getenv("DISTANCE_METRIC", "COSINE")
    CHUNK_SIZE: int = int(os.getenv("CHUNK_SIZE", "800"))
    CHUNK_OVERLAP: int = int(os.getenv("CHUNK_OVERLAP", "100"))
    MAX_CHUNK_SIZE: int = int(os.getenv("MAX_CHUNK_SIZE", "1200"))

class LlmConfig:
    PROVIDER: str = os.getenv("LLM_PROVIDER", "openai")
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    OPENAI_MODEL: str = os.getenv("OPENAI_MODEL", "gpt-4o")
    OPENAI_MAX_TOKENS: int = int(os.getenv("OPENAI_MAX_TOKENS", "1000"))
    OPENAI_TEMPERATURE: float = float(os.getenv("OPENAI_TEMPERATURE", "0.1"))
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
    GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
    GEMINI_MAX_TOKENS: int = int(os.getenv("GEMINI_MAX_TOKENS", "4000"))
    GEMINI_TEMPERATURE: float = float(os.getenv("GEMINI_TEMPERATURE", "0.1"))
    ENABLE_JSON_RESPONSE: bool = os.getenv("ENABLE_JSON_RESPONSE", "false").lower() == "true"

class AppConfig:
    DEBUG: bool = os.getenv("DEBUG", "false").lower() == "true"
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

    API_HOST: str = os.getenv("API_HOST", "0.0.0.0")
    API_PORT: int = int(os.getenv("API_PORT", "8000"))
    UPLOADS_DIR: str = os.getenv("UPLOADS_DIR", "uploads")
    MAX_FILE_SIZE_MB: int = int(os.getenv("MAX_FILE_SIZE_MB", "100"))
    
    # CORS Configuration
    CORS_ALLOWED_ORIGINS: list = os.getenv(
        "CORS_ALLOWED_ORIGINS",
        "http://localhost:3000,http://localhost:7860,http://localhost:5173,https://nyayamind-frontend-722723826302.asia-south1.run.app,https://nyayamind-ai-content-tutor-722723826302.asia-south1.run.app,http://13.236.51.35:3000,http://13.236.51.35:8080"
    ).split(",")

class RerankingConfig:
    RERANKER_ENABLED: bool = os.getenv("RERANKER_ENABLED", "true").lower() == "true"
    RERANKER_MODEL: str = os.getenv("RERANKER_MODEL", "cross-encoder/ms-marco-MiniLM-L-6-v2")
    RERANKER_TOP_K: int = int(os.getenv("RERANKER_TOP_K", "5"))

class CriticConfig:
    CRITIC_ENABLED: bool = os.getenv("CRITIC_ENABLED", "false").lower() == "true"
    CRITIC_MODEL_NAME: str = os.getenv("CRITIC_MODEL_NAME", "models/gemini-2.5-flash")
    CRITIC_MODEL_API_KEY: str = os.getenv("CRITIC_MODEL_API_KEY", "")
    CRITIC_MODEL_TEMPERATURE: float = float(os.getenv("CRITIC_MODEL_TEMPERATURE", "0.1"))

class FeedbackConfig:
    FEEDBACK_ENABLED: bool = os.getenv("FEEDBACK_ENABLED", "true").lower() == "true"
    FEEDBACK_SIMILARITY_THRESHOLD: float = float(os.getenv("FEEDBACK_SIMILARITY_THRESHOLD", "0.8"))

class QueryConfig:
    RELEVANCE_THRESHOLD: float = float(os.getenv("RELEVANCE_THRESHOLD", "0.4"))

class StorageConfig:
    STORAGE_TYPE: str = os.getenv("STORAGE_TYPE", "local").lower()

class S3Config:
    ACCESS_KEY: str = os.getenv("S3_ACCESS_KEY", "")
    SECRET_KEY: str = os.getenv("S3_SECRET_KEY", "")
    REGION_NAME: str = os.getenv("S3_REGION_NAME", "us-east-1")
    BUCKET_NAME: str = os.getenv("S3_BUCKET_NAME", "rag-engine")

class Neo4jConfig:
    URI: str = os.getenv("NEO4J_URI", "bolt://localhost:7687")
    USER: str = os.getenv("NEO4J_USER", "neo4j")
    PASSWORD: str = os.getenv("NEO4J_PASSWORD")
    DATABASE: str = os.getenv("NEO4J_DATABASE", "neo4j")
    VECTOR_INDEX_NAME: str = os.getenv("NEO4J_VECTOR_INDEX_NAME", "legal_chunks_index")

class LlamaCloudConfig:
    API_KEY: str = os.getenv("LLAMA_CLOUD_API_KEY", "")

class QdrantConfig:
    HOST: str = os.getenv("QDRANT_HOST", "localhost")
    PORT: int = int(os.getenv("QDRANT_PORT", "6333"))
    TIMEOUT: int = int(os.getenv("QDRANT_TIMEOUT", "30"))
    API_KEY: str = os.getenv("QDRANT_API_KEY", "")

class Config:
    parser = ParserConfig()
    embedding = EmbeddingConfig()
    llm = LlmConfig()
    app = AppConfig()
    reranking = RerankingConfig()
    critic = CriticConfig()
    feedback = FeedbackConfig()
    query = QueryConfig()
    storage = StorageConfig()

    s3 = S3Config()
    qdrant = QdrantConfig()
    neo4j = Neo4jConfig()
    llama_cloud = LlamaCloudConfig()