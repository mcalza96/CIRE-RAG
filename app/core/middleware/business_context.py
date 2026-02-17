import re

import structlog
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from app.core.observability.context_vars import bind_context, tenant_id_ctx, user_id_ctx
from app.core.observability.correlation import get_correlation_id

TENANT_ID_HEADER = "X-Tenant-ID"
USER_ID_HEADER = "X-User-ID"
EXEMPT_PATHS = {"/health", "/openapi.json", "/api/v1/management/tenants"}
TENANT_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{1,127}$")

logger = structlog.get_logger(__name__)


class BusinessContextMiddleware(BaseHTTPMiddleware):
    """
    Extracts business context (Tenant, User) from headers
    and makes them available via ContextVars and structlog context.
    """

    @staticmethod
    def _error(status_code: int, code: str, message: str, details: str) -> JSONResponse:
        return JSONResponse(
            status_code=status_code,
            content={
                "error": {
                    "code": code,
                    "message": message,
                    "details": details,
                    "request_id": get_correlation_id(),
                }
            },
        )

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path in EXEMPT_PATHS or path.startswith("/docs"):
            return await call_next(request)

        # Extract headers (populated by orchestrator)
        tenant_id = request.headers.get(TENANT_ID_HEADER)
        user_id = request.headers.get(USER_ID_HEADER)

        tenant_norm = str(tenant_id or "").strip()
        if not tenant_norm:
            return self._error(
                status_code=400,
                code="TENANT_HEADER_REQUIRED",
                message="Missing tenant context",
                details=f"Missing required {TENANT_ID_HEADER} header",
            )
        if not TENANT_ID_PATTERN.fullmatch(tenant_norm):
            return self._error(
                status_code=400,
                code="INVALID_TENANT_HEADER",
                message="Invalid tenant context",
                details=f"Invalid {TENANT_ID_HEADER} header format",
            )

        # 1. Bind for structlog only after strict validation
        bind_context(tenant_id=tenant_norm, user_id=user_id)

        logger.debug(
            "business_context_bound",
            tenant_id_header=tenant_norm,
            user_id=user_id,
            request_path=path,
            request_method=request.method,
        )

        # 2. Set for ContextVars (Logic compatibility)
        tenant_token = None
        user_token = None

        if tenant_norm:
            tenant_token = tenant_id_ctx.set(tenant_norm)
        if user_id:
            user_token = user_id_ctx.set(user_id)

        try:
            return await call_next(request)
        finally:
            # Clean up context strictly
            if tenant_token:
                tenant_id_ctx.reset(tenant_token)
            if user_token:
                user_id_ctx.reset(user_token)
