"""
Centralized AI model configurations for the RAG ingestion service.
Follows the CISRE rule of centralized model registry.
"""
from app.core.settings import settings

class AIModelConfig:
    # Gemini Configuration
    GEMINI_API_KEY = settings.GEMINI_API_KEY
    GEMINI_MODEL_NAME = "gemini-2.5-flash"
    GEMINI_FLASH = "gemini-2.5-flash" # Alias for fast nodes
    
    # Groq Configuration (Primary Chat)
    GROQ_API_KEY = settings.GROQ_API_KEY
    GROQ_MODEL_DESIGN = "llama-3.3-70b-versatile"
    GROQ_MODEL_CHAT = "llama-3.1-8b-instant"
    GROQ_MODEL_FORENSIC = "llama-3.3-70b-versatile"
    
    # Validation Configuration
    JUDGE_MODEL = "gpt-4o"
    OPENAI_API_KEY = settings.OPENAI_API_KEY
    OPENAI_FALLBACK_MODEL = settings.OPENAI_FALLBACK_MODEL
    
    # Embedding Configuration (Jina v3)
    JINA_MODEL_NAME = settings.JINA_MODEL_NAME
    JINA_BASE_URL = settings.JINA_BASE_URL
    JINA_EMBEDDING_DIMENSIONS = settings.JINA_EMBEDDING_DIMENSIONS
    JINA_API_KEY = settings.JINA_API_KEY
    
    # Text Processing Limits
    MAX_CHARACTERS_PER_CHUNKING_BLOCK = 30000 
    MAX_GEMINI_PROMPT_CHARS = 100000

    # Default Temperatures
    DEFAULT_TEMPERATURE_CHAT = 0.0
    DEFAULT_TEMPERATURE_DESIGN = 0.2
    DEFAULT_TEMPERATURE_FORENSIC = 0.0

    @classmethod
    def is_gemini_available(cls) -> bool:
        return bool(cls.GEMINI_API_KEY)
