import logging
import os
from pathlib import Path
from typing import Literal, Optional

from pydantic import AliasChoices, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


logger = logging.getLogger(__name__)


PROJECT_ROOT = Path(__file__).resolve().parents[3]
ROOT_ENV = PROJECT_ROOT / ".env"
ROOT_ENV_LOCAL = PROJECT_ROOT / ".env.local"
SERVICE_ENV = PROJECT_ROOT / ".env"
SERVICE_ENV_LOCAL = PROJECT_ROOT / ".env.local"


class Settings(BaseSettings):
    """
    CIRE-RAG - Global Configuration Registry
    Centralizes all environment variables using Pydantic Settings.
    """

    model_config = SettingsConfigDict(
        env_file=(
            str(ROOT_ENV),
            str(ROOT_ENV_LOCAL),
            str(SERVICE_ENV),
            str(SERVICE_ENV_LOCAL),
        ),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Infrastructure
    DATABASE_URL: Optional[str] = None
    SUPABASE_DB_URL: Optional[str] = None
    SUPABASE_URL: Optional[str] = Field(
        None, validation_alias=AliasChoices("SUPABASE_URL", "NEXT_PUBLIC_SUPABASE_URL")
    )
    SUPABASE_SERVICE_KEY: Optional[str] = Field(None, alias="SUPABASE_SERVICE_ROLE_KEY")
    NEXT_PUBLIC_SUPABASE_URL: Optional[str] = None
    REDIS_URL: str = "redis://localhost:6379/0"
    RAG_SERVICE_URL: str = "http://localhost:8000"
    RAG_ENGINE_URL: Optional[str] = None

    # Security
    RAG_SERVICE_SECRET: str = "development-secret"
    SYSTEM_TENANT_ID: str = "00000000-0000-0000-0000-000000000000"

    # AI Models & Services
    OPENAI_API_KEY: Optional[str] = None
    GROQ_API_KEY: Optional[str] = None
    GEMINI_API_KEY: Optional[str] = None
    ANTHROPIC_API_KEY: Optional[str] = None
    JINA_API_KEY: Optional[str] = None
    JINA_MODE: Literal["LOCAL", "CLOUD"] = "CLOUD"
    EMBEDDING_PROVIDER_DEFAULT: str = "jina"
    INGEST_EMBED_PROVIDER_DEFAULT: Optional[str] = None
    INGEST_EMBED_FALLBACK_PROVIDER: str = "jina"
    INGEST_EMBED_FALLBACK_ON_TECHNICAL_ERROR: bool = True
    EMBEDDING_PROVIDER_ALLOWLIST: str = "jina,cohere"
    OPENAI_FALLBACK_MODEL: str = "gpt-4o-mini"
    STRICT_ENGINE_MAX_TOKENS: Optional[int] = 8192
    JINA_BASE_URL: str = "https://api.jina.ai/v1/embeddings"
    JINA_MODEL_NAME: str = "jinaai/jina-embeddings-v3"
    JINA_EMBEDDING_DIMENSIONS: int = 1024
    JINA_RERANK_URL: str = "https://api.jina.ai/v1/rerank"
    JINA_RERANK_MODEL: str = "jina-reranker-v2-base-multilingual"
    COHERE_API_KEY: Optional[str] = None
    COHERE_EMBED_URL: str = "https://api.cohere.com/v2/embed"
    COHERE_EMBED_MODEL: str = "embed-multilingual-v3.0"
    COHERE_EMBEDDING_DIMENSIONS: int = 1024
    COHERE_REQUEST_MAX_PARALLEL: int = 2
    COHERE_MAX_TEXTS_PER_REQUEST: int = 96
    COHERE_RERANK_URL: str = "https://api.cohere.com/v2/rerank"
    COHERE_RERANK_MODEL: str = "rerank-v3.5"

    # API Config
    API_PORT: int = 8000
    LOG_LEVEL: str = "INFO"
    ENVIRONMENT: str = "development"
    APP_ENV: str = "local"
    RUNNING_IN_DOCKER: bool = False
    FORENSIC_LOGGING_LEVEL: str = "METADATA_ONLY"

    # Worker / Throughput controls
    WORKER_CONCURRENCY: int = 4
    WORKER_PER_TENANT_CONCURRENCY: int = 6
    WORKER_POLL_INTERVAL_SECONDS: int = 2
    WORKER_SUPABASE_TRANSIENT_MAX_RETRIES: int = 3
    WORKER_SUPABASE_TRANSIENT_BASE_DELAY_SECONDS: float = 0.4
    WORKER_SUPABASE_ERROR_COOLDOWN_THRESHOLD: int = 4
    WORKER_SUPABASE_ERROR_COOLDOWN_SECONDS: float = 6.0
    WORKER_SOURCE_LOOKUP_MAX_REQUEUES: int = 3
    WORKER_JOB_HEARTBEAT_SECONDS: float = 20.0
    WORKER_REQUEUE_STALE_INTERVAL_SECONDS: int = 15
    WORKER_REQUEUE_STALE_PROCESSING_SECONDS: int = 120
    EMBEDDING_CONCURRENCY: int = 5
    EMBEDDING_CACHE_MAX_SIZE: int = 4000
    EMBEDDING_CACHE_TTL_SECONDS: int = 1800
    JINA_EMBED_RETRY_MAX_ATTEMPTS: int = 5
    JINA_EMBED_RETRY_BASE_DELAY_SECONDS: float = 0.4
    JINA_EMBED_RETRY_MAX_DELAY_SECONDS: float = 10.0
    JINA_EMBED_RETRY_429_BACKOFF_MULTIPLIER: float = 3.0
    JINA_REQUEST_MAX_PARALLEL: int = 2
    JINA_RATE_LIMIT_COOLDOWN_SECONDS: float = 0.0
    JINA_BATCH_SIZE: int = 8
    JINA_BATCH_RATE_LIMIT_DELAY_SECONDS: float = 1.0
    JINA_ADAPTIVE_BATCHING_ENABLED: bool = True
    JINA_BATCH_MIN_SIZE: int = 4
    JINA_BATCH_RECOVERY_STEP: int = 1
    WORKER_TENANT_QUEUE_SAMPLE_LIMIT: int = 1000
    WORKER_TENANT_QUEUE_DEPTH_ALERT: int = 200
    WORKER_TENANT_QUEUE_WAIT_ALERT_SECONDS: int = 300
    INGESTION_MAX_PENDING_PER_TENANT: int = 500
    INGESTION_DOCS_PER_MINUTE_PER_WORKER: int = 10
    PARSER_WINDOW_CONCURRENCY: int = 5
    PARSER_WINDOW_MAX_RETRIES: int = 2
    PARSER_WINDOW_RETRY_BASE_DELAY_SECONDS: float = 0.75
    CONTENT_CHUNKS_INSERT_BATCH_SIZE: int = 100
    CONTENT_CHUNKS_INSERT_BATCH_SLEEP_SECONDS: float = 0.0
    COMMUNITY_REBUILD_ENABLED: bool = False
    COMMUNITY_REBUILD_INTERVAL_SECONDS: int = 3600
    COMMUNITY_REBUILD_TENANTS: str = ""

    # Storage
    RAG_STORAGE_BUCKET: str = "private_assets"
    INSTITUTIONAL_STORAGE_BUCKET: str = "institutional"

    # Feature flags
    USE_TRICAMERAL: bool = False
    ENABLE_HEART_VERIFICATION: bool = False
    DAILY_VLM_LIMIT: Optional[int] = None
    RETRIEVAL_ENGINE_MODE: str = "atomic"  # unified | atomic | hybrid
    SCOPE_STRICT_FILTERING: bool = False
    RETRIEVAL_SCOPE_PENALTY_FACTOR: float = 0.25
    ATOMIC_ENABLE_FTS: bool = True
    ATOMIC_ENABLE_GRAPH_HOP: bool = True
    ATOMIC_USE_HYBRID_RPC: bool = True
    ATOMIC_MATCH_THRESHOLD: float = 0.25
    ATOMIC_HNSW_EF_SEARCH: int = 80
    ATOMIC_RRF_VECTOR_WEIGHT: float = 0.7
    ATOMIC_RRF_FTS_WEIGHT: float = 0.3
    ATOMIC_CLAUSE_QUERY_WEIGHT_BOOST_ENABLED: bool = True
    ATOMIC_CLAUSE_QUERY_RRF_VECTOR_WEIGHT: float = 0.55
    ATOMIC_CLAUSE_QUERY_RRF_FTS_WEIGHT: float = 0.75
    ATOMIC_RRF_K: int = 60
    ATOMIC_MAX_SOURCE_IDS: int = 5000
    QA_LITERAL_SEMANTIC_FALLBACK_ENABLED: bool = True
    QA_LITERAL_SEMANTIC_MIN_KEYWORD_OVERLAP: int = 2
    QA_LITERAL_SEMANTIC_MIN_SIMILARITY: float = 0.3
    INGEST_PARSER_MODE: str = "local"  # local | cloud
    JINA_READER_URL_TEMPLATE: Optional[str] = None  # e.g. https://r.jina.ai/http://host/{path}
    RERANK_MODE: str = "hybrid"  # local | jina | hybrid
    RERANK_MAX_CANDIDATES: int = 50
    RERANK_MIN_RELEVANCE_SCORE: float = 0.15  # Cross-encoder score floor
    GRAVITY_MIN_SCORE_THRESHOLD: float = 0.10  # Raw similarity floor before gravity multipliers
    RETRIEVAL_MULTI_QUERY_MAX_PARALLEL: int = 4
    RETRIEVAL_MULTI_QUERY_SUBQUERY_TIMEOUT_MS: int = 8000
    RETRIEVAL_MULTI_QUERY_SUBQUERY_RERANK_ENABLED: bool = False
    RETRIEVAL_MULTI_QUERY_DROP_SCOPE_PENALIZED_BRANCHES: bool = True
    RETRIEVAL_MULTI_QUERY_SCOPE_PENALTY_DROP_THRESHOLD: float = 0.95
    RETRIEVAL_PLAN_MAX_BRANCH_EXPANSIONS: int = 2
    RETRIEVAL_PLAN_EARLY_EXIT_SCOPE_PENALTY: float = 0.8
    RETRIEVAL_COVERAGE_GRAPH_EXPANSION_ENABLED: bool = True
    RETRIEVAL_COVERAGE_GRAPH_EXPANSION_MAX_HOPS: int = 2
    RAPTOR_STRUCTURAL_MODE_ENABLED: bool = True
    RAPTOR_SUMMARIZATION_MAX_CONCURRENCY: int = 8
    AUTHORITY_CLASSIFIER_MODE: str = "rules"  # rules | embedding_first

    # Visual router
    VISUAL_ROUTER_MAX_VISUAL_RATIO: float = 0.35
    VISUAL_ROUTER_MAX_VISUAL_PAGES: int = 12
    VISUAL_ROUTER_FULL_PAGE_MIN_SCORE: int = 5
    VISUAL_ROUTER_ALWAYS_VISUAL_SCORE: int = 8
    VISUAL_PARSE_TIMEOUT_SECONDS: int = 120
    VISUAL_PIPELINE_MAX_PARALLEL: int = 3
    VISUAL_MIN_IMAGE_BYTES: int = 16384
    VISUAL_MIN_IMAGE_WIDTH: int = 200
    VISUAL_MIN_IMAGE_HEIGHT: int = 200
    VISUAL_DEDUP_IN_DOCUMENT: bool = True
    VISUAL_CACHE_PROMPT_VERSION: str = "v1"
    VISUAL_CACHE_SCHEMA_VERSION: str = "VisualParseResult:v1"
    VISUAL_CACHE_KEY_V2_ENABLED: bool = True
    VISUAL_CACHE_BATCH_PREFETCH_ENABLED: bool = True

    # Deferred enrichment pipeline
    INGESTION_ENRICHMENT_ASYNC_ENABLED: bool = True
    INGESTION_GRAPH_BATCH_SIZE: int = 4
    GRAPH_EXTRACTION_BATCH_MAX_CHARS: int = 6000
    GRAPH_EXTRACTION_BATCH_MAX_ESTIMATED_TOKENS: int = 1500
    GRAPH_EXTRACTION_SINGLE_CHUNK_MAX_CHARS: int = 6000
    GRAPH_EXTRACTION_MAX_CONCURRENCY: int = 6
    GRAPH_EXTRACTION_RETRY_MAX_ATTEMPTS: int = 3
    GRAPH_EXTRACTION_RETRY_BASE_DELAY_SECONDS: float = 0.8
    GRAPH_EXTRACTION_RETRY_MAX_DELAY_SECONDS: float = 8.0
    GRAPH_EXTRACTION_RETRY_JITTER_SECONDS: float = 0.35
    INGEST_SKIP_STRUCTURAL_EMBEDDING: bool = True
    INGESTION_VISUAL_ASYNC_ENABLED: bool = True
    METRICS_EMBEDDING_SPAN_MIN_MS: float = 800.0
    INGESTION_GRAPH_CHUNK_LOG_EVERY_N: int = 25
    ENRICHMENT_WORKER_CONCURRENCY: int = 4
    ENRICHMENT_JOB_TIMEOUT_SECONDS: int = 900

    @field_validator("JINA_MODE", mode="before")
    @classmethod
    def _normalize_jina_mode(cls, value: str | None) -> str:
        return str(value or "CLOUD").strip().upper()

    @field_validator("APP_ENV", "ENVIRONMENT", mode="before")
    @classmethod
    def _normalize_environment_labels(cls, value: str | None) -> str:
        return str(value or "").strip().lower()

    @property
    def is_deployed_environment(self) -> bool:
        app_env = self.APP_ENV or self.ENVIRONMENT
        if app_env in {"staging", "production", "prod"}:
            return True
        if os.getenv("RAILWAY_ENVIRONMENT") or os.getenv("RAILWAY_PROJECT_ID"):
            return True
        return bool(self.RUNNING_IN_DOCKER and app_env not in {"", "local", "development", "dev"})

    @model_validator(mode="after")
    def _enforce_embedding_mode_constraints(self) -> "Settings":
        if self.is_deployed_environment and self.JINA_MODE == "LOCAL":
            logger.warning(
                "JINA_MODE=LOCAL is not allowed in deployed environments; forcing CLOUD",
                extra={"app_env": self.APP_ENV, "environment": self.ENVIRONMENT},
            )
            self.JINA_MODE = "CLOUD"
        self.EMBEDDING_PROVIDER_DEFAULT = (
            str(self.EMBEDDING_PROVIDER_DEFAULT or "jina").strip().lower()
        )
        if self.INGEST_EMBED_PROVIDER_DEFAULT:
            self.INGEST_EMBED_PROVIDER_DEFAULT = (
                str(self.INGEST_EMBED_PROVIDER_DEFAULT).strip().lower()
            )
        return self


settings = Settings()  # type: ignore[call-arg]
