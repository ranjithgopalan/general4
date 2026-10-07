"""
Read-only DAO for KB version listing + per-version card index (docs/09 §3.1/§3.2, docs/20 §9).

Backs the deterministic version-diff query mode — exact, not RAG. Read-only; RE owns writes.
"""

from __future__ import annotations

from typing import Any

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

# A forward-engineered (workspace-sync) build carries a ``.ws-<workspace_id>`` suffix on its
# ``kb_version`` — minted by the KB-sync service when a delivered workspace is merged back into the
# KB. A reverse-engineered build has no such suffix. This is the SINGLE place that rule is encoded;
# ``origin`` ("re" | "fe") + the parsed ``workspace_id`` are derived from it so the UI never has to
# re-implement the string check.
_WS_MARKER = ".ws-"


def _stamp_origin(row: dict[str, Any]) -> dict[str, Any]:
    """Annotate a version row with its ``origin`` and, for FE builds, the source ``workspace_id``."""
    version = row.get("kb_version") or ""
    marker = version.rfind(_WS_MARKER)
    if marker >= 0:
        row["origin"] = "fe"
        row["workspace_id"] = version[marker + len(_WS_MARKER):] or None
    else:
        row["origin"] = "re"
        row["workspace_id"] = None
    return row


async def list_versions(
    pool: AsyncConnectionPool, gear_id: str, *, origin: str | None = None
) -> list[dict[str, Any]]:
    """All builds for a GEAR ID, newest first — version, status, sign-off + per-version counts.

    Includes cards / nodes / chunks counts and the dominant product category, so the RE-Console
    "Versions & Status" page can render real rows (docs/18). Each row is stamped with a derived
    ``origin`` ("re" reverse-engineered | "fe" workspace-sync) + the FE ``workspace_id``; pass
    ``origin`` to return only that side (RE Versions & Status vs FE Forward Builds).
    """
    sql = (
        "SELECT v.kb_version, v.gear_id, v.status, v.build_date, v.signed_off_at, v.signed_off_by, "
        "  (SELECT count(*) FROM fe_kb_cards  k WHERE k.kb_version = v.kb_version) AS cards, "
        "  (SELECT count(*) FROM fe_kb_nodes  n WHERE n.kb_version = v.kb_version) AS nodes, "
        "  (SELECT count(*) FROM fe_kb_chunks c WHERE c.kb_version = v.kb_version) AS chunks, "
        # Dominant card category. For a forward (workspace-sync) build — version LIKE '%.ws-%' — a full
        # snapshot inherits the base KB's cards, so count ONLY the forward-origin delta cards; otherwise
        # the column would always show the base's largest bucket, not what the workspace changed.
        "  (SELECT category FROM fe_kb_cards k WHERE k.kb_version = v.kb_version AND category IS NOT NULL "
        "     AND (v.kb_version NOT LIKE '%%.ws-%%' OR k.metadata->>'origin' = 'forward') "
        "     GROUP BY category ORDER BY count(*) DESC LIMIT 1) AS category "
        "FROM fe_kb_versions v WHERE v.gear_id = %s ORDER BY v.build_date DESC"
    )
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(sql, (gear_id,))
            rows = [_stamp_origin(r) for r in await cur.fetchall()]
    if origin in ("re", "fe"):
        rows = [r for r in rows if r["origin"] == origin]
    return rows


async def card_index(pool: AsyncConnectionPool, kb_version: str) -> dict[str, dict[str, Any]]:
    """``id -> {kind, label, source_locus}`` for every card in a version (the diff surface)."""
    sql = "SELECT id, kind, label, source_locus FROM fe_kb_cards WHERE kb_version = %s"
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(sql, (kb_version,))
            rows = await cur.fetchall()
    return {r["id"]: {"kind": r["kind"], "label": r["label"], "source_locus": r["source_locus"]} for r in rows}
