"""Application configuration using Pydantic Settings."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).parent.parent.parent
ENV_FILE = PROJECT_ROOT / ".env"

_LANGCHAIN_PROVIDERS = {"openai": "openai", "anthropic": "anthropic", "gemini": "google-genai"}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ENV_FILE, env_file_encoding="utf-8", extra="ignore")

    @field_validator("translation_debug_dir", mode="before")
    @classmethod
    def _empty_translation_debug_dir(cls, v: object) -> str | None:
        if v is None:
            return None
        if isinstance(v, str) and not v.strip():
            return None
        return str(v).strip() if isinstance(v, str) else v

    draft_llm_provider: Literal["openai", "anthropic", "gemini"] = "openai"
    draft_llm_model: str = "gpt-5.4"
    openai_api_key: str | None = None
    gemini_api_key: str | None = None
    anthropic_api_key: str | None = None
    sarvam_api_key: str | None = None
    mistral_api_key: str | None = None

    # Model used for the structured-metadata extraction call inside output_node.
    # Defaulted to Haiku so the small, fixed-shape extraction does not consume
    # the main draft model's TPM budget (gpt-5.4 has hit OpenAI's 10k-TPM cap on
    # interim-application drafts where the message history alone is ~10k tokens).
    metadata_extraction_model: str = "claude-haiku-4-5-20251001"
    metadata_extraction_provider: str = "anthropic"

    # OCR backend selection. "gemini" = Gemini Vision (default, primary path).
    # "mistral" / "mistral_ocr" = Mistral's dedicated document OCR endpoint for PDFs.
    # "sarvam" = Sarvam Document Intelligence (markdown only; <=10 pages per job).
    ocr_provider: Literal["gemini", "mistral", "mistral_ocr", "sarvam"] = "gemini"
    mistral_ocr_model: str = "mistral-ocr-latest"
    # Concurrent Sarvam jobs when chunking long PDFs. Each chunk is ≤10 pages.
    sarvam_ocr_concurrency: int = 4
    # Concurrent Gemini Vision calls per PDF (one call per page). Gemini API
    # rate-limits: free tier ~15 rpm, paid tier much higher. Lower if rate-limited.
    gemini_ocr_concurrency: int = 4
    # Concurrent Mistral Pixtral calls per PDF in the legacy image OCR path.
    mistral_ocr_concurrency: int = 4
    # Mistral vision-capable model. Pixtral 12B is general-purpose; Pixtral Large
    # is higher accuracy. Both work as drop-in.
    mistral_vision_model: str = "pixtral-12b-2409"
    # Scanned-PDF translation goes through a single vision LLM call per page that
    # emits target-language semantic HTML preserving 2D layout. Mistral OCR is
    # still used to extract image bytes that get spliced into placeholders.
    vision_translation_model: str = "claude-sonnet-4-5-20250929"
    vision_translation_concurrency: int = 4
    # When True (default), scanned-PDF vision translation requests structured JSON
    # blocks with typography hints → higher fidelity spacing/fonts vs legacy HTML-only.
    # Set VISION_TRANSLATION_STRUCTURED_LAYOUT=false to revert to legacy behaviour.
    vision_translation_structured_layout: bool = True
    # When True with TRANSLATION_DEBUG_DIR set, writes per-page vision_fidelity *.json under that dir.
    # Default False — fidelity metrics still appear on the completed job metadata only.
    vision_translation_debug_fidelity_files: bool = False
    # Language hint passed to Sarvam (BCP-47, must match Sarvam's accepted list).
    # Sarvam's API rejects "unknown" — pick the document's primary script.
    # Common: en-IN, hi-IN, mr-IN, ta-IN, te-IN, bn-IN, gu-IN.
    sarvam_ocr_language: str = "en-IN"
    # Sarvam chat/translation model. Options: sarvam-m (24B, default), sarvam-30b, sarvam-105b.
    # Used when a request selects provider "sarvam" for translation or draft chat.
    sarvam_chat_model: str = "sarvam-m"
    # Sarvam REST translate model. "sarvam-translate:v1" is the formal/legal
    # translation model with 2000-char input limit. We use formal mode + {{n}}
    # placeholder masking to preserve English tech/brand terms.
    sarvam_translate_model: str = "sarvam-translate:v1"
    # Concurrent POSTs to Sarvam REST /translate. Non-translatable blocks (numbers,
    # emails, punctuation) are skipped, so effective call count is much lower than
    # total <p> count. 4 is safe with the retry/backoff logic.
    sarvam_translate_max_concurrency: int = 4
    # Retries after the first attempt on HTTP 429 / 502 / 503 (Retry-After or exponential backoff).
    sarvam_translate_max_retries: int = 6
    # Backoff base when Retry-After is absent: delay = min(60, base * mult^attempt).
    sarvam_translate_retry_base_seconds: float = 1.0
    # Translation backend. "sarvam" hits Sarvam REST formal translate (cheapest,
    # Indic-specialist); any other value is treated as an LLM model name dispatched
    # via langchain init_chat_model — e.g. "gemini-2.5-flash", "claude-haiku-4-5-20251001",
    # "gpt-5-mini". On LLM models targeting Hindi, the CBIC/Rajbhasha legal prompt
    # is applied automatically; Sarvam ignores it (no prompt input).
    translation_llm_model: str = "sarvam"
    # Char budget per batched translate call. Sarvam REST caps input at ~2000;
    # leave headroom for the sentinel + glossary sentinel expansion.
    translation_batch_max_chars: int = 1800

    # ── Three-stage agent translation pipeline ───────────────────────────
    # Stage A (glossary extractor) → Stage B (translator with doc context) →
    # Stage C (source-grounded reviewer). Activated for legal/administrative
    # documents to eliminate mid-document drift / topic substitution.
    # Stage A (glossary) + Stage C (reviewer) now run for both Sarvam and LLM
    # backends — only Stage B (translation) is dispatched by translation_llm_model.
    translation_reviewer_enabled: bool = True
    translation_reviewer_model: str = "claude-haiku-4-5-20251001"
    translation_reviewer_max_concurrency: int = 4
    translation_glossary_extractor_model: str = "claude-haiku-4-5-20251001"
    # Per-LLM-call char budget when running the three-stage pipeline. Smaller
    # than translation_batch_max_chars (1800) so any drift is bounded to a
    # short window and the reviewer has tight scope.
    translation_chunk_max_chars: int = 800
    # Preceding-output regions fed back as CONTEXT_PREVIOUS_OUTPUT for the
    # translator. Higher = more continuity but more tokens.
    translation_context_window_regions: int = 3

    # ── Pipeline quality layer ──────────────────────────────────────────
    # Style smoother runs in parallel with the fidelity reviewer (Stage C).
    # Emits diff-style edits so its output can be merged into reviewer fixes
    # without overwriting fidelity corrections. Catches over-Sanskritization,
    # awkward literal renderings, and fused content-words (हैंकेवल → हैं केवल)
    # that rule-based passes can't solve without a Hindi morphological splitter.
    translation_smoother_enabled: bool = True
    translation_smoother_model: str = "claude-haiku-4-5-20251001"
    translation_smoother_max_concurrency: int = 4
    # Paragraph-/role-aware chunker. False keeps the legacy char-budget _pack.
    translation_semantic_chunking: bool = True
    # Cross-page paragraph stitching is OPT-IN — gated by strict geometry +
    # role + heading checks, but the failure mode (merging unrelated paragraphs
    # across section breaks) is severe enough to keep this off by default.
    translation_cross_page_stitch: bool = False
    # Hindi numeral policy after post-processing. "western" (default) keeps
    # 0-9 — matches legal/contract conventions. "devanagari" rewrites to ०-९.
    translation_hindi_numerals: Literal["western", "devanagari"] = "western"
    # PDF page margins. Legacy 0/0/0/0 gave an edge-to-edge "machine print"
    # look; current defaults read like a proper legal document.
    translation_pdf_margin_top: str = "2.2cm"
    translation_pdf_margin_bottom: str = "2.2cm"
    translation_pdf_margin_left: str = "2.5cm"
    translation_pdf_margin_right: str = "2cm"
    # Running header (document subject) + page number rendered via Playwright
    # headerTemplate / footerTemplate. Off keeps the bare-page look.
    translation_pdf_running_header: bool = True
    # Sarvam's OpenAI-compatible base URL — override only if Sarvam changes hosts.
    sarvam_api_base_url: str = "https://api.sarvam.ai/v1"
    # Content-addressed OCR + vision-translation cache in S3 (shared client/path prefix).
    # When False: no cache reads/writes — vision translation always calls the LLM per page.
    ocr_cache_enabled: bool = True
    ocr_cache_prefix: str = "ocr-cache"

    # Service
    service_host: str = "0.0.0.0"
    service_port: int = 8001
    debug: bool = False

    # PostgreSQL
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "knowlex"
    postgres_username: str = "postgres"
    postgres_password: str = ""
    legal_db_url: str | None = None

    # Chat LLM default (frontend sends model ID directly; env var: CHAT_LLM_MODEL)
    chat_llm_model: str = "gemini-2.5-flash"

    # Directory for debug dumps of intermediate translation files (HTML, PDF).
    # Leave empty in production. Set e.g. /tmp/translation_debug to inspect:
    #   1_pymupdf_page_N.html, 2_before_translate.html, 4_final.html, 5_rendered.pdf
    translation_debug_dir: str | None = None

    # Web search — Firecrawl is primary (scrapes full article text), Serper is fallback.
    # Both restricted to the 3 trusted Indian legal sources below (no Indian Kanoon).
    # Scrape count is chosen per call by the LLM via the `num_sources` tool arg
    # (clamped 1-5 in legal_web_search_firecrawl.py), so it is not set here.
    firecrawl_api_key: str = ""
    firecrawl_search_domains: list[str] = Field(
        default=["livelaw.in", "scconline.com", "barandbench.com"]
    )
    serper_api_key: str = ""

    # Draft+verify pipeline for workspace chat with web_search=True.
    # Each claim verified = 1 Firecrawl search credit (no scrape).
    # Cap extraction to keep cost bounded; lower if credits spike.
    firecrawl_verify_max_claims: int = 7
    firecrawl_verify_concurrency: int = 5

    # Jobs
    job_timeout_seconds: int = 1800
    job_max_retries: int = 3

    # CORS
    cors_allowed_origins: list[str] = Field(default=["https://app.knowlex.ai"])
    trust_forwarded_headers: bool = False

    # S3
    s3_access_key: str = ""
    s3_secret_key: str = ""
    s3_region_name: str = "ap-south-1"
    s3_bucket_name: str = "knowlex-user-documents"
    s3_signed_url_expiry: int = 3600

    # Document encryption (AES-256-GCM envelope encryption, matches platform API)
    document_encryption_master_key: str = ""

    # ── Embeddings ────────────────────────────────────────────────────────
    # Each RAG system has its own embedding config so we can mix providers
    # without cross-contamination. `embedding_*` (legacy, below) is the
    # fallback for anything that hasn't been migrated yet.

    # Workspace RAG (rag_engine/) — user documents in Qdrant.
    # Default mirrors the legacy single config until we re-index to BGE.
    workspace_embedding_provider: str | None = None  # falls back to embedding_provider
    workspace_embedding_model: str | None = None     # falls back to embedding_model
    workspace_vector_size: int | None = None         # falls back to vector_size

    # Legal retrieval (legal_retrieval/) — Supreme Court judgments in
    # PostgreSQL + pgvector. DB column is halfvec(3072) — must use Gemini.
    legal_embedding_provider: str = "gemini"
    legal_embedding_model: str = "gemini-embedding-2-preview"
    legal_vector_size: int = 3072

    # Legacy single config (kept for backward compat + workspace fallback).
    embedding_model: str = "BAAI/bge-m3"
    embedding_provider: str = "huggingface"
    vector_size: int = 1024
    distance_metric: str = "COSINE"
    chunk_size: int = 800
    chunk_overlap: int = 100
    max_chunk_size: int = 1200

    def get_workspace_embedding_provider(self) -> str:
        return self.workspace_embedding_provider or self.embedding_provider

    def get_workspace_embedding_model(self) -> str:
        return self.workspace_embedding_model or self.embedding_model

    def get_workspace_vector_size(self) -> int:
        return self.workspace_vector_size or self.vector_size

    # LLM extras (RAG engine)
    openai_model: str = "gpt-4o"
    openai_max_tokens: int = 1000
    openai_temperature: float = 0.1
    gemini_model: str = "gemini-3.1-flash"
    gemini_max_tokens: int = 4000
    gemini_temperature: float = 0.1
    enable_json_response: bool = False

    # Qdrant
    qdrant_host: str = "localhost"
    qdrant_port: int = 6333
    qdrant_timeout: int = 30
    qdrant_api_key: str = ""

    # Reranking
    reranker_enabled: bool = True
    reranker_model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"
    reranker_top_k: int = 5

    # Feedback
    feedback_enabled: bool = True
    feedback_similarity_threshold: float = 0.8

    # Query
    relevance_threshold: float = 0.4

    # Semantic chunking
    semantic_similarity_threshold: float = 0.8
    semantic_min_chunk_size: int = 100
    semantic_model: str = "sentence-transformers/all-MiniLM-L6-v2"

    def get_langchain_provider(self) -> str:
        return _LANGCHAIN_PROVIDERS.get(self.draft_llm_provider, self.draft_llm_provider)

    @staticmethod
    def get_langchain_provider_for_model(model_id: str) -> str:
        """Infer LangChain provider from model ID string."""
        if model_id.startswith("gemini"):
            return "google-genai"
        return "openai"

    def get_llm_api_key(self) -> str | None:
        keys = {"openai": self.openai_api_key, "anthropic": self.anthropic_api_key, "gemini": self.gemini_api_key}
        return keys.get(self.draft_llm_provider)



@lru_cache
def get_settings() -> Settings:
    return Settings()
