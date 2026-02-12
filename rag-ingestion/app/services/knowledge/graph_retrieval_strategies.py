import asyncio
import json
import math
import re
from typing import Any, Optional
from uuid import UUID

import structlog
from langchain_core.language_models.chat_models import BaseChatModel

from app.core.llm import get_llm
from app.infrastructure.supabase.client import get_async_supabase_client
from app.services.embedding_service import JinaEmbeddingService

logger = structlog.get_logger(__name__)


def _to_float_list(value: Any) -> Optional[list[float]]:
    if value is None:
        return None
    if isinstance(value, list):
        try:
            return [float(v) for v in value]
        except Exception:
            return None
    if isinstance(value, str):
        raw = value.strip()
        if raw.startswith("[") and raw.endswith("]"):
            raw = raw[1:-1]
        if not raw:
            return None
        try:
            return [float(token.strip()) for token in raw.split(",") if token.strip()]
        except Exception:
            return None
    return None


def _cosine_similarity(vec_a: Optional[list[float]], vec_b: Optional[list[float]]) -> float:
    if not vec_a or not vec_b or len(vec_a) != len(vec_b):
        return -1.0
    dot = sum(a * b for a, b in zip(vec_a, vec_b))
    norm_a = math.sqrt(sum(a * a for a in vec_a))
    norm_b = math.sqrt(sum(b * b for b in vec_b))
    if norm_a == 0.0 or norm_b == 0.0:
        return -1.0
    return dot / (norm_a * norm_b)


