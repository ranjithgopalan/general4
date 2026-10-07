"""
fe_kb_review_items DAO (docs/25 Phase 2) — persist + read gap dispositions per (kb_version, gap_id).

Read-only in Phase 1; Phase 2 adds the human decision (FALSE_POSITIVE / RESOLVED / SME_CONFIRMED) that the
GapService merges over the live gap set and the promote gate honours. Async psycopg (dict rows).
"""

from __future__ import annotations

from typing import Any

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

_VALID = {"OPEN", "FALSE_POSITIVE", "RESOLVED", "SME_CONFIRMED"}


async def dispositions(pool: AsyncConnectionPool, kb_version: str) -> dict[str, dict[str, Any]]:
    """gap_id -> disposition row for a version. Tolerant of a not-yet-migrated table (returns {} so the
    read-only review keeps working before migration 0003 is applied)."""
    sql = ("SELECT gap_id, status, note, artifact_ref, disposed_by, disposed_at "
           "FROM fe_kb_review_items WHERE kb_version = %s")
    try:
        async with pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(sql, (kb_version,))
            return {r["gap_id"]: r for r in await cur.fetchall()}
    except Exception:  # noqa: BLE001 — table absent (pre-migration) or transient; degrade to no dispositions
        return {}


async def set_disposition(pool: AsyncConnectionPool, *, kb_version: str, gap_id: str, gap_type: str,
                          severity: str, status: str, note: str | None, by: str | None,
                          artifact_ref: str | None,
                          workspace_id: str | None = None,
                          stage_key: str | None = None) -> None:
    """Upsert a gap's disposition. `status` must be one of the CHECK set."""
    if status not in _VALID:
        raise ValueError(f"invalid status: {status}")
    sql = (
        "INSERT INTO fe_kb_review_items "
        "(kb_version, gap_id, gap_type, severity, status, note, artifact_ref, disposed_by, workspace_id, stage_key) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
        "ON CONFLICT (kb_version, gap_id) DO UPDATE SET status=EXCLUDED.status, note=EXCLUDED.note, "
        "artifact_ref=EXCLUDED.artifact_ref, disposed_by=EXCLUDED.disposed_by, disposed_at=now(), "
        "workspace_id=COALESCE(fe_kb_review_items.workspace_id, EXCLUDED.workspace_id), "
        "stage_key=COALESCE(fe_kb_review_items.stage_key, EXCLUDED.stage_key)"
    )
    async with pool.connection() as conn, conn.cursor() as cur:
        await cur.execute(sql, (kb_version, gap_id, gap_type, severity, status, note, artifact_ref, by,
                                workspace_id, stage_key))
