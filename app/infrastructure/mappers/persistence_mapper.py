"""
Persistence Mapper - CIRE-RAG Infrastructure Layer

Centralized mediator for Domain <-> SQL transformations.
Strictly follows naming standards: camelCase for Domain, snake_case for DB.
Ensures paridad with TypeScript PersistenceMapper.
"""

from typing import Dict, Any, List
from uuid import UUID


class PersistenceMapper:
    """
    Standardized mapper for all persistent entities in the RAG system.
    """

    @staticmethod
    def map_to_sql(domain_obj: Any, table: str) -> Dict[str, Any]:
        """
        Maps a domain object (Pydantic model, dataclass, or dict) to SQL format.
        """
        if table == "content_chunks":
            return PersistenceMapper._map_chunk_to_sql(domain_obj)
        elif table == "regulatory_nodes":
            return PersistenceMapper._map_regulatory_node_to_sql(domain_obj)
        elif table == "regulatory_edges":
            return PersistenceMapper._map_regulatory_edge_to_sql(domain_obj)

        # Fallback for generic dicts
        if isinstance(domain_obj, dict):
            return domain_obj
        if hasattr(domain_obj, "dict"):
            return domain_obj.dict()
        return dict(domain_obj)

    @staticmethod
    def _get_val(obj: Any, keys: List[str], default: Any = None) -> Any:
        """Helper to get value from object or dict using multiple possible keys."""
        is_dict = isinstance(obj, dict)
        for key in keys:
            if is_dict:
                if key in obj:
                    return obj[key]
            else:
                if hasattr(obj, key):
                    return getattr(obj, key)
        return default

    @staticmethod
    def _map_chunk_to_sql(chunk: Any) -> Dict[str, Any]:
        """Maps Content Chunk to 'content_chunks' table schema."""
        # Handles both camelCase (Domain) and snake_case (Infrastructure)
        # Supports both Pydantic objects and Dicts

        # Helper aliases
        get = PersistenceMapper._get_val

        # IDs safely converted to string only if they exist
        source_id = get(chunk, ["sourceId", "source_id"])
        chunk_id = get(chunk, ["id", "chunkId", "chunk_id"])
        institution_id = get(chunk, ["institutionId", "institution_id"]) or get(
            chunk, ["metadata"], {}
        ).get("institution_id")
        collection_id = get(chunk, ["collectionId", "collection_id"]) or get(
            chunk, ["metadata"], {}
        ).get("collection_id")
        # CIRE-ORCH expects metadata -> row -> metadata structure for advanced filtering.
        # However, Supabase RPC (hybrid_search) expects filters at the TOP level of metadata.
        # We duplicate critical fields to satisfy both.
        raw_metadata = get(chunk, ["metadata"], {})

        nested_metadata = {"row": {"metadata": raw_metadata, "source_layer": "content_chunk"}}

        # Merge top-level filterable fields into the final metadata object
        final_metadata = nested_metadata.copy()
        for key in [
            "source_standard",
            "clause_id",
            "scope",
            "chunk_role",
            "doc_section_type",
            "is_toc",
            "is_frontmatter",
            "is_normative_body",
            "retrieval_eligible",
            "structure_eligible",
        ]:
            if key in raw_metadata:
                final_metadata[key] = raw_metadata[key]

        result = {
            "source_id": str(source_id) if source_id else None,
            "content": get(chunk, ["content"], ""),
            "embedding": get(chunk, ["embedding"], []),
            "chunk_index": get(chunk, ["chunkIndex", "chunk_index"], 0),
            "file_page_number": get(chunk, ["filePageNumber", "file_page_number"], 1),
            "metadata": final_metadata,
            "institution_id": str(institution_id) if institution_id else None,
            "collection_id": str(collection_id) if collection_id else None,
            "is_global": get(chunk, ["isGlobal", "is_global"], False),
            "semantic_context": get(chunk, ["semanticContext", "semantic_context"], None),
        }

        if chunk_id:
            result["id"] = str(chunk_id)

        return result

    @staticmethod
    def _map_regulatory_node_to_sql(node: Any) -> Dict[str, Any]:
        """Maps RegulatoryNode to 'regulatory_nodes' table."""
        return {
            "id": str(getattr(node, "id")),
            "tenant_id": str(getattr(node, "tenant_id") or getattr(node, "tenantId")),
            "node_type": getattr(node, "node_type").value
            if hasattr(getattr(node, "node_type"), "value")
            else getattr(node, "node_type"),
            "title": getattr(node, "title"),
            "content": getattr(node, "content"),
            "properties": getattr(node, "properties", {}),
            "embedding": getattr(node, "embedding", None),
        }

    @staticmethod
    def _map_regulatory_edge_to_sql(edge: Any) -> Dict[str, Any]:
        """Maps RegulatoryEdge to 'regulatory_edges' table."""
        return {
            "source_id": str(getattr(edge, "source_id")),
            "target_id": str(getattr(edge, "target_id")),
            "edge_type": getattr(edge, "edge_type").value
            if hasattr(getattr(edge, "edge_type"), "value")
            else getattr(edge, "edge_type"),
            "weight": getattr(edge, "weight", 1.0),
            "metadata": getattr(edge, "metadata", {}),
        }

    @staticmethod
    def map_from_sql(data: Dict[str, Any], entity_type: str) -> Any:
        """
        Future implementation for mapping SQL records back to Domain Entities.
        Currently relying on Pydantic's populate_by_name for most flows.
        """
        return data
