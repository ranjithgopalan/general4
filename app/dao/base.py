"""
Generic async repository contract + base implementation.

Concrete repositories (KB cards, graph, workspaces, artifacts — later milestones)
subclass ``BaseRepository`` so callers depend on the protocol, not on psycopg
directly. ``BaseRepository`` delegates to the shared async pool in ``dao.postgres``.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol, runtime_checkable

from app.dao import postgres


@runtime_checkable
class RepositoryProtocol(Protocol):
    """Minimal async SQL access shape — all repositories implement this."""

    async def fetch_one(self, sql: str, params: Sequence[Any] | None = None) -> dict[str, Any] | None: ...

    async def fetch_all(self, sql: str, params: Sequence[Any] | None = None) -> list[dict[str, Any]]: ...

    async def execute(self, sql: str, params: Sequence[Any] | None = None) -> int: ...


class BaseRepository:
    """Base for concrete repositories — thin delegation to the shared psycopg pool."""

    async def fetch_one(self, sql: str, params: Sequence[Any] | None = None) -> dict[str, Any] | None:
        return await postgres.fetch_one(sql, params)

    async def fetch_all(self, sql: str, params: Sequence[Any] | None = None) -> list[dict[str, Any]]:
        return await postgres.fetch_all(sql, params)

    async def execute(self, sql: str, params: Sequence[Any] | None = None) -> int:
        return await postgres.execute(sql, params)
