
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field
from enum import Enum
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[4]
ROOT_ENV = PROJECT_ROOT / ".env"
ROOT_ENV_LOCAL = PROJECT_ROOT / ".env.local"
SERVICE_ENV = PROJECT_ROOT / "python-services" / "rag-ingestion" / ".env"
SERVICE_ENV_LOCAL = PROJECT_ROOT / "python-services" / "rag-ingestion" / ".env.local"

class RetrievalProfile(str, Enum):
    STRICT = "strict"
    BALANCED = "balanced"
    CREATIVE = "creative"

class RetrievalSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="RAG_",
        env_file=(
            str(ROOT_ENV),
            str(ROOT_ENV_LOCAL),
            str(SERVICE_ENV),
            str(SERVICE_ENV_LOCAL),
        ),
        extra="ignore"
    )

    # Similarity Thresholds
    MATCH_THRESHOLD_DEFAULT: float = 0.25
    MATCH_THRESHOLD_STRICT: float = 0.35
    
    # Hybrid Search Weights
    HYBRID_ALPHA: float = 0.5  # 1.0 = Only Vectors, 0.0 = Only Keywords
    
    # Retrieval Limits
    TOP_K: int = 10
    RERANKER_TOP_N: int = 5
    
    # Context Management
    MAX_CHUNKS_PER_PROMPT: int = 15
    CONTEXT_WINDOW_WIDTH: int = 1  # Number of surrounding chunks to include
    
    # Model Config
    EMBEDDING_TASK: str = "retrieval.query"

retrieval_settings = RetrievalSettings()
