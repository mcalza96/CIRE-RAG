"""Tests for Late Grounding architecture (Retrieval as a Pointer).

Validates that _graph_hop() resolves graph entities to real content chunks,
enriches metadata with graph provenance, and falls back gracefully for
ungrounded entities.
"""

from __future__ import annotations

from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.infrastructure.supabase.repositories.atomic_engine import AtomicRetrievalEngine


class _DummyEmbeddingService:
    async def embed_texts(self, texts, task="retrieval.query"):
        return [[0.1] * 4 for _ in texts]


def _make_engine(
    *,
    graph_repo: Any = None,
    retrieval_repo: Any = None,
) -> AtomicRetrievalEngine:
    engine = AtomicRetrievalEngine(
        embedding_service=cast(Any, _DummyEmbeddingService()),
    )
    if graph_repo is not None:
        engine._graph_repo = graph_repo
    if retrieval_repo is not None:
        engine._retrieval_repository = retrieval_repo
    return engine


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

ENTITY_A_ID = str(uuid4())
ENTITY_B_ID = str(uuid4())
CHUNK_1_ID = str(uuid4())
CHUNK_2_ID = str(uuid4())

NAV_ROWS = [
    {
        "entity_id": ENTITY_A_ID,
        "entity_name": "Requisito de Calibracion",
        "entity_type": "OBLIGATION",
        "entity_description": "La organizacion debe calibrar equipos de medicion",
        "similarity": 0.85,
        "hop_depth": 0,
        "path_ids": [],
    },
    {
        "entity_id": ENTITY_B_ID,
        "entity_name": "Reglamento Academico",
        "entity_type": "DOCUMENT",
        "entity_description": "Normativa interna de gestion academica",
        "similarity": 0.72,
        "hop_depth": 1,
        "path_ids": [ENTITY_A_ID],
    },
]

PROVENANCE_LINKS_FULL = [
    {"chunk_id": CHUNK_1_ID, "node_id": ENTITY_A_ID},
    {"chunk_id": CHUNK_2_ID, "node_id": ENTITY_B_ID},
]

PROVENANCE_LINKS_PARTIAL = [
    {"chunk_id": CHUNK_1_ID, "node_id": ENTITY_A_ID},
    # ENTITY_B has no chunk lineage
]

RAW_CHUNKS = [
    {
        "id": CHUNK_1_ID,
        "source": CHUNK_1_ID,
        "content": "Articulo 7.1.5 - La organizacion debe determinar y proporcionar los recursos de seguimiento y medicion.",
        "metadata": {"source_standard": "ISO 9001", "clause_id": "7.1.5", "id": CHUNK_1_ID},
        "similarity": 0.0,
        "score": 0.0,
        "source_layer": "graph_grounded",
        "source_type": "content_chunk",
        "source_id": "src-1",
    },
    {
        "id": CHUNK_2_ID,
        "source": CHUNK_2_ID,
        "content": "Articulo 4 - El alumno que sea sorprendido cometiendo plagio sera sancionado.",
        "metadata": {"documento": "Reglamento 2025", "pagina": 12, "id": CHUNK_2_ID},
        "similarity": 0.0,
        "score": 0.0,
        "source_layer": "graph_grounded",
        "source_type": "content_chunk",
        "source_id": "src-2",
    },
]


def _mock_graph_repo(nav_rows, provenance_links):
    repo = MagicMock()
    repo.search_multi_hop_context = AsyncMock(return_value=nav_rows)
    repo.resolve_node_to_chunk_ids = AsyncMock(return_value=provenance_links)
    return repo


