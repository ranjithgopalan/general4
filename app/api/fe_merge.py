"""``/ws/{id}/merge`` — the MERGE stage (DevOps): merge delivered work + trigger the KB-refresh.

After QA accepts, the workspace advances to MERGE. Hitting *Merge* here (DevOps persona) runs the
central ``KbRefreshService`` in the BACKGROUND (docs/23): the accepted, grounded artifacts are
transformed into a KB delta, merged onto the current KB export, and loaded as a NEW immutable version
(STAGING) via the central kb-indexer — awaiting human promotion to ACTIVE. The workspace then moves to
PENDING_SYNC (syncing) and, once the new version is promoted, to CLOSED.

The refresh is fire-and-forget (BackgroundTasks) so the request returns immediately; the UI polls the
workspace state. A refresh failure never corrupts the lifecycle — the workspace still moves to
PENDING_SYNC and the failure is logged (the new version simply isn't produced).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends

from app.core.dependencies import get_current_persona, get_kb_refresh_service, get_workspace_service
from app.lifecycle.kb_sync import KbRefreshService
from app.models.workspace import Workspace, WorkspaceState
from app.services.workspace_service import WorkspaceService
from app.utils.logging import log

router = APIRouter(prefix="/ws", tags=["merge"])

WsDep = Annotated[WorkspaceService, Depends(get_workspace_service)]
RefreshDep = Annotated[KbRefreshService, Depends(get_kb_refresh_service)]
PersonaDep = Annotated[str, Depends(get_current_persona)]


async def _run_and_sync(workspace_id: str, refresh: KbRefreshService, ws_svc: WorkspaceService) -> None:
    """Background: run the KB-refresh, then advance MERGE -> PENDING_SYNC (best-effort, never raises)."""
    result = await refresh.run(workspace_id)
    log.info(f"[merge] ws={workspace_id} refresh ok={result.ok} version={result.new_kb_version} "
             f"{result.delta_counts} — {result.message}")
    try:
        ws = await ws_svc.get(workspace_id)
        if ws.state is WorkspaceState.MERGE:
            await ws_svc.advance(workspace_id, actor="kb-refresh")  # MERGE -> PENDING_SYNC
    except Exception as exc:  # noqa: BLE001
        log.warning(f"[merge] ws={workspace_id} could not advance to PENDING_SYNC: {exc}")


@router.post("/{workspace_id}/merge", summary="Merge (DevOps) → trigger the background KB-refresh")
async def merge(
    workspace_id: str, bg: BackgroundTasks, ws_svc: WsDep, refresh: RefreshDep, persona: PersonaDep,
) -> dict:
    """Advance QA_TESTING -> MERGE (if needed) and kick off the KB-refresh round-trip in the background."""
    ws = await ws_svc.get(workspace_id)
    if ws.state is WorkspaceState.QA_TESTING:
        await ws_svc.advance(workspace_id, actor=f"persona:{persona}")  # QA -> MERGE
    bg.add_task(_run_and_sync, workspace_id, refresh, ws_svc)
    return {
        "workspace_id": workspace_id,
        "state": WorkspaceState.MERGE.value,
        "status": "syncing",
        "detail": "Merging delivered work and refreshing the KB — a new STAGING version will await sign-off.",
    }


@router.get("/{workspace_id}/merge", summary="Merge/sync status (current workspace state)")
async def merge_status(workspace_id: str, ws_svc: WsDep) -> Workspace:
    return await ws_svc.get(workspace_id)
