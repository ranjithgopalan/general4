"""``/ws/{id}/brd`` — read + lifecycle for the BRD stage (Business Requirements Document).

Thin controllers over ``BRDService`` — same pattern as ``fe_fsd.py``. **Generation is via
fe.generate** (``POST /ws/{id}/generate`` with ``artifact_type="brd"``). These routes READ the
persisted BRDDocument, advance the lifecycle (accept → STORIES), and export the SAME artifact
as an official AIG .docx (with AIG logo, version table, sign-off strip).

Optional ``stakeholder_input`` query param allows the BA to pass additional context text that
the BRD agent uses alongside the FSD cards (business-language supplementary requirements).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response

from app.core.dependencies import get_brd_service, get_current_persona
from app.lifecycle.stages.brd.handler import BRDService
from app.lifecycle.stages.brd.schema import BRDDocument
from app.models.workspace import Workspace
from app.utils.exceptions import ResourceNotFoundError

router = APIRouter(prefix="/ws", tags=["brd"])

SvcDep = Annotated[BRDService, Depends(get_brd_service)]
PersonaDep = Annotated[str, Depends(get_current_persona)]

_DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


@router.get("/{workspace_id}/brd", summary="Get the latest BRD artifact")
async def get_brd(workspace_id: str, svc: SvcDep) -> BRDDocument:
    brd = await svc.get(workspace_id)
    if brd is None:
        raise ResourceNotFoundError(f"no BRD for {workspace_id}", {"workspace_id": workspace_id})
    return brd


@router.post("/{workspace_id}/brd/accept", summary="Accept the BRD → advance to STORIES")
async def accept_brd(workspace_id: str, svc: SvcDep, persona: PersonaDep) -> Workspace:
    return await svc.accept(workspace_id, persona=persona)


@router.get("/{workspace_id}/brd/export.docx", summary="Export the BRD as an official AIG .docx")
async def export_brd_docx(workspace_id: str, svc: SvcDep) -> Response:
    data = await svc.export_docx(workspace_id)
    return Response(
        content=data,
        media_type=_DOCX_MIME,
        headers={"Content-Disposition": f'attachment; filename="brd-{workspace_id}.docx"'},
    )
