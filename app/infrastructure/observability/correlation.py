from contextvars import ContextVar
from uuid import uuid4
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.types import ASGIApp, Receive, Scope, Send

# Context Variable to store the Correlation ID
# Default is None to detect missing contexts if needed, or we could default to 'unknown'
correlation_id_ctx: ContextVar[str] = ContextVar("correlation_id", default=None)

CORRELATION_ID_HEADER = "X-Correlation-ID"

import structlog

logger = structlog.get_logger(__name__)

def get_correlation_id() -> str:
    """Returns the current correlation ID. Defaults to 'unknown' if not set."""
    return correlation_id_ctx.get() or "unknown"

def set_correlation_id(correlation_id: str) -> None:
    """Sets the correlation ID in the current context."""
    correlation_id_ctx.set(correlation_id)

class CorrelationMiddleware(BaseHTTPMiddleware):
    """
    Middleware that extracts the Correlation ID from the request headers
    and sets it in the ContextVar for the duration of the request.
    """
    
    async def dispatch(self, request: Request, call_next):
        # Extract from header (Prioritize Canonical Next.js Header)
        # request.headers names are normalized to lowercase in Starlette
        incoming_id = (
            request.headers.get("x-correlation-id") or 
            request.headers.get("x-trace-id") or 
            request.headers.get("x-request-id") or
            request.headers.get(CORRELATION_ID_HEADER.lower())
        )
        
        if not incoming_id:
            incoming_id = str(uuid4())
            noisy_path_prefixes = (
                "/health",
                "/api/v1/ingestion/documents",
                "/api/v1/ingestion/collections",
                "/api/v1/ingestion/batches/",
            )
            is_noisy_path = any(request.url.path.startswith(prefix) for prefix in noisy_path_prefixes)
            log_method = logger.debug if is_noisy_path else logger.warning
            log_method(
                "Missing Trace headers in incoming request. "
                "Generated new trace ID as origin.",
                new_trace_id=incoming_id,
                path=request.url.path
            )
        
        # Set context
        token = correlation_id_ctx.set(incoming_id)
        
        try:
            response = await call_next(request)
            # Propagate back to client
            response.headers[CORRELATION_ID_HEADER] = incoming_id
            return response
            
        finally:
            # Clean up context strictly
            correlation_id_ctx.reset(token)

import logging

class CorrelationLogFilter(logging.Filter):
    """
    Logging filter to inject correlation ID into log records.
    """
    def filter(self, record):
        record.correlation_id = get_correlation_id()
        return True
