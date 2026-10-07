"""Graph state — ids and statuses only. Documents never live in graph state:
they are in the artefact store / Postgres and are read by each node's intake step.
"""

from __future__ import annotations

from typing import Annotated, Any, TypedDict


def _merge_int_counters(
    left: dict[str, int] | None,
    right: dict[str, int] | None,
) -> dict[str, int]:
    """Additive merge for parallel-barrier counters.

    When two parallel branches each write ``{group: 1}`` to
    ``parallel_sync_counts``, LangGraph calls this reducer and the accumulated
    result is ``{group: 2}`` — the barrier reads that total to know all branches
    have arrived before routing forward.
    """
    merged = dict(left or {})
    for k, v in (right or {}).items():
        merged[k] = merged.get(k, 0) + v
    return merged


class GraphState(TypedDict, total=False):
    project_id: str
    workspace_id: str
    workflow_run_id: str           # stable ID for one BRD→delivery lifecycle; set once at graph start
    tier: str                      # global | architecture | mini
    pipeline: str
    epic_id: str | None
    initiated_by: str | None
    current_stage: str | None
    run_ids: dict[str, str]        # stage_key -> latest run id
    approved: dict[str, bool]      # stage_key -> approved?
    retries: dict[str, int]        # stage_key -> rejections so far
    epic_workspace_ids: list[str]  # Global only: Mini threads opened by fan-out
    architecture_workspace_id: str | None  # Global only: the paired Architecture Workspace
    current_epic_idx: int          # Global only: per-EPIC loop counter (feature/user-story/coverage)
    handed_off: bool               # Mini only: laptop stages in progress
    #: Barrier counter for parallel stage groups (e.g. "code-gen" -> arrivals).
    #: The additive ``_merge_int_counters`` reducer accumulates increments from
    #: each parallel branch so the barrier node can detect when all have arrived.
    parallel_sync_counts: Annotated[dict[str, int], _merge_int_counters]
    error: str | None
    finished: bool
    last_event: dict[str, Any] | None


#: How many rejections a stage may absorb before the thread stops with an error.
MAX_REJECT_RETRIES = 3

#: Stage targets executed on the developer laptop (Claude Code CLI); everything
#: else runs server-side inside the graph. This is the M5–M7 / M9–M11 split.
LAPTOP_TARGETS = frozenset({"ui", "api", "db", "tests"})

#: Global tail after fan-out: wait for all Minis, KB refresh (STAGING), promote, close.
MERGE_STAGE = "merge"
