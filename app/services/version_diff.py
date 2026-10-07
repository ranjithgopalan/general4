"""
Deterministic KB version-diff (docs/20 §9) — exact set-diff over ``kb_cards``, not RAG.

Answers "what changed between v1 and v2 / last quarter" precisely and completely by comparing the two
versions' card indexes: added / removed / changed (label or source_locus differs) / unchanged count.
No LLM. Requires ≥ 2 versions to exist.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from app.config.settings import Settings, get_settings
from app.dao import version_dao
from app.models.kb import VersionDiff


def diff_indexes(a: dict[str, dict[str, Any]], b: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Pure diff of two card indexes: added / removed / changed (label|locus) / unchanged count."""
    a_ids, b_ids = set(a), set(b)
    common = a_ids & b_ids
    changed = sorted(
        i
        for i in common
        if (a[i].get("label"), a[i].get("source_locus")) != (b[i].get("label"), b[i].get("source_locus"))
    )
    return {
        "added": sorted(b_ids - a_ids),
        "removed": sorted(a_ids - b_ids),
        "changed": changed,
        "unchanged": len(common) - len(changed),
    }


class VersionDiffService:
    """List KB versions and diff any two (deterministic)."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    @staticmethod
    def _pool():
        from app.dao.postgres import get_pool

        return get_pool()

    async def versions(self, *, origin: str | None = None) -> list[dict[str, Any]]:
        """List KB builds (newest first). ``origin`` filters to "re" (reverse-engineered) or "fe"
        (workspace-sync) builds; ``None`` returns all. Each row carries a derived ``origin``."""
        return await version_dao.list_versions(self._pool(), self._settings.GEAR_ID, origin=origin)

    async def diff(self, from_version: str, to_version: str) -> VersionDiff:
        pool = self._pool()
        a = await version_dao.card_index(pool, from_version)
        b = await version_dao.card_index(pool, to_version)
        return VersionDiff(from_version=from_version, to_version=to_version, **diff_indexes(a, b))


@lru_cache
def get_version_diff_service() -> VersionDiffService:
    """Process-wide singleton VersionDiffService (cached)."""
    return VersionDiffService()