def _mock_retrieval_repo(chunks):
    repo = MagicMock()
    repo.retrieve_hybrid_optimized = AsyncMock(return_value=[])
    repo.fetch_chunks_by_ids = AsyncMock(return_value=[dict(c) for c in chunks])
    return repo


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_graph_hop_returns_grounded_chunks(monkeypatch) -> None:
    """When all entities have chunk provenance, _graph_hop returns real content chunks."""
    monkeypatch.setattr("app.infrastructure.settings.settings.ATOMIC_ENABLE_GRAPH_HOP", True)
    monkeypatch.setattr("app.infrastructure.settings.settings.ATOMIC_MATCH_THRESHOLD", 0.3)

    graph_repo = _mock_graph_repo(NAV_ROWS, PROVENANCE_LINKS_FULL)
    retrieval_repo = _mock_retrieval_repo(RAW_CHUNKS)
    engine = _make_engine(graph_repo=graph_repo, retrieval_repo=retrieval_repo)

    tenant_id = str(uuid4())
    results = await engine._graph_hop(
        query_vector=[0.1] * 4,
        scope_context={"tenant_id": tenant_id},
        fetch_k=10,
    )

    # All results should be real content chunks
    grounded = [r for r in results if r["source_type"] == "content_chunk"]
    ungrounded = [r for r in results if r["source_type"] == "knowledge_entity_ungrounded"]

    assert len(grounded) == 2
    assert len(ungrounded) == 0

    # Verify content is REAL document text, not synthesized graph text
    for result in grounded:
        assert "[anchor]" not in result["content"]
        assert "[hop-" not in result["content"]
        assert result["source_layer"] == "graph_grounded"

    # Verify metadata enrichment
    for result in grounded:
        meta = result["metadata"]
        assert meta["retrieved_via"] == "graph"
        assert "graph_reasoning" in meta
        assert "graph_entity_ids" in meta
        assert isinstance(meta["graph_entity_ids"], list)

    # Verify similarity is propagated from graph navigation
    chunk_1 = next(r for r in grounded if r["id"] == CHUNK_1_ID)
    assert chunk_1["similarity"] == 0.85  # From ENTITY_A


@pytest.mark.asyncio
async def test_graph_hop_fallback_ungrounded(monkeypatch) -> None:
    """When entities have no chunk provenance, they fall back to synthetic text."""
    monkeypatch.setattr("app.infrastructure.settings.settings.ATOMIC_ENABLE_GRAPH_HOP", True)
    monkeypatch.setattr("app.infrastructure.settings.settings.ATOMIC_MATCH_THRESHOLD", 0.3)

    graph_repo = _mock_graph_repo(NAV_ROWS, PROVENANCE_LINKS_PARTIAL)
    retrieval_repo = _mock_retrieval_repo([RAW_CHUNKS[0]])  # Only chunk for entity A
    engine = _make_engine(graph_repo=graph_repo, retrieval_repo=retrieval_repo)

    tenant_id = str(uuid4())
    results = await engine._graph_hop(
        query_vector=[0.1] * 4,
        scope_context={"tenant_id": tenant_id},
        fetch_k=10,
    )

    grounded = [r for r in results if r["source_type"] == "content_chunk"]
    ungrounded = [r for r in results if r["source_type"] == "knowledge_entity_ungrounded"]

    assert len(grounded) == 1
    assert len(ungrounded) == 1

    # Grounded chunk should have real content
    assert "Articulo 7.1.5" in grounded[0]["content"]
    assert grounded[0]["metadata"]["retrieved_via"] == "graph"

    # Ungrounded entity should have synthetic text
    assert "[hop-1]" in ungrounded[0]["content"]
    assert "Reglamento Academico" in ungrounded[0]["content"]
    assert ungrounded[0]["id"].startswith("graph:")
    assert ungrounded[0]["metadata"]["grounded"] is False
    assert ungrounded[0]["source_layer"] == "graph"


