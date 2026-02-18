"""
Centralized AI model configurations for the RAG ingestion service.
Follows the CIRE-RAG rule of centralized model registry.
"""

from app.core.settings import settings


class AIModelConfig:
    # Gemini Configuration
    GEMINI_API_KEY = settings.GEMINI_API_KEY
    GEMINI_MODEL_NAME = "gemini-2.5-flash-lite"
    GEMINI_FLASH = "gemini-2.5-flash-lite"  # Alias for fast nodes

    # Groq Configuration
    GROQ_API_KEY = settings.GROQ_API_KEY
    GROQ_MODEL_LIGHTWEIGHT = "openai/gpt-oss-20b"
    GROQ_MODEL_HEAVY = "openai/gpt-oss-120b"
    GROQ_MODEL_DESIGN = GROQ_MODEL_HEAVY
    GROQ_MODEL_CHAT = GROQ_MODEL_LIGHTWEIGHT
    GROQ_MODEL_FORENSIC = GROQ_MODEL_HEAVY
    GROQ_MODEL_ORCHESTRATION = GROQ_MODEL_LIGHTWEIGHT
    GROQ_MODEL_SUMMARIZATION = GROQ_MODEL_LIGHTWEIGHT

    # Validation Configuration
    JUDGE_MODEL = "gpt-4o"
    OPENAI_API_KEY = settings.OPENAI_API_KEY
    OPENAI_FALLBACK_MODEL = settings.OPENAI_FALLBACK_MODEL

    # Embedding Configuration (Jina v3)
    JINA_MODEL_NAME = settings.JINA_MODEL_NAME
    JINA_BASE_URL = settings.JINA_BASE_URL
    JINA_EMBEDDING_DIMENSIONS = settings.JINA_EMBEDDING_DIMENSIONS
    JINA_API_KEY = settings.JINA_API_KEY
    JINA_RERANK_URL = settings.JINA_RERANK_URL
    JINA_RERANK_MODEL = settings.JINA_RERANK_MODEL

    # Text Processing Limits
    MAX_CHARACTERS_PER_CHUNKING_BLOCK = 30000
    MAX_GEMINI_PROMPT_CHARS = 100000

    # Default Temperatures
    DEFAULT_TEMPERATURE_CHAT = 0.0
    DEFAULT_TEMPERATURE_DESIGN = 0.2
    DEFAULT_TEMPERATURE_FORENSIC = 0.0
    DEFAULT_TEMPERATURE_ORCHESTRATION = 0.0
    DEFAULT_TEMPERATURE_SUMMARIZATION = 0.3
    DEFAULT_TEMPERATURE_GENERATION = 0.2

    _LIGHTWEIGHT_CAPABILITIES = {
        "CHAT",
        "ORCHESTRATION",
        "SUMMARIZATION",
    }

    _HEAVY_CAPABILITIES = {
        "DESIGN",
        "FORENSIC",
        "GENERATION",
    }

    _GROQ_MODEL_BY_CAPABILITY = {
        "CHAT": GROQ_MODEL_CHAT,
        "ORCHESTRATION": GROQ_MODEL_ORCHESTRATION,
        "DESIGN": GROQ_MODEL_DESIGN,
        "FORENSIC": GROQ_MODEL_FORENSIC,
        "GENERATION": GROQ_MODEL_HEAVY,
        "SUMMARIZATION": GROQ_MODEL_SUMMARIZATION,
    }

    @classmethod
    def is_gemini_available(cls) -> bool:
        return bool(cls.GEMINI_API_KEY)

    @classmethod
    def get_groq_model_for_capability(cls, capability: str) -> str:
        normalized = (capability or "CHAT").strip().upper()
        return cls._GROQ_MODEL_BY_CAPABILITY.get(normalized, cls.GROQ_MODEL_CHAT)

    @classmethod
    def is_lightweight_capability(cls, capability: str) -> bool:
        normalized = (capability or "CHAT").strip().upper()
        return normalized in cls._LIGHTWEIGHT_CAPABILITIES
