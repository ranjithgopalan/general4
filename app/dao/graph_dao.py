"""
Read-only DAO for the knowledge-graph explorer.

psycopg 3 async over the shared pool (``app.dao.postgres``). Deterministic reads only — RE owns
all graph writes (docs/09 §8). The provider hydrates the full ACTIVE-version graph once via
``fetch_nodes`` / ``fetch_edges`` and walks it in NetworkX; ``fetch_card`` / ``fetch_evidence``
back the tier-2 / tier-3 node drill. Tables: ``fe_kb_versions``, ``fe_kb_nodes``, ``fe_kb_edges``,
``fe_kb_cards``, ``fe_kb_evidence`` (docs/09 §3).
"""

from __future__ import annotations

import hashlib
from typing import Any

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

_NODE_COLS = "id, kind, label, metadata->>'category' AS category, source_locus, confidence, metadata"
_PROSE_EXCERPT_LEN = 200  # chars of card prose backfilled into graph nodes when summary is absent


async def active_version(pool: AsyncConnectionPool, gear_id: str) -> str | None:
    """The single ACTIVE kb_version for a GEAR ID, or None (no built/promoted KB yet)."""
    sql = (
        "SELECT kb_version FROM fe_kb_versions "
        "WHERE gear_id = %s AND status = 'ACTIVE' ORDER BY build_date DESC LIMIT 1"
    )
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(sql, (gear_id,))
            row = await cur.fetchone()
            return row[0] if row else None


async def fetch_node_signatures(pool: AsyncConnectionPool, kb_version: str) -> dict[str, dict[str, str]]:
    """Per-node content signature for a version — {id: {kind, label, sig}} (docs/25 Phase 3 #12).

    kb_nodes has no content_hash column, so the signature is a stable digest of the fields that
    define a node's content (label|kind|source_locus). Two versions' signature maps drive the
    version diff: id in target-not-base = added, base-not-target = removed, both-differ = modified.
    """
    sql = "SELECT id, kind, label, source_locus FROM fe_kb_nodes WHERE kb_version = %s"
    out: dict[str, dict[str, str]] = {}
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(sql, (kb_version,))
            for r in await cur.fetchall():
                blob = f"{r.get('label') or ''}|{r.get('kind') or ''}|{r.get('source_locus') or ''}"
                out[r["id"]] = {
                    "kind": r.get("kind") or "",
                    "label": r.get("label") or r["id"],
                    "sig": hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16],  # content key, not a secret
                }
    return out


async def fetch_nodes(
    pool: AsyncConnectionPool,
    kb_version: str,
    category: str | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    sql = f"SELECT {_NODE_COLS} FROM fe_kb_nodes WHERE kb_version = %s"
    params: list[Any] = [kb_version]
    if category:
        sql += " AND category = %s"
        params.append(category)
    sql += " ORDER BY id"
    if limit:
        sql += " LIMIT %s"
        params.append(limit)
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(sql, params)
            return await cur.fetchall()


async def fetch_nodes_with_summary(
    pool: AsyncConnectionPool,
    kb_version: str,
    category: str | None = None,
) -> list[dict[str, Any]]:
    """Like ``fetch_nodes`` but LEFT JOINs ``fe_kb_cards`` to backfill a prose excerpt when
    ``metadata.summary`` is absent.  The excerpt is stored as ``prose_excerpt`` on each row
    so ``GraphProvider._norm_node`` can use it as a fallback summary for the inverted index.
    """
    sql = f"""
        SELECT n.id, n.kind, n.label, n.category, n.source_locus,
               n.confidence, n.metadata,
               LEFT(c.text_en, %s) AS prose_excerpt
        FROM fe_kb_nodes n
        LEFT JOIN fe_kb_cards c ON c.id = n.id AND c.kb_version = n.kb_version
        WHERE n.kb_version = %s
    """
    params: list[Any] = [_PROSE_EXCERPT_LEN, kb_version]
    if category:
        sql += " AND n.category = %s"
        params.append(category)
    sql += " ORDER BY n.id"
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(sql, params)
            return await cur.fetchall()


async def fetch_edges(
    pool: AsyncConnectionPool,
    kb_version: str,
    category: str | None = None,
) -> list[dict[str, Any]]:
    """Edges whose BOTH endpoints exist as nodes (drops dangling; category-scoped if given)."""
    sql = """
        SELECT e.kb_edge_id AS id, e.from_id, e.to_id, e.label, e.tag, e.confidence, e.metadata
        FROM fe_kb_edges e
        JOIN fe_kb_nodes s ON s.id = e.from_id AND s.kb_version = e.kb_version
        JOIN fe_kb_nodes t ON t.id = e.to_id   AND t.kb_version = e.kb_version
        WHERE e.kb_version = %s
    """
    params: list[Any] = [kb_version]
    if category:
        sql += " AND s.category = %s AND t.category = %s"
        params += [category, category]
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(sql, params)
            return await cur.fetchall()


async def fetch_card(pool: AsyncConnectionPool, kb_version: str, card_id: str) -> dict[str, Any] | None:
    """Tier-2 drill: the full card body for a node id."""
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "SELECT id, kind, metadata->>'label' AS label, text_en AS prose, text_en, metadata FROM fe_kb_cards WHERE kb_version = %s AND id = %s",
                (kb_version, card_id),
            )
            return await cur.fetchone()


async def pinned_version_for_workspace(pool: AsyncConnectionPool, workspace_id: str) -> str | None:
    """Return pinned_kb_version for a workspace, or None (Gap 6 — workspace-scoped KB queries)."""
    sql = "SELECT pinned_kb_version FROM fe_workspaces WHERE workspace_id = %s LIMIT 1"
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(sql, (workspace_id,))
            row = await cur.fetchone()
    return row[0] if row else None


async def fetch_evidence(pool: AsyncConnectionPool, kb_version: str, card_id: str) -> list[dict[str, Any]]:
    """Tier-3 drill: verbatim evidence spans + re_anchor verdicts for a card."""
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "SELECT source_locus, snippet, anchor_verdict, authority_tier FROM fe_kb_evidence "
                "WHERE kb_version = %s AND card_id = %s ORDER BY id",
                (kb_version, card_id),
            )
            return await cur.fetchall()
