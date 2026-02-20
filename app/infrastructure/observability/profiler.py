import time
import functools
from typing import Optional
import structlog

from app.infrastructure.observability.correlation import get_correlation_id

logger = structlog.get_logger("latency_monitor")

def profile_step(step_name: Optional[str] = None):
    """
    Async decorator to measure execution time of a function/node.
    Logs structured data with correlation_id.
    """
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            target_step_name = step_name or func.__name__

            start_time = time.perf_counter()
            correlation_id = get_correlation_id()
            
            try:
                result = await func(*args, **kwargs)
                return result
            finally:
                duration_ms = (time.perf_counter() - start_time) * 1000
                
                logger.info(
                    "latency_metric",
                    correlation_id=correlation_id,
                    span_name=target_step_name,
                    duration_ms=round(duration_ms, 2)
                )
        return wrapper
    return decorator

def profile_step_sync(step_name: Optional[str] = None):
    """
    Synchronous decorator to measure execution time.
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            target_step_name = step_name or func.__name__

            start_time = time.perf_counter()
            correlation_id = get_correlation_id()
            
            try:
                result = func(*args, **kwargs)
                return result
            finally:
                duration_ms = (time.perf_counter() - start_time) * 1000
                
                logger.info(
                    "latency_metric",
                    correlation_id=correlation_id,
                    span_name=target_step_name,
                    duration_ms=round(duration_ms, 2)
                )
        return wrapper
    return decorator
