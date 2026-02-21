import pytest

from app.api.v1.errors import ApiError
from app.api.v1.tenant_guard import enforce_tenant_match, require_tenant_from_context
from app.infrastructure.observability.context_vars import tenant_id_ctx


def test_require_tenant_from_context_raises_when_missing() -> None:
    with pytest.raises(ApiError) as exc:
        require_tenant_from_context()
    assert exc.value.code == "TENANT_HEADER_REQUIRED"


def test_enforce_tenant_match_raises_when_mismatch() -> None:
    token = tenant_id_ctx.set("tenant-a")
    try:
        with pytest.raises(ApiError) as exc:
            enforce_tenant_match("tenant-b", "body.tenant_id")
        assert exc.value.code == "TENANT_MISMATCH"
    finally:
        tenant_id_ctx.reset(token)


def test_enforce_tenant_match_returns_header_tenant_when_match() -> None:
    token = tenant_id_ctx.set("tenant-a")
    try:
        assert enforce_tenant_match("tenant-a", "body.tenant_id") == "tenant-a"
    finally:
        tenant_id_ctx.reset(token)
