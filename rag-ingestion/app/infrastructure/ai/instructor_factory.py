import logging
from typing import Tuple, Any, Optional
from app.core.ai_models import AIModelConfig

logger = logging.getLogger(__name__)

class InstructorClientFactory:
    """
    Factory for instructor-patched clients.
    Handles provider selection and library-specific patching.
    """

    @staticmethod
    def create_async_client() -> Tuple[Any, str]:
        """
        Create and return a configured ASYNC instructor client and model name.
        """
        try:
            import instructor
            import httpx
        except ImportError:
            raise ImportError(
                "The 'instructor' and 'httpx' libraries are required for async structured generation."
            )

        # 1. Try Groq (Async)
        if AIModelConfig.GROQ_API_KEY:
            try:
                from groq import AsyncGroq
                client = instructor.from_groq(
                    AsyncGroq(api_key=AIModelConfig.GROQ_API_KEY),
                    mode=instructor.Mode.JSON,
                )
                return client, AIModelConfig.GROQ_MODEL_FORENSIC
            except ImportError:
                pass

        # 2. Try Gemini (Async)
        if AIModelConfig.GEMINI_API_KEY:
            try:
                import google.generativeai as genai
                genai.configure(api_key=AIModelConfig.GEMINI_API_KEY)
                # Gemini doesn't use httpx directly in from_gemini, but we want async support
                client = instructor.from_gemini(
                    client=genai.GenerativeModel(AIModelConfig.GEMINI_MODEL_NAME),
                    mode=instructor.Mode.GEMINI_JSON,
                )
                # Note: instructor.from_gemini actually supports async out of the box
                return client, AIModelConfig.GEMINI_MODEL_NAME
            except ImportError:
                pass
        
        # Fallback to OpenAI Async if needed
        openai_key = AIModelConfig.OPENAI_API_KEY
        if openai_key:
            try:
                from openai import AsyncOpenAI
                client = instructor.from_openai(AsyncOpenAI(api_key=openai_key))
                return client, AIModelConfig.OPENAI_FALLBACK_MODEL
            except ImportError:
                pass

        raise ValueError("No valid AI provider found for async instructor client.")

    @staticmethod
    def create_client() -> Tuple[Any, str]:
        """
        Create and return a configured instructor client and model name.
        
        Priority: Groq -> Gemini -> OpenAI
        """
        try:
            import instructor
        except ImportError:
            raise ImportError(
                "The 'instructor' library is required for structured generation. "
                "Install it with: pip install instructor"
            )

        # 1. Try Groq (Primary)
        if AIModelConfig.GROQ_API_KEY:
            try:
                from groq import Groq
                
                client = instructor.from_groq(
                    Groq(api_key=AIModelConfig.GROQ_API_KEY),
                    mode=instructor.Mode.JSON,
                )
                model = AIModelConfig.GROQ_MODEL_FORENSIC
                logger.debug(f"InstructorClientFactory using Groq: {model}")
                return client, model
                
            except ImportError:
                logger.warning("groq package not installed, trying next provider")

        # 2. Try Gemini
        if AIModelConfig.GEMINI_API_KEY:
            try:
                import google.generativeai as genai
                
                genai.configure(api_key=AIModelConfig.GEMINI_API_KEY)
                client = instructor.from_gemini(
                    client=genai.GenerativeModel(AIModelConfig.GEMINI_MODEL_NAME),
                    mode=instructor.Mode.GEMINI_JSON,
                )
                model = AIModelConfig.GEMINI_MODEL_NAME
                logger.debug(f"InstructorClientFactory using Gemini: {model}")
                return client, model
                
            except ImportError:
                logger.warning("google-generativeai package not installed")

        # 3. Try OpenAI
        openai_key = AIModelConfig.OPENAI_API_KEY
        if openai_key:
            try:
                from openai import OpenAI
                
                client = instructor.from_openai(OpenAI(api_key=openai_key))
                model = AIModelConfig.OPENAI_FALLBACK_MODEL
                logger.debug(f"InstructorClientFactory using OpenAI: {model}")
                return client, model
                
            except ImportError:
                logger.warning("openai package not installed")

        raise ValueError(
            "No valid AI provider found for instructor client. "
            "Set GROQ_API_KEY, GEMINI_API_KEY, or OPENAI_API_KEY."
        )
