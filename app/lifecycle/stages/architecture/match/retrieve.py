"""C1 — independent architecture retrieval (docs/24 §C1).

The Architecture stage used to be a pure pass-through enricher of upstream citations: its KB set was
the union of Stories/BRD/FSD/Analysis IDs. In the design-first order (Architecture precedes Stories,
BRD folded into the FSD), the FSD cites few SYS/CMP/INT/API/ENT cards, so the SRD came out sparse
("No system nodes" stub, hardcoded sequence participants).

This module gives the stage its OWN grounded retrieval: when the prior-artifact ID set is thin, seed
the graph from the requirement text and expand along structural edges to pull in the architecture
families (System / Integration / Component / ApiOp / Entity / Workflow / Sequence). Every ID returned
is a real graph node, so it stays inside the grounding whitelist (re_anchor still gates every claim).

Deterministic graph walk (no LLM). Read-only over the graph.
"""

from __future__ import annotations

from app.lifecycle.stages.analysis.schema import ImpactCitation
from app.services.graph_provider import GraphProvider

# Structural edges that carry architecture meaning (CLAUDE.md §Stage-3 edge labels).
ARCH_EDGE_LABELS = frozenset(
    {"DEPENDS_ON", "CALLS", "FEEDS_INTO", "PRODUCES", "IMPLEMENTS", "SCREEN_OF"}
)
# Node families the SRD is built from (Architect persona includeKinds, CLAUDE.md §6).
ARCH_KINDS = frozenset(
    {"System", "Integration", "Component", "ApiOp", "Entity", "Workflow", "Sequence"}
)

# Cap graph-retrieved additions so a broad requirement can't flood the grounding whitelist.
_MAX_RETRIEVED = 40


async def retrieve_architecture_ids(
    graph: GraphProvider | None,
    requirement: str,
    *,
    existing_ids: set[str],
    category: str | None = None,
    limit: int = _MAX_RETRIEVED,
) -> list[ImpactCitation]:
    """Seed→expand the graph for architecture nodes not already cited by upstream artifacts.

    Supplements the (design-first: thin) upstream citation set with the stage's own grounded walk.
    Returns extra ``ImpactCitation``s (never duplicates of ``existing_ids``), ordered by graph
    relevance score and capped at ``limit``. Empty when the graph is unavailable or nothing seeds.
    """
    if graph is None or not requirement.strip():
        return []

    seed_res = await graph.seeds(requirement, category=category)
    seed_ids = [s.id for s in seed_res.seeds]
    if not seed_ids:
        return []

    expanded = await graph.expand_seeds(seed_ids, depth=2, edge_labels=set(ARCH_EDGE_LABELS))

    out: list[ImpactCitation] = []
    seen = set(existing_ids)
    for hit in expanded:  # already score-ordered (seeds first, then decay by distance)
        if hit.kind in ARCH_KINDS and hit.id not in seen:
            seen.add(hit.id)
            out.append(ImpactCitation(id=hit.id, kind=hit.kind, label=hit.label))
            if len(out) >= limit:
                break
    return out
