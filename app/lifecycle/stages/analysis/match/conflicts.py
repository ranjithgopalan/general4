"""Conflict detection with existing nodes (deterministic; docs/22 §4).

Two deterministic detectors for v1:
- **evidence contradiction** — a matched/affected node whose evidence re_anchor verdict is non-verbatim
  / disputed (WRONG_CONTEXT / FABRICATED / NOT_VERBATIM).
- **sibling-workspace overlap** — the impact set intersects another OPEN workspace's impact set
  (surfaced EARLY at ANALYSIS, 04 §8d). Detect + report in v1; serialized promotion/rebase is v2.

Rule-vs-code **drift** is surfaced by the ReAct agent (grounded, whitelist-filtered) — not here.
All conflicts resolve the same way: DISPUTED → human adjudication.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from app.lifecycle.stages.analysis.schema import Conflict
from app.services.graph_provider import GraphProvider
from app.utils.logging import log

_CONTRADICTION_VERDICTS = {"WRONG_CONTEXT", "FABRICATED", "NOT_VERBATIM"}


async def evidence_contradictions(graph: GraphProvider, ids: list[str], *, cap: int = 8) -> list[Conflict]:
    """Flag nodes whose cited evidence failed verbatim re_anchor (a grounded contradiction signal)."""
    conflicts: list[Conflict] = []
    for nid in ids[:cap]:
        detail = await graph.node_detail(nid)
        if detail is None:
            continue
        bad = [e for e in detail.evidence if (e.anchor_verdict or "").upper() in _CONTRADICTION_VERDICTS]
        if bad:
            conflicts.append(
                Conflict(
                    kind="contradiction",
                    subject_ids=[nid],
                    detail=f"{detail.node.label} [{nid}] has disputed/non-verbatim evidence ({bad[0].anchor_verdict}).",
                    severity="high",
                )
            )
    return conflicts


async def sibling_overlap(
    list_open_workspaces: Callable[[], Awaitable[list[Any]]],
    get_analysis: Callable[[str], Awaitable[Any]],
    *,
    this_workspace_id: str,
    this_ids: set[str],
    this_requirement: str = "",
) -> list[Conflict]:
    """Flag other OPEN workspaces whose impact set intersects this one (early conflict, 04 §8d).

    Same-requirement siblings are skipped (a re-run/duplicate of the SAME change is not a conflict),
    and remaining conflicts are collapsed to one per distinct requirement — so N duplicate workspaces
    don't surface as N false "in-flight" conflicts.
    """
    conflicts: list[Conflict] = []
    try:
        others = await list_open_workspaces()
    except Exception as exc:  # noqa: BLE001 — overlap check is best-effort enrichment
        log.warning(f"[impact] sibling-overlap scan skipped: {exc}")
        return []
    this_req = (this_requirement or "").strip().lower()
    seen_reqs: set[str] = set()
    for ws in others:
        wid = getattr(ws, "workspace_id", None)
        state = getattr(getattr(ws, "state", None), "value", "")
        if not wid or wid == this_workspace_id or state == "CLOSED":
            continue
        other = await get_analysis(wid)
        if other is None:
            continue
        other_req = (getattr(other, "requirement", "") or "").strip().lower()
        # Same requirement = the same change re-run/duplicated → not a real conflict.
        if this_req and other_req == this_req:
            continue
        # Collapse multiple open workspaces that share one requirement to a single conflict entry.
        if other_req and other_req in seen_reqs:
            continue
        other_ids = {c.id for c in other.matched} | {n.id for n in other.affected}
        overlap = sorted(this_ids & other_ids)
        if overlap:
            if other_req:
                seen_reqs.add(other_req)
            conflicts.append(
                Conflict(
                    kind="sibling-overlap",
                    subject_ids=overlap,
                    detail=f"Impact set overlaps open workspace {wid} on {len(overlap)} node(s): {', '.join(overlap[:5])}.",
                    severity="medium",
                )
            )
    return conflicts
