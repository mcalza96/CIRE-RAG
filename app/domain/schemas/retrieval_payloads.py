from __future__ import annotations

from typing import Any, TypeAlias

# NOTE:
# We intentionally keep these as type aliases to avoid TypedDict/list invariance friction
# across the existing codebase (which mutates rows, merges lists, and passes through
# multiple layers).

RetrievalRow: TypeAlias = dict[str, Any]
GroundedContext: TypeAlias = dict[str, Any]
