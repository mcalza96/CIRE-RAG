import logging
import math
from typing import Any, Optional
from uuid import UUID

from app.infrastructure.supabase.client import get_async_supabase_client
from app.services.embedding_service import JinaEmbeddingService
from app.services.knowledge.graph_extractor import ChunkGraphExtraction, Entity, Relation

logger = logging.getLogger(__name__)


class SupabaseGraphRepository:
    """Persistencia del subgrafo semantico en tablas knowledge_* de Supabase."""

    def __init__(
        self, supabase_client=None, embedding_service: Optional[JinaEmbeddingService] = None
    ):
        self._client = supabase_client
        self._embedding_service = embedding_service or JinaEmbeddingService.get_instance()

    async def _get_client(self):
        if self._client is None:
            self._client = await get_async_supabase_client()
        return self._client

    @staticmethod
    def _norm(text: str) -> str:
        return (text or "").strip().casefold()

    @staticmethod
    def _merge_description(existing: Optional[str], incoming: Optional[str]) -> str:
        existing_clean = (existing or "").strip()
        incoming_clean = (incoming or "").strip()

        if not existing_clean:
            return incoming_clean
        if not incoming_clean:
            return existing_clean
        if incoming_clean in existing_clean:
            return existing_clean
        return f"{existing_clean}\n\n{incoming_clean}"

    @staticmethod
    def _to_float_list(vector_value: Any) -> Optional[list[float]]:
        if vector_value is None:
            return None

        if isinstance(vector_value, list):
            try:
                return [float(v) for v in vector_value]
            except Exception:
                return None

        if isinstance(vector_value, str):
            raw = vector_value.strip()
            if raw.startswith("[") and raw.endswith("]"):
                raw = raw[1:-1]
            if not raw:
                return None
            try:
                return [float(part.strip()) for part in raw.split(",") if part.strip()]
            except Exception:
                return None

        return None

    @staticmethod
    def _cosine_similarity(vec_a: Optional[list[float]], vec_b: Optional[list[float]]) -> float:
        if not vec_a or not vec_b or len(vec_a) != len(vec_b):
            return -1.0

        dot = sum(a * b for a, b in zip(vec_a, vec_b))
        norm_a = math.sqrt(sum(a * a for a in vec_a))
        norm_b = math.sqrt(sum(b * b for b in vec_b))

        if norm_a == 0.0 or norm_b == 0.0:
            return -1.0
        return dot / (norm_a * norm_b)

    async def _bulk_insert_with_fallback(
        self, table: str, rows: list[dict], select_cols: str = "*"
    ) -> list[dict]:
        if not rows:
            return []

        client = await self._get_client()
        try:
            query = client.table(table).insert(rows)
            if hasattr(query, "select"):
                response = await query.select(select_cols).execute()
                return response.data or []
            await query.execute()
            return [row for row in rows if isinstance(row, dict)]
        except Exception as batch_error:
            logger.warning("Fallo insercion batch en %s, fallback por fila: %s", table, batch_error)
            inserted: list[dict] = []
            for row in rows:
                try:
                    query = client.table(table).insert(row)
                    if hasattr(query, "select"):
                        response = await query.select(select_cols).execute()
                        if response.data:
                            inserted.extend(response.data)
                    else:
                        await query.execute()
                        if isinstance(row, dict):
                            inserted.append(row)
                except Exception as row_error:
                    row_preview = {
                        "tenant_id": row.get("tenant_id"),
                        "source_entity_id": row.get("source_entity_id"),
                        "target_entity_id": row.get("target_entity_id"),
                        "relation_type": row.get("relation_type"),
                    }
                    logger.error(
                        "Fallo insercion fila en %s: %s | row_preview=%s",
                        table,
                        row_error,
                        row_preview,
                    )
            return inserted

    async def _bulk_upsert_with_fallback(
        self,
        table: str,
        rows: list[dict],
        on_conflict: str,
        select_cols: str = "*",
    ) -> list[dict]:
        if not rows:
            return []

        client = await self._get_client()
        try:
            query = client.table(table).upsert(rows, on_conflict=on_conflict)
            if hasattr(query, "select"):
                response = await query.select(select_cols).execute()
                data = response.data if isinstance(response.data, list) else []
                return data

            # Compatibility path for SDK variants where upsert builder has no .select()
            await query.execute()
            return [row for row in rows if isinstance(row, dict)]
        except Exception as batch_error:
            logger.warning("Fallo upsert batch en %s, fallback por fila: %s", table, batch_error)
            upserted: list[dict] = []
            for row in rows:
                try:
                    query = client.table(table).upsert(row, on_conflict=on_conflict)
                    if hasattr(query, "select"):
                        response = await query.select(select_cols).execute()
                        if isinstance(response.data, list):
                            upserted.extend(response.data)
                        elif isinstance(response.data, dict):
                            upserted.append(response.data)
                    else:
                        await query.execute()
                        if isinstance(row, dict):
                            upserted.append(row)
                except Exception as row_error:
                    logger.error("Fallo upsert fila en %s: %s | row=%s", table, row_error, row)
            return upserted

    async def _ensure_entity_embeddings(
        self,
        extraction: ChunkGraphExtraction,
        entity_embeddings: Optional[dict[str, list[float]]],
        embedding_mode: Optional[str] = None,
        embedding_provider: Optional[str] = None,
    ) -> dict[str, list[float]]:
        by_name = entity_embeddings or {}

        missing_entities: list[Entity] = []
        missing_texts: list[str] = []
        for entity in extraction.entities:
            norm_name = self._norm(entity.name)
            if norm_name in by_name:
                continue
            missing_entities.append(entity)
            missing_texts.append(f"{entity.name}. {entity.description}")

        if not missing_texts:
            return by_name

        try:
            vectors = await self._embedding_service.embed_texts(
                missing_texts,
                mode=embedding_mode,
                provider=embedding_provider,
            )
            for entity, vector in zip(missing_entities, vectors):
                if vector:
                    by_name[self._norm(entity.name)] = vector
        except Exception as exc:
            logger.warning("No se pudieron generar embeddings para entidades faltantes: %s", exc)

        return by_name

    @staticmethod
    def _dedupe_entities(entities: list[Entity]) -> list[Entity]:
        merged: dict[str, Entity] = {}
        for entity in entities:
            key = entity.name.strip().casefold()
            if key not in merged:
                merged[key] = entity
                continue

            previous = merged[key]
            merged[key] = Entity(
                name=previous.name,
                type=previous.type or entity.type,
                description=(
                    previous.description
                    if entity.description in previous.description
                    else f"{previous.description}\n\n{entity.description}"
                ),
            )

        return list(merged.values())

    @staticmethod
    def _normalize_relation_type(value: str) -> str:
        return value.strip().replace(" ", "_").replace("-", "_").upper()

    async def _upsert_subgraph_atomic_rpc(
        self,
        extraction: ChunkGraphExtraction,
        tenant_id: UUID,
        chunk_id: Optional[UUID],
        entity_embeddings: dict[str, list[float]],
    ) -> Optional[dict[str, Any]]:
        client = await self._get_client()

        entities_payload: list[dict[str, Any]] = []
        for entity in extraction.entities:
            norm_name = self._norm(entity.name)
            entities_payload.append(
                {
                    "name": entity.name,
                    "type": entity.type,
                    "description": entity.description,
                    "embedding": entity_embeddings.get(norm_name),
                }
            )

        relations_payload: list[dict[str, Any]] = []
        for relation in extraction.relations:
            relations_payload.append(
                {
                    "source": relation.source,
                    "target": relation.target,
                    "relation_type": self._normalize_relation_type(relation.relation_type),
                    "description": relation.description,
                    "weight": max(1, min(10, int(relation.weight))),
                }
            )

        rpc_params = {
            "p_tenant_id": str(tenant_id),
            "p_chunk_id": str(chunk_id) if chunk_id else None,
            "p_entities": entities_payload,
            "p_relations": relations_payload,
        }

        try:
            response = await client.rpc("upsert_knowledge_subgraph_atomic", rpc_params).execute()
            rows = response.data or []
            if not rows:
                return None

            row = rows[0] if isinstance(rows, list) else rows
            if not isinstance(row, dict):
                return None

            return {
                "nodes_upserted": int(row.get("nodes_upserted", 0)),
                "edges_upserted": int(row.get("edges_upserted", 0)),
                "links_upserted": int(row.get("links_upserted", 0)),
                "entities_extracted": int(row.get("entities_extracted", len(extraction.entities))),
                "relations_extracted": int(
                    row.get("relations_extracted", len(extraction.relations))
                ),
                "entities_inserted": int(row.get("entities_inserted", 0)),
                "entities_merged": int(row.get("entities_merged", 0)),
                "relations_inserted": int(row.get("relations_inserted", 0)),
                "relations_merged": int(row.get("relations_merged", 0)),
                "errors": row.get("errors", []),
            }
        except Exception as exc:
            logger.warning("Atomic subgraph RPC failed, fallback to client-side upsert: %s", exc)
            return None

    async def upsert_knowledge_subgraph(
        self,
        extraction: ChunkGraphExtraction,
        chunk_id: Optional[UUID],
        tenant_id: UUID,
        entity_embeddings: Optional[dict[str, list[float]]] = None,
        embedding_mode: Optional[str] = None,
        embedding_provider: Optional[str] = None,
    ) -> dict[str, Any]:
        stats = {
            "nodes_upserted": 0,
            "edges_upserted": 0,
            "links_upserted": 0,
            "entities_extracted": len(extraction.entities),
            "relations_extracted": len(extraction.relations),
            "entities_inserted": 0,
            "entities_merged": 0,
            "relations_inserted": 0,
            "relations_merged": 0,
            "errors": [],
        }

        if extraction.is_empty():
            return stats

        entities = self._dedupe_entities(extraction.entities)
        normalized_relations = [
            Relation(
                source=relation.source,
                target=relation.target,
                relation_type=self._normalize_relation_type(relation.relation_type),
                description=relation.description,
                weight=max(1, min(10, int(relation.weight))),
            )
            for relation in extraction.relations
        ]
        normalized_extraction = ChunkGraphExtraction(
            entities=entities, relations=normalized_relations
        )

        entity_embeddings = await self._ensure_entity_embeddings(
            normalized_extraction,
            entity_embeddings,
            embedding_mode=embedding_mode,
            embedding_provider=embedding_provider,
        )

        rpc_stats = await self._upsert_subgraph_atomic_rpc(
            extraction=normalized_extraction,
            tenant_id=tenant_id,
            chunk_id=chunk_id,
            entity_embeddings=entity_embeddings,
        )
        if rpc_stats is not None:
            return rpc_stats

        client = await self._get_client()
        tenant_str = str(tenant_id)
        chunk_str = str(chunk_id) if chunk_id else None

        entities = normalized_extraction.entities
        entity_embeddings = await self._ensure_entity_embeddings(
            ChunkGraphExtraction(entities=entities, relations=normalized_extraction.relations),
            entity_embeddings,
            embedding_mode=embedding_mode,
            embedding_provider=embedding_provider,
        )

        existing_response = (
            await client.table("knowledge_entities")
            .select("id,name,type,description,embedding")
            .eq("tenant_id", tenant_str)
            .execute()
        )
        existing_rows = existing_response.data or []

        existing_by_name = {self._norm(row.get("name", "")): row for row in existing_rows}
        existing_with_vectors = [
            {
                **row,
                "_vector": self._to_float_list(row.get("embedding")),
                "_norm_name": self._norm(row.get("name", "")),
            }
            for row in existing_rows
        ]

        update_rows: list[dict] = []
        insert_rows: list[dict] = []
        entity_id_by_name: dict[str, str] = {}

        for entity in entities:
            norm_name = self._norm(entity.name)
            entity_vector = entity_embeddings.get(norm_name)
            matched = existing_by_name.get(norm_name)

            if matched is None and entity_vector:
                best_similarity = -1.0
                best_match = None
                for candidate in existing_with_vectors:
                    similarity = self._cosine_similarity(entity_vector, candidate.get("_vector"))
                    if similarity > best_similarity:
                        best_similarity = similarity
                        best_match = candidate
                if best_match is not None and best_similarity >= 0.95:
                    matched = best_match

            try:
                if matched is not None:
                    merged_description = self._merge_description(
                        matched.get("description"), entity.description
                    )
                    row = {
                        "id": matched["id"],
                        "tenant_id": tenant_str,
                        "name": matched.get("name") or entity.name,
                        "type": entity.type or matched.get("type"),
                        "description": merged_description,
                    }
                    if entity_vector:
                        row["embedding"] = entity_vector
                    update_rows.append(row)
                    entity_id_by_name[norm_name] = str(matched["id"])
                else:
                    row = {
                        "tenant_id": tenant_str,
                        "name": entity.name,
                        "type": entity.type,
                        "description": entity.description,
                        "metadata": {},
                    }
                    if entity_vector:
                        row["embedding"] = entity_vector
                    insert_rows.append(row)
            except Exception as exc:
                logger.error("Error preparando entidad '%s': %s", entity.name, exc)
                stats["errors"].append(f"entity_prepare:{entity.name}:{exc}")

        updated_entities = await self._bulk_upsert_with_fallback(
            table="knowledge_entities",
            rows=update_rows,
            on_conflict="id",
            select_cols="id,name",
        )
        inserted_entities = await self._bulk_insert_with_fallback(
            table="knowledge_entities",
            rows=insert_rows,
            select_cols="id,name",
        )

        for row in updated_entities + inserted_entities:
            entity_id_by_name[self._norm(row.get("name", ""))] = str(row.get("id"))

        stats["nodes_upserted"] = len(updated_entities) + len(inserted_entities)
        stats["entities_merged"] = len(updated_entities)
        stats["entities_inserted"] = len(inserted_entities)

        unresolved_entities = [
            entity for entity in entities if self._norm(entity.name) not in entity_id_by_name
        ]
        if unresolved_entities:
            unresolved_names = [entity.name for entity in unresolved_entities]
            try:
                resolve_resp = (
                    await client.table("knowledge_entities")
                    .select("id,name")
                    .eq("tenant_id", tenant_str)
                    .in_("name", unresolved_names)
                    .execute()
                )
                for row in resolve_resp.data or []:
                    entity_id_by_name[self._norm(row.get("name", ""))] = str(row.get("id"))
            except Exception as resolve_err:
                logger.warning(
                    "No se pudieron resolver entidades faltantes post-upsert: %s", resolve_err
                )

        relation_candidates: list[Relation] = []
        seen_relations: set[tuple[str, str, str]] = set()

        for relation in normalized_extraction.relations:
            source_name = self._norm(relation.source)
            target_name = self._norm(relation.target)
            relation_type = (
                relation.relation_type.strip().upper().replace(" ", "_").replace("-", "_")
            )

            source_id = entity_id_by_name.get(source_name)
            target_id = entity_id_by_name.get(target_name)
            if not source_id or not target_id:
                stats["errors"].append(
                    f"relation_missing_entity:{relation.source}->{relation.target}:{relation.relation_type}"
                )
                continue

            key = (source_id, target_id, relation_type)
            if key in seen_relations:
                continue
            seen_relations.add(key)

            relation_candidates.append(
                Relation(
                    source=source_id,
                    target=target_id,
                    relation_type=relation_type,
                    description=relation.description,
                    weight=max(1, min(10, int(relation.weight))),
                )
            )

        update_rel_rows: list[dict] = []
        insert_rel_rows: list[dict] = []

        if relation_candidates:
            source_ids = list({rel.source for rel in relation_candidates})
            target_ids = list({rel.target for rel in relation_candidates})
            relation_types = list({rel.relation_type for rel in relation_candidates})

            existing_rel_response = (
                await client.table("knowledge_relations")
                .select("id,source_entity_id,target_entity_id,relation_type,description,weight")
                .eq("tenant_id", tenant_str)
                .in_("source_entity_id", source_ids)
                .in_("target_entity_id", target_ids)
                .in_("relation_type", relation_types)
                .execute()
            )

            existing_rel_rows_raw = existing_rel_response.data or []
            existing_rel_rows: list[dict[str, Any]] = [
                row for row in existing_rel_rows_raw if isinstance(row, dict)
            ]
            existing_rel_map = {
                (
                    str(row.get("source_entity_id")),
                    str(row.get("target_entity_id")),
                    str(row.get("relation_type", "")).upper(),
                ): row
                for row in existing_rel_rows
            }

            for relation in relation_candidates:
                key = (relation.source, relation.target, relation.relation_type)
                existing_rel = existing_rel_map.get(key)

                if existing_rel:
                    current_weight = float(existing_rel.get("weight") or 0)
                    merged_description = self._merge_description(
                        existing_rel.get("description"), relation.description
                    )
                    update_rel_rows.append(
                        {
                            "id": existing_rel["id"],
                            "tenant_id": tenant_str,
                            "source_entity_id": relation.source,
                            "target_entity_id": relation.target,
                            "relation_type": relation.relation_type,
                            "description": merged_description,
                            "weight": current_weight + 1,
                        }
                    )
                else:
                    insert_rel_rows.append(
                        {
                            "tenant_id": tenant_str,
                            "source_entity_id": relation.source,
                            "target_entity_id": relation.target,
                            "relation_type": relation.relation_type,
                            "description": relation.description,
                            "weight": relation.weight,
                            "metadata": {},
                        }
                    )

        updated_relations = await self._bulk_upsert_with_fallback(
            table="knowledge_relations",
            rows=update_rel_rows,
            on_conflict="id",
            select_cols="id",
        )
        inserted_relations = await self._bulk_insert_with_fallback(
            table="knowledge_relations",
            rows=insert_rel_rows,
            select_cols="id",
        )
        stats["edges_upserted"] = len(updated_relations) + len(inserted_relations)
        stats["relations_merged"] = len(updated_relations)
        stats["relations_inserted"] = len(inserted_relations)

        if chunk_str:
            provenance_rows = [
                {
                    "tenant_id": tenant_str,
                    "entity_id": entity_id,
                    "chunk_id": chunk_str,
                }
                for entity_id in set(entity_id_by_name.values())
            ]

            linked = await self._bulk_upsert_with_fallback(
                table="knowledge_node_provenance",
                rows=provenance_rows,
                on_conflict="tenant_id,entity_id,chunk_id",
                select_cols="id",
            )
            stats["links_upserted"] = len(linked)

        return stats

    @staticmethod
    def _legacy_to_chunk_extraction(extraction: Any) -> ChunkGraphExtraction:
        nodes = getattr(extraction, "nodes", []) or []
        edges = getattr(extraction, "edges", []) or []

        entities: list[Entity] = []
        relations: list[Relation] = []
        temp_to_name: dict[str, str] = {}

        for node in nodes:
            name = getattr(node, "name", None)
            node_type = str(getattr(node, "node_type", "ENTITY"))
            description = getattr(node, "content", None) or ""
            temp_id = getattr(node, "temp_id", None)
            if not name:
                continue

            entities.append(Entity(name=name, type=node_type, description=description))
            if temp_id:
                temp_to_name[str(temp_id)] = str(name)

        for edge in edges:
            source_temp = str(getattr(edge, "source_temp_id", ""))
            target_temp = str(getattr(edge, "target_temp_id", ""))
            source_name = temp_to_name.get(source_temp)
            target_name = temp_to_name.get(target_temp)
            if not source_name or not target_name:
                continue

            raw_weight = getattr(edge, "weight", 1)
            if isinstance(raw_weight, float) and raw_weight <= 1.0:
                weight = max(1, min(10, int(round(raw_weight * 10))))
            else:
                weight = max(1, min(10, int(raw_weight)))

            relations.append(
                Relation(
                    source=source_name,
                    target=target_name,
                    relation_type=str(getattr(edge, "edge_type", "RELATED_TO")),
                    description=str(getattr(edge, "description", "")),
                    weight=weight,
                )
            )

        return ChunkGraphExtraction(entities=entities, relations=relations)

    async def persist_chunk_extraction(
        self,
        extraction: Any,
        tenant_id: UUID,
        chunk_id: UUID,
        generate_embeddings: bool = True,
        embedding_mode: Optional[str] = None,
        embedding_provider: Optional[str] = None,
    ) -> dict[str, Any]:
        if isinstance(extraction, ChunkGraphExtraction):
            graph_extraction = extraction
        else:
            graph_extraction = self._legacy_to_chunk_extraction(extraction)

        entity_embeddings = None
        if generate_embeddings and graph_extraction.entities:
            try:
                texts = [
                    f"{entity.name}. {entity.description}" for entity in graph_extraction.entities
                ]
                vectors = await self._embedding_service.embed_texts(
                    texts,
                    mode=embedding_mode,
                    provider=embedding_provider,
                )
                entity_embeddings = {
                    self._norm(entity.name): vector
                    for entity, vector in zip(graph_extraction.entities, vectors)
                    if vector
                }
            except Exception as exc:
                logger.warning("Fallo generando embeddings en persist_chunk_extraction: %s", exc)

        return await self.upsert_knowledge_subgraph(
            extraction=graph_extraction,
            chunk_id=chunk_id,
            tenant_id=tenant_id,
            entity_embeddings=entity_embeddings,
            embedding_mode=embedding_mode,
            embedding_provider=embedding_provider,
        )
