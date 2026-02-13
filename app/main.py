
import structlog
from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError, ResponseValidationError
from fastapi.responses import JSONResponse

from app.api.v1.api_router import v1_router
from app.core.middleware.business_context import BusinessContextMiddleware
from app.core.observability.correlation import CorrelationMiddleware, get_correlation_id
from app.core.observability.logger_config import configure_structlog

# Configure Structlog (JSON Logging)
configure_structlog()
logger = structlog.get_logger(__name__)

app = FastAPI(
    title="CISRE Ingestion and Structured Retrieval API",
    description="Refactored SOLID architecture for cognitive ingestion and structured retrieval workflows.",
    version="2.0.0"
)


# Register Middleware (Stack order: Last added runs FIRST)

# 2. Business Context Middleware (Inner)
app.add_middleware(BusinessContextMiddleware)
    
# 1. Correlation Middleware (Outer) - Generates/Extracts Request ID
app.add_middleware(CorrelationMiddleware)

@app.exception_handler(ResponseValidationError)
async def response_validation_exception_handler(request: Request, exc: ResponseValidationError):
    """
    Handles errors when the backend fails to match the output contract (response_model).
    """
    logger.error(
        "backend_contract_breach",
        type="contract_violation",
        direction="outbound_backend",
        endpoint=str(request.url),
        validation_errors=exc.errors(),
        message="Backend failed to fulfill the data contract for the response."
    )
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "Internal Server Error: Data Contract Breach", "type": "backend_contract_breach"},
    )

@app.exception_handler(RequestValidationError)
async def request_validation_exception_handler(request: Request, exc: RequestValidationError):
    """
    Handles errors when the incoming data doesn't match the input contract.
    Useful for detecting out-of-sync frontend calls.
    """
    logger.warn(
        "frontend_contract_breach",
        type="contract_violation",
        direction="inbound_backend",
        endpoint=str(request.url),
        validation_errors=exc.errors(),
    )
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"detail": exc.errors(), "type": "frontend_contract_breach"},
    )


# Include Modular Routers
app.include_router(v1_router)


@app.get("/health")
def health_check():
    """
    Service health check.
    """
    return {
        "status": "ok",
        "service": "rag-engine",
        "api_v1": "available"
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
