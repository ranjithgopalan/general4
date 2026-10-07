"""
Graph Repository (Finding 7) — deep graph traversal via PostgreSQL recursive CTEs.

GraphProvider.neighbors() / expand_seeds() use NetworkX for shallow, fast queries.
This module supplements for deep traversal (depth > 2) only, querying fe_kb_edges
with WITH RECURSIVE CTEs so the full graph does not need to be loaded into memory.

Column names from migration 0017: from_id, to_id, edge_type, kb_version.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)

_MAX_DEPTH = 10


async def get_descendants(
    node_id: str,
    *,
    kb_version: str,
    pool: Any,
    schema: str,
    max_depth: int = 5,
) -> list[str]:
    """Return all descendant node IDs reachable from node_id via directed edges (from_id → to_id)."""
    max_depth = min(max_depth, _MAX_DEPTH)
    sql = f"""
        WITH RECURSIVE descendants(node_id, depth) AS (
            SELECT to_id, 1
            FROM {schema}.fe_kb_edges
            WHERE from_id = %s AND kb_version = %s
            UNION ALL
            SELECT e.to_id, d.depth + 1
            FROM {schema}.fe_kb_edges e
            JOIN descendants d ON e.from_id = d.node_id
            WHERE d.depth < %s AND e.kb_version = %s
        )
        SELECT DISTINCT node_id FROM descendants
    """
    return await _fetch_ids(sql, (node_id, kb_version, max_depth, kb_version), pool)


async def get_ancestors(
    node_id: str,
    *,
    kb_version: str,
    pool: Any,
    schema: str,
    max_depth: int = 5,
) -> list[str]:
    """Return all ancestor node IDs that can reach node_id via directed edges."""
    max_depth = min(max_depth, _MAX_DEPTH)
    sql = f"""
        WITH RECURSIVE ancestors(node_id, depth) AS (
            SELECT from_id, 1
            FROM {schema}.fe_kb_edges
            WHERE to_id = %s AND kb_version = %s
            UNION ALL
            SELECT e.from_id, a.depth + 1
            FROM {schema}.fe_kb_edges e
            JOIN ancestors a ON e.to_id = a.node_id
            WHERE a.depth < %s AND e.kb_version = %s
        )
        SELECT DISTINCT node_id FROM ancestors
    """
    return await _fetch_ids(sql, (node_id, kb_version, max_depth, kb_version), pool)


async def get_path(
    source_id: str,
    target_id: str,
    *,
    kb_version: str,
    pool: Any,
    schema: str,
    max_depth: int = 10,
) -> list[str]:
    """Return the shortest path (list of node IDs) from source_id to target_id, or [] if unreachable."""
    max_depth = min(max_depth, _MAX_DEPTH)
    sql = f"""
        WITH RECURSIVE path_search(node_id, path, depth) AS (
            SELECT to_id, ARRAY[%s::TEXT, to_id], 1
            FROM {schema}.fe_kb_edges
            WHERE from_id = %s AND kb_version = %s
            UNION ALL
            SELECT e.to_id, p.path || e.to_id, p.depth + 1
            FROM {schema}.fe_kb_edges e
            JOIN path_search p ON e.from_id = p.node_id
            WHERE p.depth < %s
              AND NOT (e.to_id = ANY(p.path))
              AND e.kb_version = %s
        )
        SELECT path FROM path_search
        WHERE node_id = %s
        ORDER BY depth
        LIMIT 1
    """
    try:
        async with pool.connection() as conn:
            from psycopg.rows import dict_row  # noqa: PLC0415
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(sql, (source_id, source_id, kb_version, max_depth, kb_version, target_id))
                row = await cur.fetchone()
                return row["path"] if row else []
    except Exception as exc:  # noqa: BLE001
        log.warning("[graph_repository] get_path failed: %s", exc)
        return []


async def _fetch_ids(sql: str, params: tuple, pool: Any) -> list[str]:
    try:
        async with pool.connection() as conn:
            from psycopg.rows import dict_row  # noqa: PLC0415
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(sql, params)
                rows = await cur.fetchall()
                return [r["node_id"] for r in rows]
    except Exception as exc:  # noqa: BLE001
        log.warning("[graph_repository] fetch failed: %s", exc)
        return []
