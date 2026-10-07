"""``/ws/{id}/fsd`` — read + lifecycle for the FSD stage (Functional Specification Document).

Thin controllers over ``FSDService`` — same pattern as ``fe_analysis.py``. **Generation is via
fe.generate** (``POST /ws/{id}/generate`` with ``artifact_type="fsd"``). These routes READ the
persisted FSDDocument, advance the lifecycle (accept), and export the SAME artifact as AIG .docx.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Response

from app.core.dependencies import get_current_persona, get_fsd_service
from app.lifecycle.stages.fsd.handler import FSDService
from app.lifecycle.stages.fsd.sections.business import BusinessFsdView
from app.models.workspace import Workspace

router = APIRouter(prefix="/ws", tags=["fsd"])

SvcDep = Annotated[FSDService, Depends(get_fsd_service)]
PersonaDep = Annotated[str, Depends(get_current_persona)]

_DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


@router.get(
    "/{workspace_id}/fsd",
    summary="Get the FSD as the plain-language BUSINESS view (the front-end feed — no ids / IT terms)",
)
async def get_fsd(workspace_id: str, svc: SvcDep) -> BusinessFsdView:
    # The front end shows the business-worded FSD; the stored FSDDocument stays technical internally
    # (downstream BRD/Stories + AC-3 traceability read it via the service, not this route).
    return await svc.get_business_fsd(workspace_id)


@router.post("/{workspace_id}/fsd/accept", summary="Accept the FSD → advance to BRD")
async def accept_fsd(workspace_id: str, svc: SvcDep, persona: PersonaDep) -> Workspace:
    return await svc.accept(workspace_id, persona=persona)


@router.get("/{workspace_id}/fsd/export.docx", summary="Export the FSD as a plain-language BUSINESS .docx")
async def export_fsd_docx(workspace_id: str, svc: SvcDep) -> Response:
    data = await svc.export_business_docx(workspace_id)
    return Response(
        content=data,
        media_type=_DOCX_MIME,
        headers={"Content-Disposition": f'attachment; filename="fsd-{workspace_id}.docx"'},
    )
