import asyncio
from typing import Any

from app.api.v1.schemas.retrieval_advanced import HybridRetrievalRequest
from app.application.services.retrieval_contract_service import RetrievalContractService
from app.infrastructure.container import CognitiveContainer


class _FakeRetrievalTools:
    async def retrieve(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return {
            "items": [
                {
                    "id": "row-1",
                    "content": "text",
                    "similarity": 0.88,
                    "score": 0.88,
                    "source_layer": "hybrid",
                    "source_type": "content_chunk",
                    "metadata": {"tenant_id": "tenant-a", "source_id": "s1"},
                }
            ],
            "trace": {
                "filters_applied": {"tenant_id": "tenant-a"},
                "engine_mode": "atomic",
                "planner_used": False,
                "planner_multihop": False,
                "fallback_used": False,
                "rpc_compat_mode": "without_hnsw_ef_search",
                "timings_ms": {"total": 12.3},
                "warnings": ["hybrid_rpc_signature_mismatch_hnsw_ef_search"],
                "warning_codes": ["HYBRID_RPC_SIGNATURE_MISMATCH_HNSW"],
            },
        }


class _FakeContainer:
    def __init__(self) -> None:
        self.retrieval_tools = _FakeRetrievalTools()


def test_run_hybrid_propagates_engine_trace_warnings(monkeypatch) -> None:
    service = RetrievalContractService()
    service._retrieval_tools = _FakeRetrievalTools()
    
    response = asyncio.run(
        service.run_hybrid(
            HybridRetrievalRequest(
                query="iso 9001 control documentado",
                tenant_id="tenant-a",
                k=4,
                fetch_k=12,
            )
        )
    )
    assert "hybrid_rpc_signature_mismatch_hnsw_ef_search" in response.trace.warnings
    assert "HYBRID_RPC_SIGNATURE_MISMATCH_HNSW" in response.trace.warning_codes
    assert response.trace.rpc_compat_mode == "without_hnsw_ef_search"
