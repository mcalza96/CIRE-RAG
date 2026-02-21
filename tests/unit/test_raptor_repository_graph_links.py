from __future__ import annotations

import asyncio
from uuid import uuid4

from app.domain.raptor_schemas import SummaryNode
from app.infrastructure.supabase.repositories.supabase_raptor_repository import SupabaseRaptorRepository


class _FakeQuery:
    def __init__(self, client, table_name: str):
        self._client = client
        self._table_name = table_name

    def upsert(self, payload, on_conflict=None):
        self._client.calls.append(
            {
                "table": self._table_name,
                "payload": payload,
                "on_conflict": on_conflict,
            }
        )
        return self

    async def execute(self):
        return type("Resp", (), {"data": []})()


class _FakeClient:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def table(self, name: str):
        return _FakeQuery(self, name)


def test_save_summary_node_mirrors_into_knowledge_graph() -> None:
    fake = _FakeClient()
    repo = SupabaseRaptorRepository(supabase_client=fake)

    node = SummaryNode(
        id=uuid4(),
        content="Summary content",
        title="Summary title",
        embedding=[0.1, 0.2],
        level=1,
        children_ids=[uuid4()],
        children_summary_ids=[uuid4()],
        tenant_id=uuid4(),
        source_document_id=uuid4(),
        section_ref="L1:12:9.1",
        section_node_id=uuid4(),
    )

    asyncio.run(repo.save_summary_node(node))

    tables = [call["table"] for call in fake.calls]
    assert "regulatory_nodes" in tables
    assert "knowledge_entities" in tables
    assert "knowledge_relations" in tables
