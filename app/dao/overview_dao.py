"""
Read-only DAO for the Active-KB Overview (docs/20 §4a) — deterministic counts over the ACTIVE version.

Reads the ACTIVE ``fe_kb_versions`` row (incl. any stored ``coverage_stats``) and, for the compute-live
path, family counts / product counts / source URIs / entity counts from ``fe_kb_cards`` / ``fe_kb_sources`` /
``fe_kb_nodes`` / ``fe_kb_edges`` / ``fe_kb_chunks``. Read-only; RE owns writes.
"""

from __future__ import annotations

from typing import Any

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool


async def active_version_row(pool: AsyncConnectionPool, gear_id: str) -> dict[str, Any] | None:
    """The ACTIVE version's header row for a GEAR ID (incl. stored coverage_stats), or None."""
    sql = (
        "SELECT kb_version, status, build_date, signed_off_at, signed_off_by, coverage_stats, gate_results "
        "FROM fe_kb_versions WHERE gear_id = %s AND status = 'ACTIVE' ORDER BY build_date DESC LIMIT 1"
    )
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(sql, (gear_id,))
            return await cur.fetchone()


async def health_counts(pool: AsyncConnectionPool, kb_version: str) -> dict[str, int]:
    """Build-health counts: FABRICATED re_anchor verdicts (must be 0) + OPEN review-queue items."""
    sql = """
        SELECT
          (SELECT count(*) FROM fe_kb_evidence      WHERE kb_version = %s AND anchor_verdict = 'FABRICATED') AS fabricated,
          (SELECT count(*) FROM fe_kb_knowledge_gaps WHERE kb_version = %s AND status = 'OPEN')               AS review_open
    """
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(sql, (kb_version, kb_version))
            row = await cur.fetchone()
    return {k: int(v or 0) for k, v in (row or {}).items()}


async def entity_counts(pool: AsyncConnectionPool, kb_version: str) -> dict[str, int]:
    """cards / nodes / edges / chunks counts for a version (one round-trip)."""
    sql = """
        SELECT
          (SELECT count(*) FROM fe_kb_cards  WHERE kb_version = %s) AS cards,
          (SELECT count(*) FROM fe_kb_nodes  WHERE kb_version = %s) AS nodes,
          (SELECT count(*) FROM fe_kb_edges  WHERE kb_version = %s) AS edges,
          (SELECT count(*) FROM fe_kb_chunks WHERE kb_version = %s) AS chunks
    """
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(sql, (kb_version, kb_version, kb_version, kb_version))
            row = await cur.fetchone()
    return {k: int(v or 0) for k, v in (row or {}).items()}


async def family_counts(pool: AsyncConnectionPool, kb_version: str) -> list[dict[str, Any]]:
    """Card counts grouped by KB kind (family)."""
    sql = "SELECT kind, count(*) AS n FROM fe_kb_cards WHERE kb_version = %s GROUP BY kind ORDER BY n DESC"
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(sql, (kb_version,))
            return await cur.fetchall()


async def product_counts(pool: AsyncConnectionPool, kb_version: str) -> list[dict[str, Any]]:
    """Card counts grouped by product/category (e.g. japan-auto-au / -auw)."""
    sql = "SELECT category, count(*) AS n FROM fe_kb_cards WHERE kb_version = %s GROUP BY category ORDER BY n DESC"
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(sql, (kb_version,))
            return await cur.fetchall()


async def source_uris(pool: AsyncConnectionPool, kb_version: str) -> list[dict[str, Any]]:
    """Source URIs + types for a version (drives the tech-stack + repo rollup)."""
    sql = "SELECT uri, source_type FROM fe_kb_sources WHERE kb_version = %s"
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(sql, (kb_version,))
            return await cur.fetchall()
