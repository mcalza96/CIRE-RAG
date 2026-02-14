from __future__ import annotations

import structlog
from fastapi import Security
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer

from app.api.v1.errors import ApiError
from app.core.settings import settings

logger = structlog.get_logger(__name__)

bearer_auth = HTTPBearer(
    auto_error=False,
    scheme_name="BearerAuth",
    description="Authorization Bearer token. Use RAG_SERVICE_SECRET as token value.",
)
service_secret_auth = APIKeyHeader(
    name="X-Service-Secret",
    auto_error=False,
    scheme_name="ServiceSecretAuth",
    description="Service secret header for S2S calls.",
)


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
    bearer_credentials: HTTPAuthorizationCredentials | None = Security(bearer_auth),
    x_service_secret: str | None = Security(service_secret_auth),
) -> None:
    """
    Enforces API auth only in deployed environments.
    Accepts either Bearer token or X-Service-Secret using RAG_SERVICE_SECRET value.
    """
    expected = str(settings.RAG_SERVICE_SECRET or "").strip()
    env_inconsistent = (expected and expected != "development-secret") and (
        settings.RUNNING_IN_DOCKER
        or bool(settings.APP_ENV in {"staging", "production", "prod"})
        or bool(settings.ENVIRONMENT in {"staging", "production", "prod"})
    )
    if not settings.is_deployed_environment:
        logger.info(
            "service_auth_bypass",
            auth_mode="local_bypass",
            service_secret_configured=bool(expected and expected != "development-secret"),
        )
        if env_inconsistent:
            logger.critical(
                "service_auth_env_inconsistent",
                app_env=settings.APP_ENV,
                environment=settings.ENVIRONMENT,
                running_in_docker=settings.RUNNING_IN_DOCKER,
            )
            raise ApiError(
                status_code=500,
                code="AUTH_ENV_INCONSISTENT",
                message="Invalid auth environment configuration",
                details="Auth bypass active while runtime signals non-local deployment",
            )
        return

    logger.info("service_auth_mode", auth_mode="deployed", service_secret_configured=bool(expected))
    if not expected or expected == "development-secret":
        raise ApiError(
            status_code=500,
            code="AUTH_MISCONFIGURED",
            message="Service secret must be configured in deployed environments",
        )

    bearer = None
    if bearer_credentials and str(bearer_credentials.scheme or "").lower() == "bearer":
        bearer = (bearer_credentials.credentials or "").strip() or None
    header_secret = x_service_secret.strip() if x_service_secret else None
    candidate = bearer or header_secret
    caller_auth_mode = "bearer" if bearer else ("x_service_secret" if header_secret else "missing")

    logger.info("service_auth_attempt", caller_auth_mode=caller_auth_mode)

    if candidate != expected:
        logger.warning("service_auth_failed", caller_auth_mode=caller_auth_mode)
        raise ApiError(
            status_code=401,
            code="UNAUTHORIZED",
            message="Unauthorized",
            details="Missing or invalid service token",
        )

    logger.info("service_auth_ok", caller_auth_mode=caller_auth_mode)
