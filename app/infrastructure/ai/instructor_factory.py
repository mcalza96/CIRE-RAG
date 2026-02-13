import logging
from typing import Any, Tuple

from app.core.ai_models import AIModelConfig

logger = logging.getLogger(__name__)


def _create_gemini_instructor_client(instructor_module: Any) -> Tuple[Any, str]:
    """Build Gemini instructor client using non-deprecated google.genai SDK."""
    if not AIModelConfig.GEMINI_API_KEY:
        raise ValueError("Missing GEMINI_API_KEY")

    try:
        from google import genai
    except ImportError as exc:
        raise ImportError("google-genai package not installed") from exc

    client = genai.Client(api_key=AIModelConfig.GEMINI_API_KEY)

    factory_fn = getattr(instructor_module, "from_genai", None)
    if factory_fn is None:
        factory_fn = getattr(instructor_module, "from_gemini", None)
    if factory_fn is None:
        raise AttributeError("Instructor build does not support Gemini adapters (from_genai/from_gemini missing)")

    mode = getattr(instructor_module.Mode, "GEMINI_JSON", instructor_module.Mode.JSON)
    wrapped = factory_fn(
        client=client,
        mode=mode,
    )
    return wrapped, AIModelConfig.GEMINI_MODEL_NAME


def create_async_instructor_client() -> Tuple[Any, str]:
    """Create and return an async instructor client and model name."""
    try:
        import instructor
        import httpx
    except ImportError:
        raise ImportError(
            "The 'instructor' and 'httpx' libraries are required for async structured generation."
        )

    _ = httpx

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

    if AIModelConfig.GEMINI_API_KEY:
        try:
            return _create_gemini_instructor_client(instructor)
        except Exception as exc:
            logger.warning(f"Gemini async client init failed: {exc}")

    openai_key = AIModelConfig.OPENAI_API_KEY
    if openai_key:
        try:
            from openai import AsyncOpenAI

            client = instructor.from_openai(AsyncOpenAI(api_key=openai_key))
            return client, AIModelConfig.OPENAI_FALLBACK_MODEL
        except ImportError:
            pass

    raise ValueError("No valid AI provider found for async instructor client.")


def create_instructor_client() -> Tuple[Any, str]:
    """Create and return a configured instructor client and model name."""
    try:
        import instructor
    except ImportError:
        raise ImportError(
            "The 'instructor' library is required for structured generation. "
            "Install it with: pip install instructor"
        )

    if AIModelConfig.GROQ_API_KEY:
        try:
            from groq import Groq

            client = instructor.from_groq(
                Groq(api_key=AIModelConfig.GROQ_API_KEY),
                mode=instructor.Mode.JSON,
            )
            model = AIModelConfig.GROQ_MODEL_FORENSIC
            logger.debug(f"Instructor client using Groq: {model}")
            return client, model
        except ImportError:
            logger.warning("groq package not installed, trying next provider")

    if AIModelConfig.GEMINI_API_KEY:
        try:
            client, model = _create_gemini_instructor_client(instructor)
            logger.debug(f"Instructor client using Gemini: {model}")
            return client, model
        except Exception as exc:
            logger.warning(f"google-genai / instructor Gemini init failed: {exc}")

    openai_key = AIModelConfig.OPENAI_API_KEY
    if openai_key:
        try:
            from openai import OpenAI

            client = instructor.from_openai(OpenAI(api_key=openai_key))
            model = AIModelConfig.OPENAI_FALLBACK_MODEL
            logger.debug(f"Instructor client using OpenAI: {model}")
            return client, model
        except ImportError:
            logger.warning("openai package not installed")

    raise ValueError(
        "No valid AI provider found for instructor client. "
        "Set GROQ_API_KEY, GEMINI_API_KEY, or OPENAI_API_KEY."
    )
