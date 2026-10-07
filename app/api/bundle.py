"""``GET /ws/{id}/bundle`` — the single grounded read the local Developer plugin fetches by workspace_id.

docs/27 §6.1 (P1). Composes the accepted upstream artifacts (impact / FSD / SRD / stories / dev) +
change-specs + target files + repo hints + KB citations into one payload. Read-only; the state model is
unchanged (the DB stays the source of truth — see docs/27 §4). The plugin maps each ``repo_hints`` prefix
to a local repo root via its own config; central never emits local paths.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.core.dependencies import get_current_persona, get_workspace_bundle_service
from app.services.workspace_bundle import WorkspaceBundle, WorkspaceBundleService

router = APIRouter(prefix="/ws", tags=["developer"])

SvcDep = Annotated[WorkspaceBundleService, Depends(get_workspace_bundle_service)]
PersonaDep = Annotated[str, Depends(get_current_persona)]


@router.get(
    "/{workspace_id}/bundle",
    summary="Get the grounded workspace bundle (specs + change-specs + target files + citations)",
)
async def get_bundle(workspace_id: str, svc: SvcDep, persona: PersonaDep) -> WorkspaceBundle:
    """Aggregate every accepted upstream artifact + the developer change-specs for the local plugin.

    Raises 404 only if the workspace itself does not exist; a stage with no artifact yet is reported
    as ``present: false`` rather than failing the read.
    """
    return await svc.build(workspace_id)
