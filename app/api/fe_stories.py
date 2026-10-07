"""``/ws/{id}/stories`` — read + lifecycle for the STORIES stage (User Stories Backlog).

Thin controllers over ``StoriesService`` — same pattern as ``fe_brd.py``. Generation is via
fe.generate (``POST /ws/{id}/generate`` with ``artifact_type="stories"``). These routes READ
the persisted StoriesDocument, advance the lifecycle (accept → ARCHITECTURE), and export the
SAME artifact as a Rally-compatible CSV with HTML descriptions (Q2 decision).

Stories are exported as CSV only (not .docx) — they live in Rally.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Response

from app.core.dependencies import get_stories_service, get_current_persona
from app.lifecycle.render.docx import render_stories_csv
from app.lifecycle.stages.stories.handler import StoriesService
from app.lifecycle.stages.stories.schema import StoriesDocument
from app.models.workspace import Workspace
from app.utils.exceptions import ResourceNotFoundError

router = APIRouter(prefix="/ws", tags=["stories"])

SvcDep = Annotated[StoriesService, Depends(get_stories_service)]
PersonaDep = Annotated[str, Depends(get_current_persona)]

_CSV_MIME = "text/csv; charset=utf-8"


@router.get("/{workspace_id}/stories", summary="Get the latest Stories artifact")
async def get_stories(workspace_id: str, svc: SvcDep) -> StoriesDocument:
    doc = await svc.get(workspace_id)
    if doc is None:
        raise ResourceNotFoundError(
            f"no Stories for {workspace_id}", {"workspace_id": workspace_id}
        )
    return doc


@router.post(
    "/{workspace_id}/stories/accept",
    summary="Accept the Stories backlog → advance to ARCHITECTURE",
)
async def accept_stories(workspace_id: str, svc: SvcDep, persona: PersonaDep) -> Workspace:
    return await svc.accept(workspace_id, persona=persona)


@router.get(
    "/{workspace_id}/stories/export.csv",
    summary="Export Stories as a Rally-compatible CSV with HTML descriptions",
)
async def export_stories_csv(workspace_id: str, svc: SvcDep) -> Response:
    doc = await svc.get(workspace_id)
    if doc is None:
        raise ResourceNotFoundError(
            f"no Stories for {workspace_id}", {"workspace_id": workspace_id}
        )
    csv_bytes = render_stories_csv(doc)
    return Response(
        content=csv_bytes,
        media_type=_CSV_MIME,
        headers={
            "Content-Disposition": f'attachment; filename="stories-{workspace_id}.csv"',
        },
    )
