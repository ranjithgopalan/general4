"""``/ws/{id}/qa`` — read + lifecycle for the QA stage (test plan + execution records).

Thin controllers over ``QAService``. Generation goes via ``POST /ws/{id}/generate``
(artifact_type="test-plan") via the fe.generate dispatcher. These routes:
  GET  /ws/{id}/qa                         — read persisted TestPlanDocument
  POST /ws/{id}/qa/accept                  — accept (→ PENDING_SYNC); gate: FAIL/BLOCKED block
  GET  /ws/{id}/qa/export.docx             — AIG .docx export
  POST /ws/{id}/qa/execute                 — append a TestExecution record

Optional ``override_justification`` query param on /accept allows QA to proceed
despite FAIL/BLOCKED executions when an approved justification is provided.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response

from app.core.dependencies import get_current_persona, get_qa_service
from app.lifecycle.stages.qa.handler import QAService
from app.lifecycle.stages.qa.schema import TestPlanDocument
from app.models.workspace import Workspace
from app.utils.exceptions import ResourceNotFoundError

router = APIRouter(prefix="/ws", tags=["qa"])

SvcDep = Annotated[QAService, Depends(get_qa_service)]
PersonaDep = Annotated[str, Depends(get_current_persona)]

_DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


@router.get("/{workspace_id}/qa", summary="Get the latest QA test plan")
async def get_test_plan(workspace_id: str, svc: SvcDep) -> TestPlanDocument:
    plan = await svc.get(workspace_id)
    if plan is None:
        raise ResourceNotFoundError(
            f"no QA test plan for {workspace_id}", {"workspace_id": workspace_id}
        )
    return plan


@router.post("/{workspace_id}/qa/accept", summary="Accept the QA test plan → advance to PENDING_SYNC")
async def accept_test_plan(
    workspace_id: str,
    svc: SvcDep,
    persona: PersonaDep,
    override_justification: Annotated[str | None, Query(
        description="Required when FAIL/BLOCKED executions exist — records why QA accepts despite failures"
    )] = None,
) -> Workspace:
    return await svc.accept(
        workspace_id,
        persona=persona,
        override_justification=override_justification,
    )


@router.get(
    "/{workspace_id}/qa/export.docx",
    summary="Export the QA test plan as an official AIG .docx",
)
async def export_qa_docx(workspace_id: str, svc: SvcDep) -> Response:
    data = await svc.export_docx(workspace_id)
    return Response(
        content=data,
        media_type=_DOCX_MIME,
        headers={"Content-Disposition": f'attachment; filename="test-plan-{workspace_id}.docx"'},
    )


@router.post(
    "/{workspace_id}/qa/execute",
    summary="Record a test execution result for a specific test case",
)
async def record_execution(
    workspace_id: str,
    svc: SvcDep,
    persona: PersonaDep,
    test_id: Annotated[str, Query(description="TestCase.id (e.g. TC-STR-JAUTO-001-01)")],
    status: Annotated[str, Query(description="TODO | PASS | FAIL | BLOCKED | SKIP")],
    notes: Annotated[str, Query(description="Test notes or failure description")] = "",
    override_justification: Annotated[str | None, Query(
        description="Required when status=FAIL|BLOCKED if QA intends to accept despite failure"
    )] = None,
    executed_by: Annotated[str | None, Query(description="QA engineer name or ID")] = None,
) -> TestPlanDocument:
    return await svc.record_execution(
        workspace_id,
        persona=persona,
        test_id=test_id,
        status=status,
        notes=notes,
        override_justification=override_justification,
        executed_by=executed_by,
    )