class LocalGraphSearch:
    """Local graph retrieval with entity anchoring and 1-hop traversal."""

    def __init__(
        self,
        supabase_client=None,
        llm_provider: Optional[BaseChatModel] = None,
        embedding_service: Optional[JinaEmbeddingService] = None,
        anchor_similarity_threshold: float = 0.72,
    ):
        self._supabase = supabase_client
        self._llm = llm_provider or get_llm(temperature=0.0, capability="FORENSIC")
        self._embedding = embedding_service or JinaEmbeddingService.get_instance()
        self._anchor_similarity_threshold = anchor_similarity_threshold

    async def _get_client(self):
        if self._supabase is None:
            self._supabase = await get_async_supabase_client()
        return self._supabase

    async def extract_entities_from_query(self, query: str) -> list[str]:
        if not query.strip():
            return []

        system_prompt = (
            "Extract potential named entities and domain anchors from a user query for graph retrieval. "
            "Return strict JSON: {\"entities\": [\"...\"]}. "
            "Do not include explanations."
        )

        try:
            response = await self._llm.ainvoke(
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": query},
                ]
            )
            content = str(response.content).strip()
            parsed = json.loads(content)
            entities = parsed.get("entities", []) if isinstance(parsed, dict) else []
            clean = [str(item).strip() for item in entities if str(item).strip()]
            if clean:
                return clean[:8]
        except Exception:
            logger.debug("llm_entity_extraction_fallback", query=query)

        fallback = re.findall(r"[A-Z][\w\-]+(?:\s+[A-Z][\w\-]+)*", query)
        if fallback:
            return list(dict.fromkeys([f.strip() for f in fallback if f.strip()]))[:8]
        return [query.strip()[:80]]

    async def _match_exact_anchors(self, tenant_id: UUID, entity_name: str) -> list[dict]:
        client = await self._get_client()
        try:
            response = await client.table("knowledge_entities").select(
                "id,name,type,description,embedding"
            ).eq("tenant_id", str(tenant_id)).ilike("name", entity_name).limit(6).execute()
            return response.data or []
        except Exception as exc:
            logger.warning("local_exact_anchor_match_failed", entity=entity_name, error=str(exc))
            return []

    async def _match_vector_anchors(self, tenant_id: UUID, entities: list[str]) -> list[dict]:
        if not entities:
            return []

        client = await self._get_client()
        try:
            vectors = await self._embedding.embed_texts(entities, task="retrieval.query")
            if not vectors:
                return []

            merged: dict[str, dict] = {}
            for vector in vectors:
                rpc_res = await client.rpc(
                    "match_knowledge_entities",
                    {
                        "query_embedding": vector,
                        "p_tenant_id": str(tenant_id),
                        "match_count": 8,
                        "match_threshold": self._anchor_similarity_threshold,
                    },
                ).execute()
                for row in rpc_res.data or []:
                    if row.get("id"):
                        merged[str(row.get("id"))] = row

            if merged:
                return list(merged.values())[:12]
        except Exception as exc:
            logger.debug("local_rpc_anchor_match_fallback", error=str(exc))

        try:
            entity_rows_resp = await client.table("knowledge_entities").select(
                "id,name,type,description,embedding"
            ).eq("tenant_id", str(tenant_id)).execute()
            entity_rows = entity_rows_resp.data or []
            if not entity_rows:
                return []

            vectors = await self._embedding.embed_texts(entities, task="retrieval.query")
            if not vectors:
                return []

            scored: list[tuple[float, dict]] = []
            for row in entity_rows:
                row_vec = _to_float_list(row.get("embedding"))
                if not row_vec:
                    continue
                best_score = max(_cosine_similarity(query_vec, row_vec) for query_vec in vectors)
                if best_score >= self._anchor_similarity_threshold:
                    scored.append((best_score, row))

            scored.sort(key=lambda item: item[0], reverse=True)
            dedup: dict[str, dict] = {}
            for _, row in scored[:12]:
                dedup[str(row.get("id"))] = row
            return list(dedup.values())
        except Exception as exc:
            logger.warning("local_vector_anchor_match_failed", error=str(exc))
            return []

    async def find_anchor_nodes(self, tenant_id: UUID, query: str) -> list[dict]:
        candidates = await self.extract_entities_from_query(query)
        if not candidates:
            return []

        exact_batches = await asyncio.gather(*[self._match_exact_anchors(tenant_id, name) for name in candidates])
        exact_rows = [row for batch in exact_batches for row in batch]

        dedup: dict[str, dict] = {str(row.get("id")): row for row in exact_rows if row.get("id")}
        if dedup:
            return list(dedup.values())[:8]

        vector_rows = await self._match_vector_anchors(tenant_id, candidates)
        for row in vector_rows:
            if row.get("id"):
                dedup[str(row.get("id"))] = row
        return list(dedup.values())[:8]

    async def _fetch_one_hop(self, tenant_id: UUID, anchor_ids: list[str]) -> tuple[list[dict], list[dict]]:
        if not anchor_ids:
            return [], []

        client = await self._get_client()
        anchors_csv = ",".join(anchor_ids)
        relation_filter = f"source_entity_id.in.({anchors_csv}),target_entity_id.in.({anchors_csv})"

        rel_resp = await client.table("knowledge_relations").select(
            "id,source_entity_id,target_entity_id,relation_type,description,weight"
        ).eq("tenant_id", str(tenant_id)).or_(relation_filter).execute()
        relations = rel_resp.data or []

        neighbor_ids = set(anchor_ids)
        for relation in relations:
            source = relation.get("source_entity_id")
            target = relation.get("target_entity_id")
            if source:
                neighbor_ids.add(str(source))
            if target:
                neighbor_ids.add(str(target))

        neighbor_list = list(neighbor_ids)
        if not neighbor_list:
            return relations, []

        ent_resp = await client.table("knowledge_entities").select(
            "id,name,type,description"
        ).eq("tenant_id", str(tenant_id)).in_("id", neighbor_list).execute()
        neighbors = ent_resp.data or []
        return relations, neighbors

    async def search(self, query: str, tenant_id: UUID) -> dict[str, Any]:
        anchors = await self.find_anchor_nodes(tenant_id, query)
        if not anchors:
            return {"context": "", "citations": [], "anchors": [], "found": False}

        anchor_ids = [str(item["id"]) for item in anchors if item.get("id")]
        relations, neighbors = await self._fetch_one_hop(tenant_id, anchor_ids)

        neighbor_by_id = {str(item.get("id")): item for item in neighbors if item.get("id")}
        lines: list[str] = ["Local Graph Context"]

        lines.append("Anchors:")
        for anchor in anchors:
            lines.append(f"- {anchor.get('name', 'Unknown')}: {anchor.get('description', '')}")

        if relations:
            lines.append("Relations (1-hop):")
            for rel in relations[:40]:
                source = neighbor_by_id.get(str(rel.get("source_entity_id")), {}).get("name", rel.get("source_entity_id"))
                target = neighbor_by_id.get(str(rel.get("target_entity_id")), {}).get("name", rel.get("target_entity_id"))
                relation_text = rel.get("description") or ""
                lines.append(
                    f"- {source} --{rel.get('relation_type', 'RELATED_TO')}--> {target}. {relation_text}".strip()
                )

        non_anchor_neighbors = [n for n in neighbors if str(n.get("id")) not in set(anchor_ids)]
        if non_anchor_neighbors:
            lines.append("Neighbor entities:")
            for neighbor in non_anchor_neighbors[:40]:
                lines.append(f"- {neighbor.get('name', 'Unknown')}")

        citations = list(dict.fromkeys([str(item.get("id")) for item in anchors + neighbors if item.get("id")]))
        return {
            "context": "\n".join(lines),
            "citations": citations,
            "anchors": anchor_ids,
            "found": True,
        }


