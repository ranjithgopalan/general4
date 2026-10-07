"""
Postgres data layer — psycopg 3 async connection pool (+ pgvector).

House decision (docs/04, docs/09): psycopg 3 async + ``psycopg_pool.AsyncConnectionPool``
with raw SQL — NOT SQLAlchemy, NOT DynamoDB. One process-wide pool, opened in the app
lifespan and closed on shutdown. Credentials resolve from Vault (``PG_VAULT_NAME``); in
local dev with no Vault, libpq env vars (PGUSER/PGPASSWORD) fill in.

Lifecycle:  open_pool() → (fetch_one / fetch_all / execute) → close_pool()
Readiness:  check_health() runs ``SELECT 1`` through the pool.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from app.config import get_settings
from app.utils.exceptions import ServiceUnavailableError
from app.utils.logging import log

_pool: AsyncConnectionPool | None = None


async def _build_conninfo() -> str:
    """Assemble a libpq conninfo string; resolve user/password from Vault when configured."""
    s = get_settings()
    from psycopg.conninfo import make_conninfo

    if s.pg_local:
        # PG_CONNECTION_MODE=local: the URL is complete (user, password, host, db); no Vault, no forced SSL.
        log.info(f"[dao/postgres] PG_CONNECTION_MODE=local — using PG_LOCAL_URL (schema {s.PG_SCHEMA})")
        return make_conninfo(s.PG_LOCAL_URL, connect_timeout=str(s.PG_CONNECT_TIMEOUT))

    parts: dict[str, Any] = {
        "host": s.PGHOST,
        "port": s.PGPORT,
        "dbname": s.PGDATABASE,
        "connect_timeout": s.PG_CONNECT_TIMEOUT,
        "sslmode": s.PG_SSLMODE,
    }
    if s.HASHICORP_API_URL and s.PG_VAULT_NAME:
        try:
            from app.utils.hashicorp_client import get_vault_client

            # PG creds live under their own appcode (PG_DB_VAULT_APP_CODE, e.g. "ah"),
            # vault PG_VAULT_NAME (pg_db_vault), keys PG_DB_USER / PG_DB_PWD.
            creds = await get_vault_client().get_credentials(
                s.PG_VAULT_NAME, app_code=s.PG_DB_VAULT_APP_CODE
            )
            if creds.username:
                parts["user"] = creds.username
            if creds.password:
                parts["password"] = creds.password
        except Exception as exc:
            log.warning(f"[dao/postgres] Vault creds unavailable ({exc}); falling back to libpq env.")
    # Use psycopg's escaper — a naive "k=v" join corrupts values with spaces/special chars
    # (e.g. a password containing a space or '='), which surfaces as a bogus auth failure.
    return make_conninfo(**{k: str(v) for k, v in parts.items() if v not in (None, "")})


async def _configure(conn: psycopg.AsyncConnection) -> None:
    """Per-connection setup: statement timeout, search_path + pgvector type registration.

    Runs in autocommit so this ``configure`` callback returns the connection IDLE, as the pool
    requires. Otherwise the ``SET`` statements open a transaction (non-autocommit) — and the
    best-effort pgvector probe below can *abort* it when the extension is absent — leaving the
    connection INTRANS, which makes the pool discard every connection and never fill (PoolTimeout).
    Autocommit is restored to the pool default afterwards.
    """
    s = get_settings()
    await conn.set_autocommit(True)
    try:
        if s.PG_STATEMENT_TIMEOUT_MS and s.PG_STATEMENT_TIMEOUT_MS > 0:
            await conn.execute(f"SET statement_timeout = {int(s.PG_STATEMENT_TIMEOUT_MS)}")
        if s.PG_SCHEMA:
            # All AIDLC tables live in this schema — set search_path per connection (safely quoted).
            # Append `public` so extension types/functions resolve by unqualified name — pgvector's
            # `vector` type is created in `public`, and register_vector_async() below looks it up by
            # name; without `public` on the path the lookup misses it ("vector type not found").
            from psycopg import sql

            await conn.execute(
                sql.SQL("SET search_path TO {}, public").format(sql.Identifier(s.PG_SCHEMA))
            )
        try:
            from pgvector.psycopg import register_vector_async

            await register_vector_async(conn)
        except Exception as exc:  # vector extension/type not present yet — non-fatal
            log.debug(f"[dao/postgres] pgvector not registered on connection: {exc}")
    finally:
        await conn.set_autocommit(False)


async def open_pool() -> None:
    """Open the process-wide async pool. No-op when already open or PGHOST is unset (local/no-DB)."""
    global _pool
    if _pool is not None:
        return
    s = get_settings()
    if not s.PGHOST and not s.pg_local:
        log.info("[dao/postgres] PGHOST empty — skipping pool (no DB configured for this env).")
        return
    conninfo = await _build_conninfo()
    _pool = AsyncConnectionPool(
        conninfo,
        min_size=s.PG_POOL_MIN_SIZE,
        max_size=s.PG_POOL_MAX_SIZE,
        timeout=s.PG_POOL_TIMEOUT,
        configure=_configure,
        open=False,
    )
    await _pool.open(wait=True, timeout=s.PG_POOL_TIMEOUT)
    log.info(
        f"[dao/postgres] Pool opened (host={s.PGHOST} db={s.PGDATABASE} size={s.PG_POOL_MIN_SIZE}-{s.PG_POOL_MAX_SIZE})"
    )


async def close_pool() -> None:
    """Close the pool on shutdown."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
        log.info("[dao/postgres] Pool closed.")


def get_pool() -> AsyncConnectionPool:
    """Return the open pool, or raise if not initialized."""
    if _pool is None:
        raise ServiceUnavailableError("Postgres pool is not initialized.")
    return _pool


async def check_health() -> bool:
    """Readiness ping — SELECT 1 through the pool. False if the pool is not open."""
    if _pool is None:
        return False
    try:
        async with _pool.connection() as conn:
            cur = await conn.execute("SELECT 1")
            row = await cur.fetchone()
            return bool(row) and row[0] == 1
    except Exception as exc:
        log.warning(f"[dao/postgres] Health check failed: {exc}")
        return False


# ── Low-level query helpers (used by repositories) ────────────────────────────
async def fetch_one(sql: str, params: Sequence[Any] | None = None) -> dict[str, Any] | None:
    """Run a query and return the first row as a dict, or None."""
    async with get_pool().connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(sql, params)
            return await cur.fetchone()


async def fetch_all(sql: str, params: Sequence[Any] | None = None) -> list[dict[str, Any]]:
    """Run a query and return all rows as a list of dicts."""
    async with get_pool().connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(sql, params)
            return await cur.fetchall()


async def execute(sql: str, params: Sequence[Any] | None = None) -> int:
    """Run a write/DDL statement and return affected row count."""
    async with get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(sql, params)
            return cur.rowcount
