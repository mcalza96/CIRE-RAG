"""Curriculum workflow runner.

Keeps a `curriculum_graph.invoke(...)` interface for compatibility with workers
and tests, while executing the flow as a direct linear pipeline:
explorer -> curator.
"""

from typing import Dict, Any, cast

from app.workflows.curriculum.state import CurriculumState
from app.workflows.curriculum.nodes import explorer_node, curator_node


class CurriculumWorkflow:
    async def invoke(self, initial_state: CurriculumState) -> Dict[str, Any]:
        state: Dict[str, Any] = dict(initial_state)

        explorer_result = await explorer_node(cast(CurriculumState, state))
        state.update(explorer_result)
        if state.get("error"):
            return state

        curator_result = await curator_node(cast(CurriculumState, state))
        state.update(curator_result)
        return state


curriculum_graph = CurriculumWorkflow()
