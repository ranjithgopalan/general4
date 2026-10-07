"""kb-refresh enqueue seam (docs/19 §8).

On workspace CLOSE (from PENDING_SYNC), a ``kb-refresh`` — the RE write-path that folds the delivery
back into the KB — is **enqueued** as a queued job, never an in-process call. This module is the seam:
a small ``RefreshEnqueuer`` protocol + a framework-phase stub that records + logs the request. A real
queue (SQS / Celery / a DB job table) implements the same protocol later, when RE resumes.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.utils.logging import log


@runtime_checkable
class RefreshEnqueuer(Protocol):
    """Enqueue a kb-refresh for a closed workspace; returns a job id."""

    async def enqueue(self, workspace_id: str, *, gear_id: str = "japan") -> str: ...


class StubRefreshEnqueuer:
    """Framework-phase stub: records the enqueue request and logs it (no real queue yet)."""

    def __init__(self) -> None:
        self.enqueued: list[dict[str, str]] = []

    async def enqueue(self, workspace_id: str, *, gear_id: str = "japan") -> str:
        job_id = f"refresh-{workspace_id}"
        self.enqueued.append({"job_id": job_id, "workspace_id": workspace_id, "gear_id": gear_id})
        log.info(f"[refresh-queue] enqueued kb-refresh job={job_id} workspace={workspace_id} gear={gear_id}")
        return job_id


_stub = StubRefreshEnqueuer()


def get_refresh_enqueuer() -> RefreshEnqueuer:
    """DI provider: the process-wide refresh enqueuer (stub until a real queue lands)."""
    return _stub
