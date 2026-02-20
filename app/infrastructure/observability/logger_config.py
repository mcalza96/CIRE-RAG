import logging
import structlog
from structlog.contextvars import bind_contextvars, merge_contextvars
from app.infrastructure.observability.context_vars import get_tenant_id, get_user_id, bind_context
from app.infrastructure.observability.correlation import get_correlation_id, CorrelationLogFilter
from app.infrastructure.settings import settings


# bind_context is now imported from context_vars

def add_context_vars(_, __, event_dict):
    """
    Processor to inject ContextVars into the log event matching Pino Canonical Schema.
    """
    # 1. Correlation ID (Root level)
    cid = get_correlation_id()
    if cid:
        event_dict["correlation_id"] = cid

    # 2. Trace Metadata (Nested under 'trace')
    trace = {
        "tenant_id": get_tenant_id(),
        "user_id": get_user_id()
    }
    
    # Merge with any existing trace info
    existing_trace = event_dict.get("trace", {})
    if isinstance(existing_trace, dict):
        trace.update(existing_trace)
    
    event_dict["trace"] = {k: v for k, v in trace.items() if v is not None}

    # 3. Canonical Message field rename
    if "event" in event_dict:
        event_dict["message"] = event_dict.pop("event")
        
    return event_dict

def configure_structlog():
    """
    Configures structlog to replace standard logging with Canonical JSON.
    """
    # Standard Logging Integration
    handler = logging.StreamHandler()
    handler.addFilter(CorrelationLogFilter())
    
    # Standard formatter for non-JSON logs (FastAPI startup, etc.)
    standard_formatter = logging.Formatter(
        " [%(asctime)s] [%(levelname)s] [trace_id=%(correlation_id)s] %(name)s: %(message)s"
    )
    handler.setFormatter(standard_formatter)
    
    resolved_level = str(settings.LOG_LEVEL or "INFO").upper()
    log_level = getattr(logging, resolved_level, logging.INFO)

    logging.basicConfig(
        format="%(message)s",
        level=log_level,
        handlers=[handler]
    )

    processors = [
        merge_contextvars,
        add_context_vars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.JSONRenderer()
    ]

    structlog.configure(
        processors=processors,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )
