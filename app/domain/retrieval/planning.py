from typing import Any
from app.domain.schemas.query_plan import PlannedSubQuery, QueryPlan

def coerce_query_plan(raw_plan: Any) -> QueryPlan | None:
    """Safely coerces a raw dictionary or object into a QueryPlan Pydantic model.
    Used to normalize LLM outputs or legacy plan formats.
    """
    if isinstance(raw_plan, QueryPlan):
        return raw_plan
    if not isinstance(raw_plan, dict):
        return None

    raw_items = raw_plan.get("sub_queries")
    if not isinstance(raw_items, list):
        return None

    subqueries: list[PlannedSubQuery] = []
    for idx, item in enumerate(raw_items, start=1):
        if not isinstance(item, dict):
            continue
        query = str(item.get("query") or "").strip()
        if not query:
            continue
        
        raw_id = item.get("id")
        if isinstance(raw_id, int):
            sq_id = raw_id
        elif isinstance(raw_id, str) and raw_id.strip().isdigit():
            sq_id = int(raw_id.strip())
        else:
            sq_id = idx
            
        dep = item.get("dependency_id")
        dep_id = dep if isinstance(dep, int) else None
        
        rels = item.get("target_relations")
        nodes = item.get("target_node_types")
        
        target_relations = (
            [str(x).strip() for x in rels if str(x).strip()] if isinstance(rels, list) else None
        )
        target_node_types = (
            [str(x).strip() for x in nodes if str(x).strip()]
            if isinstance(nodes, list)
            else None
        )
        
        subqueries.append(
            PlannedSubQuery(
                id=sq_id,
                query=query,
                dependency_id=dep_id,
                target_relations=target_relations or None,
                target_node_types=target_node_types or None,
                is_deep=bool(item.get("is_deep", False)),
            )
        )

    if not subqueries:
        return None

    mode = str(raw_plan.get("execution_mode") or "parallel").strip().lower()
    execution_mode = "sequential" if mode == "sequential" else "parallel"
    
    return QueryPlan(
        is_multihop=bool(raw_plan.get("is_multihop", len(subqueries) > 1)),
        execution_mode=execution_mode,
        sub_queries=subqueries,
        fallback_reason=(str(raw_plan.get("fallback_reason") or "").strip() or None),
    )
