"""``/ws/{id}/impact`` — read + lifecycle for the detailed Impact Analysis (ANALYSIS stage).

Thin controllers over ``ImpactAnalysisService``. **Generation is via fe.generate** (docs/11 §2.3,
``POST /ws/{id}/generate`` with ``artifact_type="analysis"``) — these routes only READ the persisted
``ImpactAnalysis`` (the FE page), advance/close the lifecycle (accept/reject), and export the SAME
artifact as an official AIG ``.docx``. Auth via the middleware chain.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Response

from app.core.dependencies import get_current_persona, get_impact_analysis_service
from app.lifecycle.stages.analysis.handler import ImpactAnalysisService
from app.lifecycle.stages.analysis.schema import ImpactAnalysis
from app.lifecycle.stages.analysis.sections.business import BusinessImpactView
from app.models.workspace import Workspace
from app.utils.exceptions import ResourceNotFoundError

router = APIRouter(prefix="/ws", tags=["impact-analysis"])

SvcDep = Annotated[ImpactAnalysisService, Depends(get_impact_analysis_service)]
PersonaDep = Annotated[str, Depends(get_current_persona)]

_DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


@router.get("/{workspace_id}/impact", summary="Get the latest impact analysis")
async def get_impact(workspace_id: str, svc: SvcDep) -> ImpactAnalysis:
    analysis = await svc.get(workspace_id)
    if analysis is None:
        raise ResourceNotFoundError(f"no impact analysis for {workspace_id}", {"workspace_id": workspace_id})
    return analysis


@router.post("/{workspace_id}/impact/accept", summary="Accept the analysis → advance to FSD (or CLOSE if Existing)")
async def accept_impact(workspace_id: str, svc: SvcDep, persona: PersonaDep) -> Workspace:
    return await svc.accept(workspace_id, persona=persona)


@router.post("/{workspace_id}/impact/reject", summary="Reject the analysis (stays at ANALYSIS for revision)")
async def reject_impact(workspace_id: str, svc: SvcDep, persona: PersonaDep) -> Workspace:
    return await svc.reject(workspace_id, persona=persona)


@router.get(
    "/{workspace_id}/impact/business",
    summary="Plain-language BUSINESS view of the impact analysis (the front-end feed — no ids / IT terms)",
)
async def get_impact_business(workspace_id: str, svc: SvcDep) -> BusinessImpactView:
    return await svc.get_business_view(workspace_id)


@router.get(
    "/{workspace_id}/impact/export.business.docx",
    summary="Export the plain-language BUSINESS impact analysis .docx (the UI download)",
)
async def export_impact_business_docx(workspace_id: str, svc: SvcDep) -> Response:
    data = await svc.export_business_docx(workspace_id)
    return Response(
        content=data,
        media_type=_DOCX_MIME,
        headers={"Content-Disposition": f'attachment; filename="impact-analysis-business-{workspace_id}.docx"'},
    )


@router.get(
    "/{workspace_id}/impact/export.docx",
    summary="Export the TECHNICAL impact analysis .docx (also archived to S3; not surfaced in the UI)",
)
async def export_impact_docx(workspace_id: str, svc: SvcDep) -> Response:
    data = await svc.export_docx(workspace_id)
    return Response(
        content=data,
        media_type=_DOCX_MIME,
        headers={"Content-Disposition": f'attachment; filename="impact-analysis-{workspace_id}.docx"'},
    )
