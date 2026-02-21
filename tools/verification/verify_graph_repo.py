"""
Verification script for the Legislative Graph Repository Refactoring.
Tests mapping, interface compliance, and domain model integrity.
"""

import os
import sys
from uuid import uuid4

# Add project root to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../..")))

from app.domain.graph_schemas import (
    RegulatoryNode, 
    NodeType, 
    RegulatoryConstraint, 
    Severity
)
from app.infrastructure.supabase.mappers.graph_persistence_mapper import GraphPersistenceMapper


def test_mapper():
    print("\n[1] Testing GraphPersistenceMapper...")
    
    tenant_id = uuid4()
    node_id = uuid4()
    
    node = RegulatoryNode(
        id=node_id,
        tenant_id=tenant_id,
        node_type=NodeType.REGLA,
        title="Art. 1 - Test",
        content="Contenido de prueba",
        properties={"severity": "BLOCKING", "scope": "Universal"}
    )
    
    # Map to SQL
    sql_data = GraphPersistenceMapper.map_node_to_sql(node)
    print(f"✓ Domain to SQL mapping successful")
    assert sql_data["id"] == str(node_id)
    assert sql_data["node_type"] == "Regla"
    
    # Map back to Domain
    # We simulate supabase response (which might have strings for UUIDs)
    db_node = GraphPersistenceMapper.map_sql_to_node(sql_data)
    print(f"✓ SQL to Domain mapping successful")
    assert db_node.id == node_id
    assert db_node.node_type == NodeType.REGLA
    assert db_node.properties["severity"] == "BLOCKING"


def test_domain_constraints():
    print("\n[2] Testing RegulatoryConstraint Model...")
    
    constraint = RegulatoryConstraint(
        rule_id=uuid4(),
        content="Debe cumplir X",
        severity=Severity.BLOCKING,
        source_article="Art. 15",
        is_constitutional=True
    )
    
    print(f"✓ RegulatoryConstraint instantiation successful: {constraint.severity}")
    assert constraint.severity == Severity.BLOCKING


def main():
    print("--- Legislative Graph Repository Verification ---")
    
    try:
        test_mapper()
        test_domain_constraints()
        print("\n✓ All repository unit tests passed!")
    except Exception as e:
        print(f"\n✗ Verification failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

    print("\n--- Verification Complete ---")


if __name__ == "__main__":
    main()
