"""Back-compat re-export — impact traversal moved to ``match/walk.py`` (docs/22 refactor)."""

from __future__ import annotations

from app.lifecycle.stages.analysis.match.walk import (
    TraversalPolicy,
    compute_affected,
    compute_downstream,
    policy_for_route,
)

__all__ = ["TraversalPolicy", "compute_affected", "compute_downstream", "policy_for_route"]
