from __future__ import annotations

import asyncio
from typing import Any
from uuid import uuid4

from app.infrastructure.settings import settings
from app.domain.raptor_schemas import BaseChunk
from app.services.knowledge.raptor_processor import RaptorProcessor


class _FakeRepo:
    def __init__(self) -> None:
        self.saved_nodes: list[Any] = []

    async def save_summary_node(self, node: Any) -> None:
        self.saved_nodes.append(node)

    async def save_summary_nodes(self, nodes: list[Any]) -> None:
        self.saved_nodes.extend(nodes)


class _FakeEmbeddingService:
    async def embed_texts(self, texts, mode=None, task=None, provider=None):
        return [[0.1, 0.2, 0.3] for _ in texts]


class _FakeSummarizer:
    async def asummarize(self, cluster_texts):
        return ("Summary", " | ".join(cluster_texts))


class _ConvergingCluster:
    def cluster(self, chunk_ids, embeddings):
        return type("Result", (), {"num_clusters": 1, "cluster_contents": {0: chunk_ids}})()


def test_raptor_structural_bootstrap_groups_by_section(monkeypatch) -> None:
    monkeypatch.setattr(settings, "RAPTOR_STRUCTURAL_MODE_ENABLED", True)

    repo = _FakeRepo()
    processor = RaptorProcessor(
        repository=repo,
        embedding_service=_FakeEmbeddingService(),
        summarization_service=_FakeSummarizer(),
        clustering_service=_ConvergingCluster(),
        max_depth=3,
    )

    tenant_id = uuid4()
    section_node_a = uuid4()
    section_node_b = uuid4()
    base_chunks = [
        BaseChunk(
            id=uuid4(),
            content="9.1 one",
            embedding=[0.0, 0.1],
            tenant_id=tenant_id,
            section_ref="L1:12:9.1",
            section_node_id=section_node_a,
        ),
        BaseChunk(
            id=uuid4(),
            content="9.1 two",
            embedding=[0.1, 0.2],
            tenant_id=tenant_id,
            section_ref="L1:12:9.1",
            section_node_id=section_node_a,
        ),
        BaseChunk(
            id=uuid4(),
            content="9.2 one",
            embedding=[0.2, 0.3],
            tenant_id=tenant_id,
            section_ref="L1:20:9.2",
            section_node_id=section_node_b,
        ),
    ]

    result = asyncio.run(processor.build_tree(base_chunks=base_chunks, tenant_id=tenant_id))

    assert result.total_nodes_created == 2
    assert len(repo.saved_nodes) == 2
    refs = {node.section_ref for node in repo.saved_nodes}
    assert refs == {"L1:12:9.1", "L1:20:9.2"}
