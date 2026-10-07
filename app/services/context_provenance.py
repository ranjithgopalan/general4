"""
Context Provenance Service — records which chunks/cards entered the assembled prompt.

Called from stage_executor after build_prompt() completes. Writes one row per
candidate into fe_context_provenance so every evaluation question about "what did the
agent receive" and "what was silently dropped by a cap" can be answered with SQL.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)

_MAX_CANDIDATES = 200  # guard against runaway insertions


@dataclass
class ProvenanceRecord:
    run_id: str
    workspace_id: str
    stage: str
    chunk_id: str
    lane: str | None
    rrf_score: float | None
    rank_position: int | None
    was_sent: bool
    cap_reason: str | None = None


async def record_provenance(
    records: list[ProvenanceRecord],
    *,
    pool: Any,
    schema: str,
) -> None:
    """Bulk-insert provenance records into fe_context_provenance. Non-fatal."""
    if not records:
        return
    sql = f"""
        INSERT INTO {schema}.fe_context_provenance
            (run_id, workspace_id, stage, chunk_id, lane,
             rrf_score, rank_position, was_sent, cap_reason)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT DO NOTHING
    """
    try:
        async with pool.connection() as conn:
            for rec in records[:_MAX_CANDIDATES]:
                await conn.execute(sql, (
                    rec.run_id, rec.workspace_id, rec.stage, rec.chunk_id,
                    rec.lane, rec.rrf_score, rec.rank_position,
                    rec.was_sent, rec.cap_reason,
                ))
    except Exception as exc:
        log.warning("[fe_context_provenance] insert failed (non-fatal): %s", exc)


def build_records(
    *,
    run_id: str,
    workspace_id: str,
    stage: str,
    lane_candidates: dict[str, list[str]],
    fused_scores: dict[str, float],
    sent_ids: set[str],
) -> list[ProvenanceRecord]:
    """Build ProvenanceRecord list from retrieval lane data.

    lane_candidates: {"graph": [...ids], "bm25": [...], "dense": [...]}
    fused_scores:    {id: rrf_score}
    sent_ids:        set of IDs that passed the cap and entered the prompt
    """
    seen: set[str] = set()
    records: list[ProvenanceRecord] = []

    for lane, ids in lane_candidates.items():
        for rank, chunk_id in enumerate(ids, start=1):
            if chunk_id in seen:
                continue
            seen.add(chunk_id)
            was_sent = chunk_id in sent_ids
            cap_reason = None if was_sent else "kind_cap_or_top_k"
            records.append(ProvenanceRecord(
                run_id=run_id,
                workspace_id=workspace_id,
                stage=stage,
                chunk_id=chunk_id,
                lane=lane,
                rrf_score=fused_scores.get(chunk_id),
                rank_position=rank,
                was_sent=was_sent,
                cap_reason=cap_reason,
            ))

    return records
