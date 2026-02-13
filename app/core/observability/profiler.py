import time
import json
import functools
from contextvars import ContextVar
from typing import Optional
import logging

# Configure logger
logger = logging.getLogger("latency_monitor")
logger.setLevel(logging.INFO)
handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter('%(message)s'))
logger.addHandler(handler)

from app.core.observability.correlation import get_correlation_id

# Deprecating old trace_context for unified correlation_id
# We use get_correlation_id() which pulls from correlation_id_ctx

def profile_step(step_name: Optional[str] = None):
    """
    Async decorator to measure execution time of a function/node.
    Logs structured JSON with trace_id.
    """
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            nonlocal step_name
            if step_name is None:
                step_name = func.__name__

            start_time = time.perf_counter()
            correlation_id = get_correlation_id()
            
            try:
                result = await func(*args, **kwargs)
                return result
            finally:
                duration_ms = (time.perf_counter() - start_time) * 1000
                
                log_payload = {
                    "level": "info",
                    "type": "latency_metric",
                    "correlation_id": correlation_id,
                    "span_name": step_name,
                    "duration_ms": round(duration_ms, 2),
                    "timestamp": time.time()
                }
                
                # Print proper JSON for log aggregators (using correlation_id)
                print(f"[PY-LATENCY] {json.dumps(log_payload)}")
        return wrapper
    return decorator

# Synchronous version if needed
def profile_step_sync(step_name: Optional[str] = None):
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            nonlocal step_name
            if step_name is None:
                step_name = func.__name__

            start_time = time.perf_counter()
            correlation_id = get_correlation_id()
            
            try:
                result = func(*args, **kwargs)
                return result
            finally:
                duration_ms = (time.perf_counter() - start_time) * 1000
                
                log_payload = {
                    "level": "info",
                    "type": "latency_metric",
                    "correlation_id": correlation_id,
                    "span_name": step_name,
                    "duration_ms": round(duration_ms, 2),
                    "timestamp": time.time()
                }
                print(f"[PY-LATENCY] {json.dumps(log_payload)}")
        return wrapper
    return decorator
