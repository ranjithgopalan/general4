"""Deterministic retrieval — requirement → matched KB cards for the impact walk (cite-or-abstain).

RECALL-biased + ALL-kinds (docs/26 impact priority): the impact walk can only start from what this
seed finds, and a business epic can impact CODE (API/CMP). So the seed reads across every kind
(not persona-scoped) and is wider than the Q&A default — a missed seed = a missed break. Persona
still gates FE writes; this only widens the READ scope for impact analysis.
"""

from __future__ import annotations

from app.config import get_settings
from app.models.kb import KbAnswer
from app.services.kb_query import KbQueryService


async def retrieve(kb: KbQueryService, *, requirement: str, persona: str) -> KbAnswer:
    """Recall-biased, all-kinds grounded retrieval of the cards the requirement touches.

    NOTE: BR/FR discovery moved to ANALYSIS stage (compute_affected graph walk via GOVERNED_BY edges).
    The retrieval phase returns screens/tables/systems/components; the graph walk finds BR/FR.
    """
    s = get_settings()
    return await kb.query(
        question=requirement, persona=persona,
        top_k=s.ANALYSIS_SEED_TOP_K, all_kinds=s.ANALYSIS_SEED_ALL_KINDS,
        task="analyze",  # Use analyze token limit (8k) for analysis-stage kb.query
    )