class GlobalGraphSearch:
    """Global graph retrieval using community summary semantic search."""

    def __init__(
        self,
        supabase_client=None,
        embedding_service: Optional[JinaEmbeddingService] = None,
    ):
        self._supabase = supabase_client
        self._embedding = embedding_service or JinaEmbeddingService.get_instance()

    async def _get_client(self):
        if self._supabase is None:
            self._supabase = await get_async_supabase_client()
        return self._supabase

    async def search(self, query: str, tenant_id: UUID, top_k: int = 5) -> dict[str, Any]:
        if not query.strip():
            return {"context": "", "community_ids": [], "citations": []}

        query_vectors = await self._embedding.embed_texts([query], task="retrieval.query")
        if not query_vectors:
            return {"context": "", "community_ids": [], "citations": []}
        query_vector = query_vectors[0]

        client = await self._get_client()
        try:
            rpc_response = await client.rpc(
                "match_knowledge_communities",
                {
                    "query_embedding": query_vector,
                    "p_tenant_id": str(tenant_id),
                    "p_level": 0,
                    "match_count": top_k,
                    "match_threshold": 0.25,
                },
            ).execute()
            rpc_rows = rpc_response.data or []
            if rpc_rows:
                return self._build_global_payload(rpc_rows)
        except Exception as exc:
            logger.debug("global_rpc_search_fallback", error=str(exc))

        response = await client.table("knowledge_communities").select(
            "id,community_id,summary,members,embedding"
        ).eq("tenant_id", str(tenant_id)).eq("level", 0).execute()
        rows = response.data or []
        if not rows:
            return {"context": "", "community_ids": [], "citations": []}

        scored_rows: list[tuple[float, dict]] = []
        for row in rows:
            community_vec = _to_float_list(row.get("embedding"))
            if not community_vec:
                continue
            score = _cosine_similarity(query_vector, community_vec)
            scored_rows.append((score, row))

        scored_rows.sort(key=lambda item: item[0], reverse=True)
        winners = [row for _, row in scored_rows[:top_k]]
        if not winners:
            return {"context": "", "community_ids": [], "citations": []}

        context_lines = ["Global Graph Context"]
        citations: list[str] = []
        community_ids: list[int] = []

        for item in winners:
            summary = str(item.get("summary") or "").strip()
            community_id = int(item.get("community_id") or -1)
            community_ids.append(community_id)
            if summary:
                context_lines.append(f"[Community {community_id}] {summary}")

            row_id = item.get("id")
            if row_id:
                citations.append(str(row_id))

            members = item.get("members") or []
            if isinstance(members, list):
                citations.extend(str(member) for member in members if member)

        return {
            "context": "\n\n".join(context_lines),
            "community_ids": community_ids,
            "citations": list(dict.fromkeys(citations)),
        }

    @staticmethod
    def _build_global_payload(rows: list[dict]) -> dict[str, Any]:
        context_lines = ["Global Graph Context"]
        citations: list[str] = []
        community_ids: list[int] = []

        for item in rows:
            summary = str(item.get("summary") or "").strip()
            community_id = int(item.get("community_id") or -1)
            community_ids.append(community_id)
            if summary:
                context_lines.append(f"[Community {community_id}] {summary}")

            row_id = item.get("id")
            if row_id:
                citations.append(str(row_id))

            members = item.get("members") or []
            if isinstance(members, list):
                citations.extend(str(member) for member in members if member)

        return {
            "context": "\n\n".join(context_lines),
            "community_ids": community_ids,
            "citations": list(dict.fromkeys(citations)),
        }
