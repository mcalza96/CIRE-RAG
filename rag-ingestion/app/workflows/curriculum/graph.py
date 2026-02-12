"""
Curriculum Curation Graph
Orchestrates the Explorer -> Curator flow.
"""
from langgraph.graph import StateGraph, END
from app.workflows.curriculum.state import CurriculumState
from app.workflows.curriculum.nodes import explorer_node, curator_node

# Initialize Graph
workflow = StateGraph(CurriculumState)

# Add Nodes
workflow.add_node("explorer", explorer_node)
workflow.add_node("curator", curator_node)

# Define Edges
workflow.set_entry_point("explorer")
workflow.add_edge("explorer", "curator")
workflow.add_edge("curator", END)

# Compile
curriculum_graph = workflow.compile()