@pytest.mark.asyncio
async def test_graph_hop_all_ungrounded(monkeypatch) -> None:
    """When NO entities have provenance, all return as ungrounded synthetic text."""
    monkeypatch.setattr("app.infrastructure.settings.settings.ATOMIC_ENABLE_GRAPH_HOP", True)
    monkeypatch.setattr("app.infrastructure.settings.settings.ATOMIC_MATCH_THRESHOLD", 0.3)

    graph_repo = _mock_graph_repo(NAV_ROWS, [])  # Empty provenance
    retrieval_repo = _mock_retrieval_repo([])
    engine = _make_engine(graph_repo=graph_repo, retrieval_repo=retrieval_repo)

    tenant_id = str(uuid4())
    results = await engine._graph_hop(
        query_vector=[0.1] * 4,
        scope_context={"tenant_id": tenant_id},
        fetch_k=10,
    )

    assert len(results) == 2
    for result in results:
        assert result["source_type"] == "knowledge_entity_ungrounded"
        assert result["metadata"]["grounded"] is False
        assert result["metadata"]["retrieved_via"] == "graph"

    # fetch_chunks_by_ids should NOT have been called (no chunk_ids to hydrate)
    retrieval_repo.fetch_chunks_by_ids.assert_not_called()


@pytest.mark.asyncio
async def test_graph_hop_disabled_returns_empty(monkeypatch) -> None:
    """When ATOMIC_ENABLE_GRAPH_HOP is False, returns empty."""
    monkeypatch.setattr("app.infrastructure.settings.settings.ATOMIC_ENABLE_GRAPH_HOP", False)

    engine = _make_engine()
    results = await engine._graph_hop(
        query_vector=[0.1] * 4,
        scope_context={"tenant_id": str(uuid4())},
        fetch_k=10,
    )
    assert results == []


@pytest.mark.asyncio
async def test_graph_hop_no_tenant_returns_empty(monkeypatch) -> None:
    """When no tenant_id in scope_context, returns empty."""
    monkeypatch.setattr("app.infrastructure.settings.settings.ATOMIC_ENABLE_GRAPH_HOP", True)

    engine = _make_engine()
    results = await engine._graph_hop(
        query_vector=[0.1] * 4,
        scope_context={},
        fetch_k=10,
    )
    assert results == []


@pytest.mark.asyncio
async def test_retrieve_context_merges_homogeneous_chunks(monkeypatch) -> None:
    """Full retrieve_context flow: graph grounded chunks merge with hybrid RPC chunks."""
    monkeypatch.setattr("app.infrastructure.settings.settings.ATOMIC_ENABLE_GRAPH_HOP", True)
    monkeypatch.setattr("app.infrastructure.settings.settings.ATOMIC_USE_HYBRID_RPC", True)
    monkeypatch.setattr("app.infrastructure.settings.settings.ATOMIC_MATCH_THRESHOLD", 0.3)

    hybrid_chunk_id = str(uuid4())
    hybrid_results = [
        {
            "id": hybrid_chunk_id,
            "content": "Chunk from hybrid search.",
            "metadata": {"source_standard": "ISO 9001"},
            "similarity": 0.90,
            "score": 0.90,
            "source_layer": "hybrid",
            "source_type": "content_chunk",
            "source_id": "src-hybrid",
        },
    ]

    graph_repo = _mock_graph_repo(NAV_ROWS[:1], PROVENANCE_LINKS_FULL[:1])
    retrieval_repo = _mock_retrieval_repo([RAW_CHUNKS[0]])
    retrieval_repo.retrieve_hybrid_optimized = AsyncMock(return_value=hybrid_results)

    engine = _make_engine(graph_repo=graph_repo, retrieval_repo=retrieval_repo)

    tenant_id = str(uuid4())
    results = await engine.retrieve_context(
        query="Que requisitos de calibracion exige ISO 9001?",
        scope_context={"tenant_id": tenant_id},
        k=10,
        fetch_k=20,
    )

    # All returned items should be content_chunks (homogeneous)
    content_chunk_types = {r["source_type"] for r in results if r.get("source_type") == "content_chunk"}
    assert "content_chunk" in content_chunk_types

    # Check that graph_grounded chunks exist
    graph_grounded = [r for r in results if r.get("source_layer") == "graph_grounded"]
    hybrid_items = [r for r in results if r.get("source_layer") == "hybrid"]

    assert len(graph_grounded) >= 1
    assert len(hybrid_items) >= 1

    # Verify graph_grounded chunks have real content, not synthetic
    for item in graph_grounded:
        assert "[anchor]" not in item["content"]
        assert item["metadata"].get("retrieved_via") == "graph"

