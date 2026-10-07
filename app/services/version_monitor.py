"""Monitor KB version changes and auto-invalidate graph cache."""

from __future__ import annotations

import asyncio
from functools import lru_cache

from app.config import get_settings
from app.dao import graph_dao, postgres
from app.utils.logging import log


class KBVersionMonitor:
    """Watches for KB version changes and invalidates graph cache when needed."""

    def __init__(self) -> None:
        self._last_known_version: str | None = None
        self._monitoring = False

    async def start(self) -> None:
        """Start monitoring for version changes (call on app startup)."""
        if self._monitoring:
            return

        self._monitoring = True
        asyncio.create_task(self._monitor_loop())
        log.info("[version-monitor] Started KB version monitoring")

    async def _monitor_loop(self) -> None:
        """Check every 30s if KB version has changed."""
        while self._monitoring:
            try:
                await self._check_version()
                await asyncio.sleep(30)  # Check every 30 seconds
            except Exception as e:
                log.warning(f"[version-monitor] Error checking KB version: {e}")
                await asyncio.sleep(30)

    async def _check_version(self) -> None:
        """Check if active KB version has changed and invalidate cache if needed."""
        try:
            pool = postgres.get_pool()
            if not pool:
                return

            settings = get_settings()
            current_version = await graph_dao.active_version(pool, settings.GEAR_ID)

            if current_version is None:
                return

            if self._last_known_version is None:
                # First check — just record the current version
                self._last_known_version = current_version
                return

            if current_version != self._last_known_version:
                # Version changed! Invalidate graph cache
                log.info(
                    f"[version-monitor] KB version changed: "
                    f"{self._last_known_version} → {current_version}. "
                    f"Invalidating graph cache."
                )

                # Invalidate the graph provider cache
                from app.services.graph_provider import GraphProvider

                graph_provider = GraphProvider()
                graph_provider.invalidate()

                self._last_known_version = current_version

        except Exception as e:
            log.warning(f"[version-monitor] Failed to check version: {e}")

    def stop(self) -> None:
        """Stop monitoring (call on app shutdown)."""
        self._monitoring = False
        log.info("[version-monitor] Stopped KB version monitoring")


@lru_cache(maxsize=1)
def get_version_monitor() -> KBVersionMonitor:
    """DI provider: singleton KB version monitor."""
    return KBVersionMonitor()
