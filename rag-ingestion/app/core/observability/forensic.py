import structlog
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult
from langchain_core.messages import BaseMessage
from app.core.settings import settings

logger = structlog.get_logger("forensic")

class ForensicLevel:
    OFF = "OFF"
    FULL = "FULL"
    METADATA_ONLY = "METADATA_ONLY"

class ForensicRecorder:
    """
    Records deep cognitive traces for RAG retrieval and LLM generation.
    Enables "Glass Box" observability to troubleshoot RAG issues.
    """
    
    LEVEL = settings.FORENSIC_LOGGING_LEVEL or ForensicLevel.FULL

    @classmethod
    def record_retrieval(cls, query: str, results: List[Dict[str, Any]], metadata: Optional[Dict[str, Any]] = None):
        """
        Logs what the RAG retrieved before sending to the LLM.
        """
        if cls.LEVEL == ForensicLevel.OFF:
            return

        data = {
            "query": query,
            "results_count": len(results),
        }

        if cls.LEVEL == ForensicLevel.FULL:
            data["retrieved_chunks"] = [
                {
                    "id": str(r.get("id")),
                    "score": r.get("similarity") or r.get("score"),
                    "source": (r.get("metadata") or {}).get("filename") or (r.get("metadata") or {}).get("source"),
                    "snippet": (r.get("content") or "")[:100] + "..." if r.get("content") else None
                }
                for r in results
            ]
        else:
            data["retrieved_chunk_ids"] = [str(r.get("id")) for r in results]

        logger.info(
            "RAG Retrieval Trace",
            type="forensic_trace",
            stage="retrieval",
            data=data,
            **(metadata or {})
        )

    @classmethod
    def record_generation(cls, prompt: Any, response: str, model_params: Optional[Dict[str, Any]] = None):
        """
        Logs the final prompt (context + mandates) and the model's raw output.
        """
        if cls.LEVEL == ForensicLevel.OFF:
            return

        # Prompt can be a string (DSPy) or List[Dict] (OpenAI/LangChain)
        prompt_str = str(prompt)
        
        data = {
            "response_length": len(response),
            "model_params": model_params or {}
        }

        if cls.LEVEL == ForensicLevel.FULL:
            data["prompt"] = prompt_str
            data["response"] = response
        else:
            data["prompt_length"] = len(prompt_str)
            
        logger.info(
            "LLM Generation Trace",
            type="forensic_trace",
            stage="generation",
            data=data
        )

class ForensicCallbackHandler(BaseCallbackHandler):
    """
    LangChain Callback Handler for Forensic Observability.
    Captures prompts and responses from any LangChain LLM call.
    """
    def on_llm_start(
        self, serialized: Dict[str, Any], prompts: List[str], **kwargs: Any
    ) -> Any:
        # Cache the current prompt for on_llm_end
        self.current_prompts = prompts

    def on_chat_model_start(
        self, serialized: Dict[str, Any], messages: List[List[BaseMessage]], **kwargs: Any
    ) -> Any:
        # Cache messages
        self.current_messages = messages

    def on_llm_end(self, response: LLMResult, **kwargs: Any) -> Any:
        for i, generation in enumerate(response.generations):
            prompt = self.current_prompts[i] if hasattr(self, "current_prompts") else self.current_messages[i] if hasattr(self, "current_messages") else "Unknown"
            resp_text = generation[0].text
            ForensicRecorder.record_generation(
                prompt=prompt,
                response=resp_text,
                model_params={
                    "model": (response.llm_output or {}).get("model_name"),
                    "provider": "langchain_callback"
                }
            )

recorder = ForensicRecorder()
