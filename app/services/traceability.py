"""Artifact lineage traversal service.

Walks the fe_artifact_relationships graph in either direction using a
recursive CTE query.  Supports forward traversal (BRD → Test) and reverse
traversal (Test → BRD) up to a configurable depth limit.

The traversal is breadth-first and cycle-safe (visited set prevents loops).

Usage:
    from app.services.traceability import traverse_lineage
    chain = await traverse_lineage("artifact-id-abc", direction="up", workspace_id="ws-001")
"""

from __future__ import annotations

import logging
from typing import Literal

log = logging.getLogger(__name__)

_MAX_DEPTH = 20   # guard against pathological graphs

# Columns returned per hop in the traversal result.
_HOP_COLS = (
    "artifact_id", "workspace_id", "kind", "status",
    "workflow_run_id", "run_id", "model", "created_at",
)


async def traverse_lineage(
    artifact_id: str,
    *,
    direction: Literal["up", "down"] = "up",
    workspace_id: str | None = None,
    max_depth: int = _MAX_DEPTH,
) -> list[dict]:
    """Return every artifact reachable from ``artifact_id`` in the given direction.

    direction="up"  — walk toward ancestors (reverse: find BRD from a test).
    direction="down" — walk toward descendants (forward: find tests from BRD).

    Each entry in the returned list is a dict with:
        artifact_id, workspace_id, kind, status, workflow_run_id,
        run_id, model, created_at, depth, path, relationship_type

    Returns an empty list when the DB pool is unavailable (non-fatal).
    """
    try:
        from app.dao import postgres  # noqa: PLC0415
        from app.config.settings import get_settings  # noqa: PLC0415
        schema = get_settings().PG_SCHEMA
    except Exception as exc:
        log.debug("[traceability] DB unavailable for traversal (non-fatal): %s", exc)
        return []

    ws_filter = "AND r.workspace_id = %(ws)s" if workspace_id else ""

    if direction == "up":
        # Follow target_artifact_id chain upward to ancestors.
        join_col, parent_col = "r.source_artifact_id", "r.target_artifact_id"
    else:
        # Follow source_artifact_id chain downward to descendants.
        join_col, parent_col = "r.target_artifact_id", "r.source_artifact_id"

    sql = f"""
        WITH RECURSIVE lineage(artifact_id, depth, path, relationship_type) AS (
            -- anchor: the starting artifact
            SELECT
                %(start)s::text AS artifact_id,
                0               AS depth,
                ARRAY[%(start)s::text] AS path,
                NULL::text      AS relationship_type
            UNION ALL
            -- recursive step: one hop per relationship edge
            SELECT
                {parent_col}    AS artifact_id,
                l.depth + 1     AS depth,
                l.path || {parent_col},
                r.relationship_type
            FROM {schema}.fe_artifact_relationships r
            JOIN lineage l ON {join_col} = l.artifact_id
            WHERE
                l.depth < %(max_depth)s
                AND NOT ({parent_col} = ANY(l.path))  -- cycle guard
                {ws_filter}
        )
        SELECT
            l.artifact_id,
            l.depth,
            l.path,
            l.relationship_type,
            a.workspace_id,
            a.kind,
            a.status,
            a.workflow_run_id,
            a.run_id,
            a.model,
            a.created_at
        FROM lineage l
        LEFT JOIN {schema}.fe_workspace_artifacts a ON a.artifact_id = l.artifact_id
        WHERE l.depth > 0
        ORDER BY l.depth, l.artifact_id
    """

    params: dict = {"start": artifact_id, "max_depth": max_depth}
    if workspace_id:
        params["ws"] = workspace_id

    try:
        rows = await postgres.fetch_all(sql, params)
        return [dict(r) for r in rows]
    except Exception as exc:
        log.warning("[traceability] traversal query failed (non-fatal): %s", exc)
        return []


async def orphan_artifacts(workspace_id: str) -> list[str]:
    """Return artifact IDs that have no entry in fe_artifact_relationships.

    These are artifacts with no recorded upstream or downstream relationship —
    they were persisted but their lineage was never written (e.g. a migration
    gap or a pre-migration artifact).

    Returns an empty list when the DB pool is unavailable.
    """
    try:
        from app.dao import postgres  # noqa: PLC0415
        from app.config.settings import get_settings  # noqa: PLC0415
        schema = get_settings().PG_SCHEMA
    except Exception as exc:
        log.debug("[traceability] DB unavailable for orphan check (non-fatal): %s", exc)
        return []

    sql = f"""
        SELECT a.artifact_id
        FROM {schema}.fe_workspace_artifacts a
        WHERE a.workspace_id = %s
          AND NOT EXISTS (
              SELECT 1 FROM {schema}.fe_artifact_relationships r
              WHERE r.workspace_id = a.workspace_id
                AND (r.source_artifact_id = a.artifact_id
                     OR r.target_artifact_id = a.artifact_id)
          )
        ORDER BY a.created_at
    """
    try:
        rows = await postgres.fetch_all(sql, (workspace_id,))
        return [str(r["artifact_id"]) for r in rows]
    except Exception as exc:
        log.warning("[traceability] orphan query failed (non-fatal): %s", exc)
        return []
