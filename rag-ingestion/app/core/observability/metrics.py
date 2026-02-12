import asyncio
import functools
import time
import structlog
from typing import Any, Callable, Dict, List, Optional
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult

logger = structlog.get_logger("metrics")

def track_span(name: str):
    """
    Decorator to track execution time and metrics for a function.
    Supports both sync and async functions.
    """
    def decorator(func: Callable):
        if asyncio.iscoroutinefunction(func):
            @functools.wraps(func)
            async def wrapper(*args, **kwargs):
                start_time = time.perf_counter()
                try:
                    result = await func(*args, **kwargs)
                    duration_ms = (time.perf_counter() - start_time) * 1000
                    _log_span(name, duration_ms, result, kwargs)
                    return result
                except Exception as e:
                    _log_span_error(name, (time.perf_counter() - start_time) * 1000, e)
                    raise
            return wrapper
        else:
            @functools.wraps(func)
            def wrapper(*args, **kwargs):
                start_time = time.perf_counter()
                try:
                    result = func(*args, **kwargs)
                    duration_ms = (time.perf_counter() - start_time) * 1000
                    _log_span(name, duration_ms, result, kwargs)
                    return result
                except Exception as e:
                    _log_span_error(name, (time.perf_counter() - start_time) * 1000, e)
                    raise
            return wrapper
    return decorator

def _log_span(name: str, duration_ms: float, result: Any, kwargs: Dict):
    metrics = {"duration_ms": round(duration_ms, 2)}
    meta = {}

    # 1. Extraction of Token Usage (LangChain / LLM Result)
    # Different providers return usage in different attributes
    usage = None
    
    # Try usage_metadata (LangChain >= 0.2 style)
    if hasattr(result, "usage_metadata") and result.usage_metadata:
        usage = result.usage_metadata
    # Try additional_kwargs for older LangChain versions
    elif hasattr(result, "additional_kwargs") and "token_usage" in result.additional_kwargs:
        usage = result.additional_kwargs["token_usage"]
    # Try raw response usage attribute (OpenAI/Groq SDK)
    elif hasattr(result, "usage") and result.usage:
        usage = result.usage

    if usage:
        if isinstance(usage, dict):
            metrics["tokens_prompt"] = usage.get("prompt_tokens") or usage.get("input_tokens")
            metrics["tokens_completion"] = usage.get("completion_tokens") or usage.get("output_tokens")
            metrics["tokens_total"] = usage.get("total_tokens")
        else:
            # Pydantic or NamedTuple style
            metrics["tokens_prompt"] = getattr(usage, "prompt_tokens", getattr(usage, "input_tokens", None))
            metrics["tokens_completion"] = getattr(usage, "completion_tokens", getattr(usage, "output_tokens", None))
            metrics["tokens_total"] = getattr(usage, "total_tokens", None)

    # 2. Extract Metadata from kwargs
    for key in ["model", "model_name", "model_id", "tenant_id", "user_id"]:
        if key in kwargs:
            meta[key] = kwargs[key]

    # If it's a class method, 'self' is args[0]
    # We could try to extract model from self if it has a .model attribute
    
    logger.info(
        "span_complete",
        type="metric_span",
        span_name=name,
        metrics={k: v for k, v in metrics.items() if v is not None},
        meta={k: v for k, v in meta.items() if v is not None}
    )

def _log_span_error(name: str, duration_ms: float, error: Exception):
    logger.error(
        "span_error",
        type="metric_span",
        span_name=name,
        metrics={"duration_ms": round(duration_ms, 2)},
        error_type=type(error).__name__,
        error_message=str(error)
    )

class MetricsCallbackHandler(BaseCallbackHandler):
    """
    LangChain Callback for automatic span metrics.
    """
    def __init__(self, span_name: str = "span:llm_inference"):
        self.span_name = span_name
        self._start_time = None

    def on_llm_start(self, *args, **kwargs) -> Any:
        self._start_time = time.perf_counter()

    def on_chat_model_start(self, *args, **kwargs) -> Any:
        self._start_time = time.perf_counter()

    def on_llm_end(self, response: LLMResult, **kwargs: Any) -> Any:
        if self._start_time:
            duration_ms = (time.perf_counter() - self._start_time) * 1000
            _log_span(self.span_name, duration_ms, response, kwargs)

    def on_llm_error(self, error: Exception, **kwargs: Any) -> Any:
        if self._start_time:
            duration_ms = (time.perf_counter() - self._start_time) * 1000
            _log_span_error(self.span_name, duration_ms, error)
