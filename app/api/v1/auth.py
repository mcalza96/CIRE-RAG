from __future__ import annotations

from fastapi import Header

from app.api.v1.errors import ApiError
from app.core.settings import settings


def _extract_bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    value = authorization.strip()
    if not value:
        return None
    if value.lower().startswith("bearer "):
        return value[7:].strip() or None
    return None


async def require_service_auth(
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_service_secret: str | None = Header(default=None, alias="X-Service-Secret"),
) -> None:
    """
    Enforces API auth only in deployed environments.
    Accepts either Bearer token or X-Service-Secret using RAG_SERVICE_SECRET value.
    """
    if not settings.is_deployed_environment:
        return

    expected = str(settings.RAG_SERVICE_SECRET or "").strip()
    if not expected or expected == "development-secret":
        raise ApiError(
            status_code=500,
            code="AUTH_MISCONFIGURED",
            message="Service secret must be configured in deployed environments",
        )

    bearer = _extract_bearer_token(authorization)
    candidate = bearer or (x_service_secret.strip() if x_service_secret else None)
    if candidate != expected:
        raise ApiError(
            status_code=401,
            code="UNAUTHORIZED",
            message="Unauthorized",
            details="Missing or invalid service token",
        )