@pytest.mark.asyncio
async def test_pipeline_raptor_hydrates_leaf_chunks() -> None:
    """When RAPTOR returns summaries, pipeline resolves them to leaf chunks via Late Grounding."""
    from app.workflows.retrieval.contract_manager import ContractManager
    
    # Mock retrieval tools
    mock_tools = MagicMock()
    
    # 1. retrieve_summaries returns summary nodes
    summary_id = str(uuid4())
    mock_tools.retrieve_summaries = AsyncMock(return_value=[
        {"id": summary_id, "title": "Cluster 1", "content": "Summary content"}
    ])
    
    # 2. repository.resolve_summaries_to_chunk_ids returns chunk IDs
    mock_repo = AsyncMock()
    mock_repo.resolve_summaries_to_chunk_ids.return_value = [CHUNK_1_ID]
    mock_tools.repository = mock_repo
    
    # 3. atomic_repo.fetch_chunks_by_ids returns raw content chunks
    mock_atomic_repo = AsyncMock()
    mock_atomic_repo.fetch_chunks_by_ids.return_value = [RAW_CHUNKS[0]]
    
    # Setup broker mock
    mock_broker = MagicMock()
    mock_atomic_engine = MagicMock()
    mock_atomic_engine._retrieval_repository = mock_atomic_repo
    mock_broker.atomic_engine = mock_atomic_engine
    mock_tools.broker = mock_broker
    
    # Init manager
    manager = ContractManager(retrieval_tools=mock_tools)
    
    warnings: list[str] = []
    items = await manager._pipeline_raptor(
        query="Test query",
        tenant_id=str(uuid4()),
        collection_id=None,
        k=5,
        trace_warnings=warnings
    )
    
    assert len(items) == 1
    assert items[0].metadata["id"] == CHUNK_1_ID
    assert "Articulo 7.1.5" in items[0].content
    assert items[0].metadata["fusion_source"] == "raptor"
    assert items[0].metadata["retrieved_via"] == "raptor"
    assert items[0].metadata["raptor_reasoning"] == "RAPTOR Cluster Expansion"
    
    # Verify calls
    mock_tools.retrieve_summaries.assert_called_once()
    mock_repo.resolve_summaries_to_chunk_ids.assert_called_once_with([summary_id])
    mock_atomic_repo.fetch_chunks_by_ids.assert_called_once_with([CHUNK_1_ID])

@pytest.mark.asyncio
async def test_pipeline_raptor_empty_leaves() -> None:
    """When RAPTOR summaries have no leaves, pipeline safely returns empty."""
    from app.workflows.retrieval.contract_manager import ContractManager
    
    mock_tools = MagicMock()
    summary_id = str(uuid4())
    mock_tools.retrieve_summaries = AsyncMock(return_value=[
        {"id": summary_id, "title": "Cluster 1", "content": "Summary content"}
    ])
    
    mock_repo = AsyncMock()
    mock_repo.resolve_summaries_to_chunk_ids.return_value = [] # No leaves resolved
    mock_tools.repository = mock_repo
    
    manager = ContractManager(retrieval_tools=mock_tools)
    
    warnings: list[str] = []
    items = await manager._pipeline_raptor(
        query="Test query",
        tenant_id=str(uuid4()),
        collection_id=None,
        k=5,
        trace_warnings=warnings
    )
    
    assert len(items) == 0

