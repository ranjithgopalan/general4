"""ContextPackage — the grounded, deterministic context the OPEN ReAct agent reasons over.

Assembled from the match engine (matched · impact set · downstream · coverage · conflicts). The
``allowed_ids`` set is the whitelist the grounding gate enforces on the agent's output (04 §11b).
``format_for_llm`` renders a sectioned context string (TMA `synthesize._build_context_from_fields`).
"""

from __future__ import annotations

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.lifecycle.stages.analysis.schema import AffectedNode, Conflict, Coverage, ImpactCitation

if TYPE_CHECKING:
    from app.lifecycle.stages.analysis.match.walk import WalkResult


@dataclass
class ContextPackage:
    requirement: str
    kb_version: str
    matched: list[ImpactCitation]
    affected: list[AffectedNode]
    downstream: list[AffectedNode]
    coverage: Coverage
    conflicts: list[Conflict]
    semantic_walk_result: WalkResult | None = None  # Aug 12, 2026: pattern-aware semantic walk
    intent_confidence: float | None = None  # LLM intent-analysis confidence → feeds classification confidence
    blind_spots: list | None = None  # Blind spots from technical intent analysis (Phase 4)

    @property
    def allowed_ids(self) -> set[str]:
        return {c.id for c in self.matched} | {n.id for n in self.affected} | {n.id for n in self.downstream}

    def format_for_llm(self) -> str:
        """Sectioned, grounded context for the ReAct agent (ids it may cite)."""
        return "\n\n".join(
            [
                f"## REQUIREMENT\n{self.requirement}",
                "## MATCHED KB CARDS (the change directly touches these)\n"
                + ("\n".join(f"[{c.id}] {c.kind}: {c.label}" for c in self.matched) or "(none retrieved)"),
                "## IMPACT SET (typed-edge neighbours)\n"
                + (
                    "\n".join(f"[{n.id}] {n.kind}: {n.label} (via {', '.join(n.via)})" for n in self.affected)
                    or "(none)"
                ),
                "## DOWNSTREAM — what depends on the change (walk IN)\n"
                + ("\n".join(f"[{n.id}] {n.kind}: {n.label}" for n in self.downstream) or "(none)"),
                "## DETECTED CONFLICTS\n"
                + ("\n".join(f"- {c.kind}: {c.detail}" for c in self.conflicts) or "(none detected deterministically)"),
            ]
        )
