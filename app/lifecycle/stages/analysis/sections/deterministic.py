"""Deterministic (graph-derived) sections — how it touches the system + where changes are needed.

Beyond the graph topology, these builders derive an *edge-aware* impact note (why a node is in the
set, from the typed edge that reached it) so the deliverable is informative even with the LLM off.
"""

from __future__ import annotations

from app.lifecycle.stages.analysis.schema import AffectedNode, ChangeLocation, ImpactCitation, TouchPoint

# System-boundary node kinds = "how it touches the existing system".
_TOUCH_KINDS = {"SYS", "INT", "ROLE"}
# Implementable node kinds = "where changes are needed" (excludes SYS - systems are dependencies, not direct changes).
# Updated Aug 17, 2026: SYS removed to prevent systems from appearing in "where changes are needed"
_CHANGE_KINDS = {"SCR", "CMP", "API", "INT"}

# What each typed edge implies for impact — turns a bare edge label into a "why it's affected" phrase.
_EDGE_IMPLICATION = {
    "FEEDS_INTO": "receives data from the change — data contract may shift",
    "CALLS": "calls the changed node — call/interface contract may change",
    "TRIGGERS": "triggered by the change — orchestration/timing may change",
    "VALIDATES": "enforces a validation tied to the change",
    "GOVERNED_BY": "governed by a rule the change touches",
    "ROLE_IN": "actor participating in the changed flow",
    "SCREEN_OF": "screen surfacing the changed data",
    "IMPLEMENTS": "implements the changed behaviour",
    "DEPENDS_ON": "depends on the changed node",
    "PRODUCES": "produces output affected by the change",
    "OVERRIDES": "overrides behaviour the change touches",
    "REFERENCES": "references the changed node",
}

_MAX_EXCERPT = 240


def card_excerpt(text: str | None) -> str | None:
    """First sentence / ~240 chars of a card body — the grounded 'what this node is' summary."""
    if not text:
        return None
    clean = " ".join(text.split())
    if not clean:
        return None
    if len(clean) <= _MAX_EXCERPT:
        return clean
    cut = clean[:_MAX_EXCERPT]
    dot = cut.rfind(". ")
    return (cut[: dot + 1] if dot >= 80 else cut.rstrip() + "…")


def impact_note(via: list[str], kind: str = "") -> str:
    """Why a node is in the impact set — edge-aware, not a constant string.

    Directly-matched nodes (no incoming edge) → "directly matched by the requirement".
    Otherwise phrase each reaching edge as its impact implication, e.g.
    ``reached via FEEDS_INTO — receives data from the change; data contract may shift``.
    """
    edges = list(dict.fromkeys(v for v in via if v))  # dedupe, keep order
    if not edges:
        return "directly matched by the requirement"
    implications = list(dict.fromkeys(_EDGE_IMPLICATION.get(e, f"linked via {e}") for e in edges))
    return f"reached via {', '.join(edges)} — {'; '.join(implications)}"


def build_touch_points(matched: list[ImpactCitation], affected: list[AffectedNode]) -> list[TouchPoint]:
    """Boundary SYS/INT/ROLE nodes the change intersects (Q1: how it touches the existing system)."""
    out: dict[str, TouchPoint] = {}
    for c in matched:
        if c.kind in _TOUCH_KINDS:
            out[c.id] = TouchPoint(
                id=c.id, label=c.label, kind=c.kind, relation="directly matched", note=c.summary
            )
    for n in affected:
        if n.kind in _TOUCH_KINDS and n.id not in out:
            out[n.id] = TouchPoint(
                id=n.id, label=n.label, kind=n.kind, relation=", ".join(n.via), note=n.summary
            )
    return list(out.values())


def build_change_locations(affected: list[AffectedNode]) -> list[ChangeLocation]:
    """Concrete SCR/CMP/API/INT/SYS nodes + loci where changes land (Q4: where to change)."""
    return [
        ChangeLocation(
            kind=n.kind, id=n.id, label=n.label, locus=n.source_locus, note=impact_note(n.via, n.kind)
        )
        for n in affected
        if n.kind in _CHANGE_KINDS
    ]
