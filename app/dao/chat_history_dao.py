"""
DAO for ``fe_chat_history`` (docs/09 §3.6) — the durable, **per-user** chatbot session log + recall.

Every row is scoped to ``user_id`` (the Okta ``sub``/email). Sessions are isolated per user: two
users may reuse the same client-supplied ``session_id`` without their conversations mixing — every
read filters by ``user_id`` and writes stamp it. Append-only writes; psycopg 3 async over the pool.

``session_name`` is populated on the **first** turn of a new session (first 50 chars of the user
query, trimmed). All subsequent turns leave it NULL. ``list_sessions`` surfaces it via MAX() — only
one row per session carries the value so MAX() is always the real name. Legacy rows without a name
fall back to LEFT(MIN(query), 50) in the aggregate so the sessions list always has a displayable label.
"""

from __future__ import annotations

from typing import Any

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

_SESSION_NAME_MAX = 50


async def has_turns(
    pool: AsyncConnectionPool,
    session_id: str,
    user_id: str | None,
) -> bool:
    """Return True when the session already has at least one recorded turn for this user.

    Used by the service to decide whether to set ``session_name`` on the current turn.
    Single ``EXISTS`` query — no full scan.
    """
    sql = (
        "SELECT EXISTS (SELECT 1 FROM fe_chat_history WHERE session_id = %s AND user_id = %s) AS has_rows"
    )
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(sql, (session_id, user_id))
            row = await cur.fetchone()
            return bool(row and row["has_rows"])


async def save_turn(
    pool: AsyncConnectionPool,
    *,
    session_id: str,
    user_id: str | None,
    persona: str,
    query: str,
    answer: str,
    citations: list[dict[str, Any]],
    confidence: float,
    kb_version: str | None,
    session_name: str | None = None,
) -> None:
    """Append one answered turn to the session log, stamped with the owning ``user_id``.

    ``session_name`` is only non-NULL on the first turn (caller's responsibility to set it);
    all subsequent turns pass None so the name set on turn-1 is not overwritten.
    """
    sql = (
        "INSERT INTO fe_chat_history "
        "(session_id, user_id, persona, query, answer, citations, confidence, kb_version, session_name) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)"
    )
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                sql,
                (session_id, user_id, persona, query, answer, Jsonb(citations), confidence, kb_version, session_name),
            )


async def list_sessions(
    pool: AsyncConnectionPool,
    user_id: str,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Summary of the caller's own sessions, most-recent activity first.

    Each row: session_id, persona, turn_count, started_at, last_at, session_name.
    ``session_name`` is the MAX of the column (non-NULL only on first turn), with a fallback to the
    first 50 chars of the earliest query so legacy rows without an explicit name still display cleanly.
    """
    sql = """
        SELECT
            session_id,
            persona,
            COUNT(*)                                        AS turn_count,
            MIN(created_at)                                 AS started_at,
            MAX(created_at)                                 AS last_at,
            COALESCE(MAX(session_name), LEFT(MIN(query), %s)) AS session_name
        FROM fe_chat_history
        WHERE user_id = %s
        GROUP BY session_id, persona
        ORDER BY last_at DESC
        LIMIT %s OFFSET %s
    """
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(sql, (_SESSION_NAME_MAX, user_id, limit, offset))
            return await cur.fetchall()


async def session_history(
    pool: AsyncConnectionPool,
    session_id: str,
    user_id: str,
) -> list[dict[str, Any]]:
    """All turns for the caller's own ``session_id``, oldest-first. Empty if not owned by ``user_id``."""
    sql = (
        "SELECT session_id, persona, query, answer, citations, confidence, kb_version, created_at "
        "FROM fe_chat_history WHERE session_id = %s AND user_id = %s ORDER BY created_at ASC"
    )
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(sql, (session_id, user_id))
            return await cur.fetchall()


async def recent_turns(
    pool: AsyncConnectionPool,
    session_id: str,
    limit: int,
    user_id: str | None,
) -> list[dict[str, Any]]:
    """The caller's last ``limit`` turns for a session, oldest-first (for prompt threading).

    Filters by ``user_id`` so follow-up resolution never sees another user's history.
    """
    sql = (
        "SELECT query, answer FROM fe_chat_history "
        "WHERE session_id = %s AND user_id = %s ORDER BY created_at DESC LIMIT %s"
    )
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(sql, (session_id, user_id, limit))
            rows = await cur.fetchall()
    return list(reversed(rows))


async def delete_session(pool: AsyncConnectionPool, session_id: str, user_id: str) -> int:
    """Delete the caller's own session turns. Returns rows deleted (0 if not owned by ``user_id``)."""
    sql = "DELETE FROM fe_chat_history WHERE session_id = %s AND user_id = %s"
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(sql, (session_id, user_id))
            return cur.rowcount
