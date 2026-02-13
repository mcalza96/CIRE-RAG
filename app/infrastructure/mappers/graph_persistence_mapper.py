"""
Graph Persistence Mapper - CIRE-RAG Infrastructure Layer

Mediator between Domain Models and Supabase/SQL persistence logic.
Strictly follows naming standards: camelCase for Domain, snake_case for DB.
"""

from typing import Dict, Any
from app.domain.graph_schemas import RegulatoryNode, RegulatoryEdge


class GraphPersistenceMapper:
    """
    Handles mapping for regulatory nodes and edges.
    """

    @staticmethod
    def map_node_to_sql(node: RegulatoryNode) -> Dict[str, Any]:
        """
        Maps RegulatoryNode domain model to supabase 'regulatory_nodes' table format.
        """
        from app.infrastructure.mappers.persistence_mapper import PersistenceMapper
        return PersistenceMapper.map_to_sql(node, "regulatory_nodes")

    @staticmethod
    def map_edge_to_sql(edge: RegulatoryEdge) -> Dict[str, Any]:
        """
        Maps RegulatoryEdge domain model to supabase 'regulatory_edges' table format.
        """
        from app.infrastructure.mappers.persistence_mapper import PersistenceMapper
        return PersistenceMapper.map_to_sql(edge, "regulatory_edges")

    @staticmethod
    def map_sql_to_node(data: Dict[str, Any]) -> RegulatoryNode:
        """
        Maps supabase 'regulatory_nodes' record to RegulatoryNode domain model.
        """
        return RegulatoryNode(
            id=data["id"],
            tenant_id=data["tenant_id"],
            node_type=data["node_type"],
            title=data["title"],
            content=data["content"],
            embedding=data.get("embedding"),
            properties=data.get("properties") or {},
        )

    @staticmethod
    def map_sql_to_edge(data: Dict[str, Any]) -> RegulatoryEdge:
        """
        Maps supabase 'regulatory_edges' record to RegulatoryEdge domain model.
        """
        return RegulatoryEdge(
            source_id=data["source_id"],
            target_id=data["target_id"],
            edge_type=data["edge_type"],
            weight=data.get("weight", 1.0),
            metadata=data.get("metadata") or {},
        )
