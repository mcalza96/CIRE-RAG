from app.api.v1.schemas.retrieval_advanced import ScopeFilters, ValidateScopeRequest
from app.services.retrieval.orchestration.contract_manager import ContractManager as RetrievalContractService


def test_validate_scope_rejects_unknown_filter_keys() -> None:
    service = RetrievalContractService()
    req = ValidateScopeRequest(
        query="Que exige ISO 9001 7.5.3?",
        tenant_id="tenant-a",
        filters=ScopeFilters.model_validate({"unknown_filter": "x"}),
    )

    result = service.validate_scope(req)

    assert result.valid is False
    assert any(item.field == "filters.unknown_filter" for item in result.violations)


def test_validate_scope_rejects_tenant_override_in_metadata() -> None:
    service = RetrievalContractService()
    req = ValidateScopeRequest(
        query="Que exige ISO 9001 7.5.3?",
        tenant_id="tenant-a",
        filters=ScopeFilters.model_validate({"metadata": {"tenant_id": "tenant-b"}}),
    )

    result = service.validate_scope(req)

    assert result.valid is False
    assert any(item.field == "filters.metadata.tenant_id" for item in result.violations)


def test_validate_scope_normalizes_time_range_and_standards() -> None:
    service = RetrievalContractService()
    req = ValidateScopeRequest(
        query="Que exige ISO 9001 7.5.3?",
        tenant_id="tenant-a",
        filters=ScopeFilters.model_validate(
            {
                "source_standards": ["ISO 9001", "ISO 14001"],
                "time_range": {
                    "field": "created_at",
                    "from": "2026-01-01T00:00:00Z",
                    "to": "2026-02-01T00:00:00Z",
                },
            }
        ),
    )

    result = service.validate_scope(req)

    assert result.valid is True
    normalized_filters = result.normalized_scope["filters"]
    assert normalized_filters["source_standard"] is None
    assert normalized_filters["source_standards"] == ["ISO 9001", "ISO 14001"]
    assert normalized_filters["time_range"]["field"] == "created_at"
    assert normalized_filters["time_range"]["from"].startswith("2026-01-01T00:00:00")
    assert normalized_filters["time_range"]["to"].startswith("2026-02-01T00:00:00")
