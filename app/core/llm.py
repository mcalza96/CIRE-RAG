from langchain_core.language_models.chat_models import BaseChatModel
from app.core.ai_models import AIModelConfig
from typing import Optional, Dict
from app.core.observability.forensic import ForensicCallbackHandler
from app.core.observability.metrics import MetricsCallbackHandler

def get_llm(
    temperature: Optional[float] = None,
    capability: str = "CHAT",
    logit_bias: Optional[Dict[int, int]] = None,
    prefer_provider: str = "auto",
) -> BaseChatModel:
    """
    Returns the configured LLM based on environment variables and capability.
    Prioritizes Groq (User Preference) -> Gemini.
    """
    
    # 0. Set default temperature based on capability if not provided
    if temperature is None:
        if capability == "DESIGN":
            temperature = AIModelConfig.DEFAULT_TEMPERATURE_DESIGN
        elif capability == "FORENSIC":
            temperature = AIModelConfig.DEFAULT_TEMPERATURE_FORENSIC
        elif capability == "ORCHESTRATION":
            temperature = AIModelConfig.DEFAULT_TEMPERATURE_ORCHESTRATION
        elif capability == "GENERATION":
            temperature = AIModelConfig.DEFAULT_TEMPERATURE_GENERATION
        elif capability == "SUMMARIZATION":
            temperature = AIModelConfig.DEFAULT_TEMPERATURE_SUMMARIZATION
        else:
            temperature = AIModelConfig.DEFAULT_TEMPERATURE_CHAT

    def _build_groq() -> Optional[BaseChatModel]:
        if not AIModelConfig.GROQ_API_KEY:
            return None
        try:
            from langchain_groq import ChatGroq

            model_name = AIModelConfig.get_groq_model_for_capability(capability)

            return ChatGroq(
                model=model_name,
                temperature=temperature,
                api_key=AIModelConfig.GROQ_API_KEY,
                model_kwargs={"logit_bias": logit_bias} if logit_bias else {},
                callbacks=[ForensicCallbackHandler(), MetricsCallbackHandler(span_name="span:llm_inference")],
            )
        except ImportError:
            print("⚠️ langchain-groq not installed. Skipping Groq.")
            return None

    def _build_gemini() -> Optional[BaseChatModel]:
        if not AIModelConfig.is_gemini_available():
            return None
        try:
            from langchain_google_genai import ChatGoogleGenerativeAI

            return ChatGoogleGenerativeAI(
                model=AIModelConfig.GEMINI_MODEL_NAME,
                temperature=temperature,
                google_api_key=AIModelConfig.GEMINI_API_KEY,
                convert_system_message_to_human=True,
                callbacks=[ForensicCallbackHandler(), MetricsCallbackHandler(span_name="span:llm_inference")],
            )
        except ImportError:
            print("⚠️ langchain-google-genai not installed. Skipping Gemini.")
            return None

    normalized_preference = (prefer_provider or "auto").strip().lower()
    if normalized_preference not in {"auto", "groq", "gemini"}:
        normalized_preference = "auto"

    if normalized_preference == "auto":
        if capability == "ORCHESTRATION":
            provider_order = ["groq", "gemini"]
        elif capability == "GENERATION":
            provider_order = ["gemini", "groq"]
        else:
            provider_order = ["groq", "gemini"]
    elif normalized_preference == "groq":
        provider_order = ["groq", "gemini"]
    else:
        provider_order = ["gemini", "groq"]

    for provider in provider_order:
        model = _build_groq() if provider == "groq" else _build_gemini()
        if model is not None:
            return model

    raise ValueError("No valid AI Provider found. Set GROQ_API_KEY or GEMINI_API_KEY. OpenAI is disabled.")
