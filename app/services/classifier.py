"""ANALYSIS change-classifier + impact (docs/04 §4/§8d) — deterministic vs the KB, LLM-refined later.

Classifies a requirement against the KB via **kb.query** (persona-filtered): no match -> **New**;
overlap -> **Enhancement** (the LLM later refines Existing / Derived). The affected set = the matched
KB cards; graph-neighbour impact expansion (via ``GraphProvider.impact``) is the enhancement seam.
Framework: deterministic + persona-scoped, no forced LLM.
"""

from __future__ import annotations

from app.models.kb import ClassificationResult
from app.services.kb_query import KbQueryService


class ChangeClassifier:
    """Classify a requirement against the KB and surface the matched/affected set."""

    def __init__(self, kb_query: KbQueryService | None = None) -> None:
        self._kb = kb_query or KbQueryService()

    async def classify(self, *, requirement: str, persona: str = "ba", top_k: int = 8) -> ClassificationResult:
        answer = await self._kb.query(question=requirement or "", persona=persona, top_k=top_k)
        matched = [c.id for c in answer.citations]
        if answer.abstained or not matched:
            return ClassificationResult(
                change_class="New",
                confidence=answer.confidence,
                rationale="no matching KB concept found — net-new",
            )
        return ClassificationResult(
            change_class="Enhancement",
            matched_ids=matched,
            affected_ids=matched,  # graph-neighbour expansion is the impact-walk enhancement seam
            confidence=answer.confidence,
            rationale=f"matches {len(matched)} existing KB card(s)",
        )
