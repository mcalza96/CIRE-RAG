"""
The Institutional Ingest Master Graph.
Orchestrates the secure ingestion of administrative documents.
Structure: Ingest -> Parse -> Embed -> Index
"""
from langgraph.graph import StateGraph, END
from app.workflows.institutional_ingest.state import InstitutionalState
from app.workflows.institutional_ingest.nodes import (
    ingest_node, 
    parse_node, 
    embed_node, 
    index_node
)

# 1. Initialize Graph
workflow = StateGraph(InstitutionalState)

# 2. Add Nodes
workflow.add_node("ingest", ingest_node)
workflow.add_node("parse", parse_node)
workflow.add_node("embed", embed_node)
workflow.add_node("index", index_node)

# 3. Define Edges (Linear Pipeline)
workflow.set_entry_point("ingest")
workflow.add_edge("ingest", "parse")
workflow.add_edge("parse", "embed")
workflow.add_edge("embed", "index")
workflow.add_edge("index", END)

# 4. Compile
institutional_ingest_graph = workflow.compile()
