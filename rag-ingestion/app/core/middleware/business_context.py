from contextvars import ContextVar
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from app.core.observability.context_vars import tenant_id_ctx, user_id_ctx, bind_context, get_tenant_id, get_user_id

TENANT_ID_HEADER = "X-Tenant-ID"
USER_ID_HEADER = "X-User-ID"

class BusinessContextMiddleware(BaseHTTPMiddleware):
    """
    Extracts business context (Tenant, User) from headers
    and makes them available via ContextVars and structlog context.
    """
    
    async def dispatch(self, request: Request, call_next):
        # Extract headers (populated by Next.js Middleware)
        tenant_id = request.headers.get(TENANT_ID_HEADER)
        user_id = request.headers.get(USER_ID_HEADER)
        
        # 1. Bind for structlog (Phase 2)
        bind_context(tenant_id=tenant_id, user_id=user_id)
        
        # 2. Set for ContextVars (Logic compatibility)
        tenant_token = None
        user_token = None
        
        if tenant_id:
            tenant_token = tenant_id_ctx.set(tenant_id)
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
