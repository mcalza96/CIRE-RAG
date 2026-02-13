from contextvars import ContextVar
from structlog.contextvars import bind_contextvars

# Context Variables for Business Domain
tenant_id_ctx: ContextVar[str] = ContextVar("tenant_id", default=None)
user_id_ctx: ContextVar[str] = ContextVar("user_id", default=None)

def get_tenant_id() -> str:
    return tenant_id_ctx.get()

def get_user_id() -> str:
    return user_id_ctx.get()

def bind_context(**kwargs):
    """
    Binds the provided key-value pairs to the current structlog context.
    """
    bind_contextvars(**kwargs)
