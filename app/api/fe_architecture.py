"""``/ws/{id}/srd`` — read + lifecycle for the Architecture stage (System Requirements Document).

Thin controllers over ``ArchitectureService`` — same pattern as ``fe_brd.py`` and ``fe_fsd.py``.
**Generation is via fe.generate** (``POST /ws/{id}/generate`` with ``artifact_type="srd"``).
These routes READ the persisted SRDDocument, advance the lifecycle (accept → DEVELOPMENT),
and export the same artifact as an official AIG .docx.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel

from app.core.dependencies import get_architecture_service, get_current_persona
from app.lifecycle.stages.architecture.handler import ArchitectureService
from app.lifecycle.stages.architecture.schema import SRDDocument
from app.models.workspace import Workspace
from app.utils.exceptions import ResourceNotFoundError

router = APIRouter(prefix="/ws", tags=["architecture"])

SvcDep = Annotated[ArchitectureService, Depends(get_architecture_service)]
PersonaDep = Annotated[str, Depends(get_current_persona)]

_DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


class _AcceptBody(BaseModel):
    """Optional body for the accept endpoint.

    ``persona`` — when provided (e.g. "architect"), it is used as the effective persona
    for the capability check instead of the JWT-resolved ``get_current_persona``.
    This mirrors the generate-stream pattern where the body carries the persona explicitly.
    """

    persona: str | None = None


@router.get("/{workspace_id}/srd", summary="Get the latest SRD artifact")
async def get_srd(workspace_id: str, svc: SvcDep) -> SRDDocument:
    srd = await svc.get(workspace_id)
    if srd is None:
        raise ResourceNotFoundError(
            f"no SRD for {workspace_id}", {"workspace_id": workspace_id}
        )
    return srd


@router.post(
    "/{workspace_id}/srd/accept",
    summary="Accept the SRD → advance to DEVELOPMENT",
)
async def accept_srd(
    workspace_id: str,
    body: _AcceptBody,
    svc: SvcDep,
    persona_dep: PersonaDep,
) -> Workspace:
    # Use body.persona when explicitly provided (architect persona in dev/CI flows);
    # fall back to the JWT-resolved persona in prod (PERSONA_GROUP_CHECK_ENABLED=True).
    effective_persona = body.persona or persona_dep
    return await svc.accept(workspace_id, persona=effective_persona)


@router.get(
    "/{workspace_id}/srd/export.docx",
    summary="Export the SRD as an official AIG .docx",
)
async def export_srd_docx(workspace_id: str, svc: SvcDep) -> Response:
    data = await svc.export_docx(workspace_id)
    return Response(
        content=data,
        media_type=_DOCX_MIME,
        headers={"Content-Disposition": f'attachment; filename="srd-{workspace_id}.docx"'},
    )
