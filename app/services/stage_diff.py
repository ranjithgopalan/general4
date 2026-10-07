"""
Stage Diff Service — computes context changes between adjacent pipeline stages.

Called after each stage completes. Compares the `was_sent=true` rows in
fe_context_provenance for adjacent stage pairs and writes a diff row into
fe_stage_context_diff so questions 8-10 (carry-forward / lost / introduced) can
be answered without any LLM call.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)

_STAGE_SEQUENCE = [
    "prd", "frd", "epic-set", "feature", "user-story", "coverage",
    "adr", "nfr", "sdd", "srd",
    "lld", "ui-code", "api-code", "db-integration", "security-opa",
    "api-test", "ui-smoke",
]


def _predecessor(stage: str) -> str | None:
    try:
        idx = _STAGE_SEQUENCE.index(stage)
        return _STAGE_SEQUENCE[idx - 1] if idx > 0 else None
    except ValueError:
        return None


async def _fetch_sent_ids(pool: Any, schema: str, workspace_id: str, stage: str) -> set[str]:
    sql = f"""
        SELECT chunk_id FROM {schema}.fe_context_provenance
        WHERE workspace_id = %s AND stage = %s AND was_sent = true
    """
    try:
        async with pool.connection() as conn:
            rows = await conn.fetch(sql, workspace_id, stage)
            return {r["chunk_id"] for r in rows}
    except Exception as exc:
        log.warning("[stage_diff] fetch failed for %s/%s (non-fatal): %s", workspace_id, stage, exc)
        return set()


async def compute_and_persist(
    *,
    workspace_id: str,
    current_stage: str,
    pool: Any,
    schema: str,
) -> None:
    """Compute the context diff from the predecessor stage and write it to fe_stage_context_diff."""
    prev_stage = _predecessor(current_stage)
    if prev_stage is None:
        return

    current_ids = await _fetch_sent_ids(pool, schema, workspace_id, current_stage)
    prev_ids = await _fetch_sent_ids(pool, schema, workspace_id, prev_stage)

    if not current_ids and not prev_ids:
        return

    added = sorted(current_ids - prev_ids)
    dropped = sorted(prev_ids - current_ids)
    carried = sorted(current_ids & prev_ids)

    sql = f"""
        INSERT INTO {schema}.fe_stage_context_diff
            (workspace_id, from_stage, to_stage,
             added_ids, dropped_ids, carried_ids,
             added_count, dropped_count, carried_count)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT DO NOTHING
    """
    try:
        async with pool.connection() as conn:
            await conn.execute(sql, (
                workspace_id, prev_stage, current_stage,
                added, dropped, carried,
                len(added), len(dropped), len(carried),
            ))
        log.info(
            "[stage_diff] %s→%s: added=%d dropped=%d carried=%d",
            prev_stage, current_stage, len(added), len(dropped), len(carried),
        )
    except Exception as exc:
        log.warning("[stage_diff] insert failed (non-fatal): %s", exc)
