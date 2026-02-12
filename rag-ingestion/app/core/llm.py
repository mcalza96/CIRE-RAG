from langchain_core.language_models.chat_models import BaseChatModel
from app.core.ai_models import AIModelConfig
from typing import Optional, Dict
from app.core.observability.forensic import ForensicCallbackHandler
from app.core.observability.metrics import MetricsCallbackHandler

def get_llm(temperature: Optional[float] = None, capability: str = "CHAT", logit_bias: Optional[Dict[int, int]] = None) -> BaseChatModel:
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
        else:
            temperature = AIModelConfig.DEFAULT_TEMPERATURE_CHAT
    
    # 1. Groq (Primary Preference)
    if AIModelConfig.GROQ_API_KEY:
        try:
            from langchain_groq import ChatGroq
            
            # Select model based on capability
            model_name = AIModelConfig.GROQ_MODEL_CHAT
            if capability == "DESIGN":
                model_name = AIModelConfig.GROQ_MODEL_DESIGN
            elif capability == "FORENSIC":
                model_name = AIModelConfig.GROQ_MODEL_FORENSIC
                
            return ChatGroq(
                model_name=model_name,
                temperature=temperature,
                api_key=AIModelConfig.GROQ_API_KEY,
                model_kwargs={"logit_bias": logit_bias} if logit_bias else {},
                callbacks=[ForensicCallbackHandler(), MetricsCallbackHandler(span_name="span:llm_inference")]
            )
        except ImportError:
            print("⚠️ langchain-groq not installed. Skipping Groq.")

    # 2. Gemini (Secondary)
    if AIModelConfig.is_gemini_available():
        try:
            from langchain_google_genai import ChatGoogleGenerativeAI
            return ChatGoogleGenerativeAI(
                model=AIModelConfig.GEMINI_MODEL_NAME,
                temperature=temperature,
                google_api_key=AIModelConfig.GEMINI_API_KEY,
                convert_system_message_to_human=True,
                callbacks=[ForensicCallbackHandler(), MetricsCallbackHandler(span_name="span:llm_inference")]
            )
        except ImportError:
            print("⚠️ langchain-google-genai not installed. Skipping Gemini.")

    raise ValueError("No valid AI Provider found. Set GROQ_API_KEY or GEMINI_API_KEY. OpenAI is disabled.")

