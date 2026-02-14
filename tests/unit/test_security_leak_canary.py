import pytest

from app.core.middleware.security import LeakCanary, SecurityViolationError


def test_leak_canary_raises_when_doc_missing_tenant_and_not_global() -> None:
    docs = [{"id": "doc-1", "metadata": {"is_global": False}}]
    with pytest.raises(SecurityViolationError):
        LeakCanary.verify_isolation("tenant-a", docs)


def test_leak_canary_raises_on_cross_tenant_document() -> None:
    docs = [{"id": "doc-2", "metadata": {"tenant_id": "tenant-b"}}]
    with pytest.raises(SecurityViolationError):
        LeakCanary.verify_isolation("tenant-a", docs)
