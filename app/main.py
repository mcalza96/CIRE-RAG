
import structlog
from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError, ResponseValidationError
from fastapi.responses import JSONResponse

from app.api.v1.errors import ApiError, api_error_exception_handler
from app.api.v1.api_router import v1_router
from app.core.middleware.business_context import BusinessContextMiddleware
from app.core.observability.correlation import CorrelationMiddleware, get_correlation_id
from app.core.observability.logger_config import configure_structlog
from app.core.settings import settings

# Configure Structlog (JSON Logging)
configure_structlog()
logger = structlog.get_logger(__name__)
logger.info(
    "auth_runtime_mode",
    auth_mode="deployed" if settings.is_deployed_environment else "local_bypass",
    service_secret_configured=bool(str(settings.RAG_SERVICE_SECRET or "").strip() and str(settings.RAG_SERVICE_SECRET).strip() != "development-secret"),
    app_env=settings.APP_ENV,
    environment=settings.ENVIRONMENT,
)

app = FastAPI(
    title="CIRE-RAG Ingestion and Structured Retrieval API",
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
        content={
            "error": {
                "code": "BACKEND_CONTRACT_BREACH",
                "message": "Internal Server Error: Data Contract Breach",
                "details": exc.errors(),
                "request_id": get_correlation_id(),
            }
        },
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
        content={
            "error": {
                "code": "FRONTEND_CONTRACT_BREACH",
                "message": "Request validation failed",
                "details": exc.errors(),
                "request_id": get_correlation_id(),
            }
        },
    )


@app.exception_handler(ApiError)
async def api_error_handler(request: Request, exc: ApiError):
    return await api_error_exception_handler(request, exc)


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
