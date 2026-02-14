from __future__ import annotations

import structlog

from app.api.v1.errors import ApiError
from app.core.observability.context_vars import get_tenant_id

logger = structlog.get_logger(__name__)


def require_tenant_from_context() -> str:
    tenant = str(get_tenant_id() or "").strip()
    if not tenant:
        raise ApiError(
            status_code=400,
            code="TENANT_HEADER_REQUIRED",
            message="Missing tenant context",
            details="X-Tenant-ID header is required",
        )
    return tenant


def enforce_tenant_match(tenant_from_payload: str | None, location: str) -> str:
    tenant_header = require_tenant_from_context()
    tenant_payload = str(tenant_from_payload or "").strip() or None

    mismatch = bool(tenant_payload and tenant_payload != tenant_header)
    logger.info(
        "tenant_guard_check",
        tenant_id_header=tenant_header,
        tenant_id_payload=tenant_payload,
        tenant_mismatch=mismatch,
        tenant_source=location,
    )

    if mismatch:
        raise ApiError(
            status_code=400,
            code="TENANT_MISMATCH",
            message="Tenant mismatch",
            details=f"Tenant in {location} must match X-Tenant-ID header",
        )

    return tenant_header
