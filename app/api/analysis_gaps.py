"""Analysis gaps & clarifications API (docs/IMPACT_ANALYSIS_CLARIFICATION_MERGED.md).

Routes for gap detection, clarification save, and rerun with clarifications.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel

from app.core.dependencies import (
    get_current_persona,
    get_analysis_service,
    get_workspace_service,
)
from app.dao.clarification_dao import (
    ClarificationRepository,
    RequirementVersionRepository,
)
from app.lifecycle.stages.analysis.schema_gaps import (
    Clarification,
    Gap,
    MergePreview,
    GapsIterationResult,
)
from app.lifecycle.stages.analysis.sections.gap_detection import (
    detect_gaps,
    merge_clarifications_into_requirement,
    build_merge_preview,
)
from app.services.workspace_service import WorkspaceService
from app.utils.exceptions import ResourceNotFoundError, ValidationError
from app.utils.logging import log

router = APIRouter(prefix="/ws", tags=["analysis-gaps"])

SvcDep = Annotated[Any, Depends(get_analysis_service)]
WsSvcDep = Annotated[WorkspaceService, Depends(get_workspace_service)]
PersonaDep = Annotated[str, Depends(get_current_persona)]


class SaveClarificationRequest(BaseModel):
    """Request to save a clarification."""

    gap_id: str
    gap_type: str
    user_input: str | dict
    clarification_type: str


class RerunRequest(BaseModel):
    """Request to rerun analysis with clarifications."""

    pass  # No params needed; uses all saved clarifications


@router.get("/{workspace_id}/analysis/gaps", summary="Get open gaps")
async def get_analysis_gaps(
    workspace_id: str,
    svc: SvcDep,
) -> list[Gap]:
    """Retrieve open gaps/questions from the latest analysis.

    Returns 5 types of gaps:
    - ambiguous-requirement (can clarify)
    - missing-kb-coverage (cannot clarify — KB gap)
    - conflict-unresolved (can clarify)
    - coverage-gap (can clarify)
    - scope-unclear (can clarify)
    """
    try:
        analysis = await svc.get(workspace_id)
    except ResourceNotFoundError:
        raise ResourceNotFoundError(f"No analysis for workspace {workspace_id}")

    if analysis is None:
        raise ResourceNotFoundError(f"No analysis for workspace {workspace_id}")

    gaps = detect_gaps(analysis)
    return gaps


@router.post("/{workspace_id}/clarifications", status_code=status.HTTP_201_CREATED, summary="Save clarification")
async def save_clarification(
    workspace_id: str,
    body: SaveClarificationRequest,
    ws_svc: WsSvcDep,
    persona: PersonaDep,
) -> Clarification:
    """Save user clarification for a gap.

    Types:
    - text: free-form text input (ambiguous-requirement, coverage-gap)
    - choice: selected option (conflict-unresolved, scope-unclear)
    - reference: KB ID references (missing-kb-coverage)
    """
    # Verify workspace exists
    ws = await ws_svc.get(workspace_id)
    if not ws:
        raise ResourceNotFoundError(f"Workspace {workspace_id} not found")

    # Validate gap_type
    valid_types = {
        "ambiguous-requirement",
        "missing-kb-coverage",
        "conflict-unresolved",
        "coverage-gap",
        "scope-unclear",
    }
    if body.gap_type not in valid_types:
        raise ValidationError(f"Invalid gap_type: {body.gap_type}")

    # Validate clarification_type
    if body.clarification_type not in {"text", "choice", "reference"}:
        raise ValidationError(f"Invalid clarification_type: {body.clarification_type}")

    clarification_id = f"clarif-{uuid.uuid4().hex[:12]}"

    # Insert into database
    clarif_repo = ClarificationRepository()
    result = await clarif_repo.create(
        clarification_id=clarification_id,
        workspace_id=workspace_id,
        gap_id=body.gap_id,
        gap_type=body.gap_type,
        user_input=str(body.user_input),
        clarification_type=body.clarification_type,
        analysis_iteration=1,
        created_by=persona,
    )

    if not result:
        raise ValidationError(f"Failed to save clarification {clarification_id}")

    return Clarification(
        clarification_id=result["clarification_id"],
        workspace_id=result["workspace_id"],
        analysis_iteration=result["analysis_iteration"],
        gap_id=result["gap_id"],
        gap_type=result["gap_type"],
        user_input=result["user_input"],
        clarification_type=result["clarification_type"],
        created_at=result["created_at"].isoformat() if result.get("created_at") else None,
        created_by=result.get("created_by"),
    )


@router.get("/{workspace_id}/analyze/rerun-preview", summary="Get merge preview")
async def get_rerun_preview(
    workspace_id: str,
    ws_svc: WsSvcDep,
) -> MergePreview:
    """Get preview of merged requirement before rerun.

    Shows:
    - Original requirement (iteration 1)
    - Merged requirement (iteration N)
    - Character count delta
    - Clarifications count
    """
    ws = await ws_svc.get(workspace_id)
    if not ws:
        raise ResourceNotFoundError(f"Workspace {workspace_id} not found")

    # Fetch saved clarifications from DB
    clarif_repo = ClarificationRepository()
    clarifications = await clarif_repo.get_by_workspace(workspace_id)

    if not clarifications:
        raise ValidationError("No clarifications saved. Please clarify some gaps first.")

    # Get current iteration
    req_repo = RequirementVersionRepository()
    latest_version = await req_repo.get_latest_version(workspace_id)
    current_iteration = latest_version["requirement_version"] if latest_version else 1
    new_iteration = current_iteration + 1

    # Merge clarifications
    merged_requirement = merge_clarifications_into_requirement(
        original_requirement=ws.requirement_text or "",
        clarifications=clarifications,
    )

    preview = build_merge_preview(
        original_requirement=ws.requirement_text or "",
        merged_requirement=merged_requirement,
        iteration=new_iteration,
        clarifications_count=len(clarifications),
    )

    return preview


@router.post("/{workspace_id}/analyze/rerun-with-clarifications", summary="Rerun with clarifications")
async def rerun_with_clarifications(
    workspace_id: str,
    body: RerunRequest,
    svc: SvcDep,
    ws_svc: WsSvcDep,
    persona: PersonaDep,
) -> AsyncIterator[dict[str, Any]]:
    """Rerun analysis with all saved clarifications merged into requirement.

    Flow:
    1. Fetch all saved clarifications
    2. Merge them into requirement (deterministic)
    3. Save as requirement_version v{n+1}
    4. Run analysis with merged requirement
    5. Detect gaps again (should be reduced)
    6. Stream progress + final gaps

    Yields:
    - {type: "preview", data: {iteration, original, merged, clarifications_count}}
    - {type: "status", data: {stage, detail}} — streaming progress
    - {type: "step", data: {tool, args}} — agent tool calls
    - {type: "result", data: ImpactAnalysis} — final analysis
    - {type: "gaps-iteration", data: {iteration, previous_count, current_count, gaps}}
    """
    ws = await ws_svc.get(workspace_id)
    if not ws:
        raise ResourceNotFoundError(f"Workspace {workspace_id} not found")

    # Fetch saved clarifications from DB
    clarif_repo = ClarificationRepository()
    clarifications = await clarif_repo.get_by_workspace(workspace_id)

    if not clarifications:
        raise ValidationError("No clarifications saved. Please clarify some gaps first.")

    # Get current iteration
    req_repo = RequirementVersionRepository()
    latest_version = await req_repo.get_latest_version(workspace_id)
    current_iteration = latest_version["requirement_version"] if latest_version else 1
    new_iteration = current_iteration + 1

    # Merge clarifications into requirement
    merged_requirement = merge_clarifications_into_requirement(
        original_requirement=ws.requirement_text or "",
        clarifications=clarifications,
    )

    # Yield rerun preview
    yield {
        "type": "preview",
        "data": {
            "iteration": new_iteration,
            "original_requirement": ws.requirement_text or "",
            "merged_requirement": merged_requirement,
            "clarifications_count": len(clarifications),
        },
    }

    # Save requirement version
    saved_version = await req_repo.create(
        workspace_id=workspace_id,
        requirement_version=new_iteration,
        requirement_text=merged_requirement,
        source="clarified",
        clarifications_applied=[c["clarification_id"] for c in clarifications],
        analysis_artifact_id=None,
    )

    if not saved_version:
        raise ValidationError(f"Failed to save requirement version v{new_iteration}")

    # Fetch previous gaps for comparison
    previous_analysis = await svc.get(workspace_id)
    previous_gaps = detect_gaps(previous_analysis) if previous_analysis else []
    previous_gap_count = len(previous_gaps)

    # Stream analysis progress
    analysis = None
    async for kind, payload in svc.analyze_stream(workspace_id, persona=persona):
        if kind == "status":
            yield {"type": "status", "data": payload}
        elif kind == "step":
            yield {"type": "step", "data": payload}
        elif kind == "result":
            analysis = payload
            yield {"type": "result", "data": {
                "workspace_id": payload.workspace_id,
                "classification": payload.classification.model_dump() if payload.classification else {},
                "narrative": payload.narrative,
                "scope": payload.scope.model_dump() if payload.scope else {},
                "matched": len(payload.matched),
                "affected": len(payload.affected),
            }}

    # Detect gaps for next iteration
    gaps = detect_gaps(analysis) if analysis else []
    current_gap_count = len(gaps)
    resolved_count = max(0, previous_gap_count - current_gap_count)

    # Yield iteration summary
    yield {
        "type": "gaps-iteration",
        "data": {
            "iteration": new_iteration,
            "previous_gap_count": previous_gap_count,
            "current_gap_count": current_gap_count,
            "resolved_count": resolved_count,
            "gaps": [
                {
                    "gap_id": g.gap_id,
                    "gap_type": g.gap_type,
                    "description": g.description,
                    "affected_ids": g.affected_ids,
                    "severity": g.severity,
                    "resolution_hint": g.resolution_hint,
                    "can_clarify": g.can_clarify,
                    "clarification_type": g.clarification_type,
                }
                for g in gaps
            ],
        },
    }
