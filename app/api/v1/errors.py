from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

from app.core.observability.correlation import get_correlation_id


def _error_example(code: str, message: str, details: Any) -> dict[str, Any]:
    return {
        "error": {
            "code": code,
            "message": message,
            "details": details,
            "request_id": "f6a4c304-1ce0-4cf5-9d8f-5d4deca4f51f",
        }
    }


ERROR_RESPONSES: dict[int, dict[str, Any]] = {
    400: {
        "description": "Bad Request",
        "content": {
            "application/json": {
                "example": _error_example("INVALID_REQUEST", "Invalid request", "Validation failed")
            }
        },
    },
    401: {
        "description": "Unauthorized",
        "content": {
            "application/json": {
                "example": _error_example("UNAUTHORIZED", "Unauthorized", None)
            }
        },
    },
    404: {
        "description": "Not Found",
        "content": {
            "application/json": {
                "example": _error_example("NOT_FOUND", "Resource not found", None)
            }
        },
    },
    409: {
        "description": "Conflict",
        "content": {
            "application/json": {
                "example": _error_example("CONFLICT", "Resource conflict", None)
            }
        },
    },
    422: {
        "description": "Unprocessable Entity",
        "content": {
            "application/json": {
                "example": _error_example(
                    "FRONTEND_CONTRACT_BREACH",
                    "Request validation failed",
                    [{"loc": ["query", "tenant_id"], "msg": "Field required"}],
                )
            }
        },
    },
    429: {
        "description": "Too Many Requests",
        "content": {
            "application/json": {
                "example": _error_example("INGESTION_BACKPRESSURE", "Ingestion queue is saturated", None)
            }
        },
    },
    500: {
        "description": "Internal Server Error",
        "content": {
            "application/json": {
                "example": _error_example("INTERNAL_ERROR", "Internal server error", None)
            }
        },
    },
}


class ApiError(Exception):
    def __init__(self, status_code: int, code: str, message: str, details: Any = None):
        super().__init__(message)
        self.status_code = int(status_code)
        self.code = code
        self.message = message
        self.details = details


async def api_error_exception_handler(_: Request, exc: ApiError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {
                "code": exc.code,
                "message": exc.message,
                "details": exc.details,
                "request_id": get_correlation_id(),
            }
        },
    )
