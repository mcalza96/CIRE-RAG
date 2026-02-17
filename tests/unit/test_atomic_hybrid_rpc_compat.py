import asyncio

from app.services.retrieval.atomic_engine import AtomicRetrievalEngine


def test_hybrid_rpc_signature_mismatch_detection() -> None:
    err = RuntimeError(
        "PGRST202: Could not find function retrieve_hybrid_optimized with hnsw_ef_search"
    )
    assert AtomicRetrievalEngine._is_hnsw_signature_mismatch(err) is True
    assert AtomicRetrievalEngine._is_hnsw_signature_mismatch(RuntimeError("other")) is False


def test_retrieve_context_retries_without_hnsw_param_on_signature_mismatch(monkeypatch) -> None:
    engine = AtomicRetrievalEngine()

    async def _embed_query(_query: str) -> list[float]:
        return [0.11, 0.22]

    async def _resolve_source_ids(_scope_context: dict) -> list[str]:
        return ["source-1"]

    calls: list[bool] = []

    async def _search_hybrid_rpc(**kwargs):  # type: ignore[no-untyped-def]
        include_hnsw = bool(kwargs.get("include_hnsw_ef_search", True))
        calls.append(include_hnsw)
        if include_hnsw:
            raise RuntimeError(
                "PGRST202: retrieve_hybrid_optimized missing signature for hnsw_ef_search"
            )
        return [
            {
                "id": "row-1",
                "content": "chunk",
                "metadata": {"source_id": "source-1"},
                "similarity": 0.9,
                "score": 0.9,
                "source_layer": "hybrid",
                "source_type": "content_chunk",
                "source_id": "source-1",
            }
        ]

    async def _search_vectors(**_kwargs):  # type: ignore[no-untyped-def]
        return []

    async def _graph_hop(**_kwargs):  # type: ignore[no-untyped-def]
        return []

    monkeypatch.setattr(engine, "_embed_query", _embed_query)
    monkeypatch.setattr(engine, "_resolve_source_ids", _resolve_source_ids)
    monkeypatch.setattr(engine, "_search_hybrid_rpc", _search_hybrid_rpc)
    monkeypatch.setattr(engine, "_search_vectors", _search_vectors)
    monkeypatch.setattr(engine, "_graph_hop", _graph_hop)

    rows = asyncio.run(
        engine.retrieve_context(
            query="q",
            scope_context={"tenant_id": "tenant-1"},
            k=5,
            fetch_k=20,
        )
    )

    assert len(rows) == 1
    assert calls == [True, False]
    assert engine.last_trace.get("hybrid_rpc_used") is True
    assert engine.last_trace.get("hybrid_rpc_compat_mode") == "without_hnsw_ef_search"
    assert engine.last_trace.get("rpc_compat_mode") == "without_hnsw_ef_search"
    warnings = engine.last_trace.get("warnings")
    assert isinstance(warnings, list)
    assert any("hybrid_rpc_signature_mismatch_hnsw_ef_search" in str(item) for item in warnings)
    warning_codes = engine.last_trace.get("warning_codes")
    assert isinstance(warning_codes, list)
    assert "HYBRID_RPC_SIGNATURE_MISMATCH_HNSW" in warning_codes
