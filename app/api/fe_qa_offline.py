"""QA offline work submission endpoints.

Routes for submitting offline QA work:
  POST /ws/{id}/qa/submit — submit offline QA test results (test cases, pass/fail, KB links)
  GET  /ws/{id}/qa/progress — retrieve current QA work status
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.core.dependencies import get_current_persona, get_qa_offline_service as _get_qa_svc
from app.lifecycle.stages.qa.offline_service import QAOfflineService, OfflineQASubmission
from app.utils.exceptions import ResourceNotFoundError, ValidationError
from app.utils.logging import log

router = APIRouter(prefix="/ws", tags=["qa_offline"])


# ── Request/Response Models ──────────────────────────────────────────────────

class QATestCaseRequest(BaseModel):
    """A single QA test case."""

    test_id: str = Field(..., description="Test ID (e.g., 'TC-JAUTO-AU-NB-001')")
    scenario: str = Field(..., description="Test scenario / title")
    status: str = Field(..., description="'PASS' | 'FAIL' | 'BLOCKED'")
    tested_components: list[str] = Field(
        default_factory=list, description="Components tested: ['GetPolicyComponent', 'PolicyService']"
    )
    tested_stories: list[str] = Field(default_factory=list, description="Stories covered: ['STR-JAUTO-001']")
    notes: str = Field(default="", description="Test notes / failure reason")
    kb_references: list[str] = Field(default_factory=list, description="KB rules tested: ['BR-JAUTO-042']")


class QAWorkSubmissionRequest(BaseModel):
    """QA offline work submission."""

    status: str = Field(..., description="'IN_PROGRESS' | 'COMPLETED'")
    summary: str = Field(default="", description="Overall QA summary (preferred field)")
    description: str = Field(default="", description="Overall QA summary (alias for summary, kept for backward compat)")
    test_cases: list[QATestCaseRequest] = Field(default_factory=list, description="Test cases executed")
    overall_pass_rate: float | None = Field(default=None, description="% tests passed (0-100)")
    pass_rate: float = Field(default=0.0, description="% tests passed (0.0-1.0, legacy)")
    blockers: str | None = Field(default=None, description="Blockers / showstoppers")

    @property
    def effective_summary(self) -> str:
        return self.summary or self.description


class QAWorkSubmissionResponse(BaseModel):
    """Response from QA work submission."""

    workspace_id: str
    stage: str
    status: str
    test_cases_recorded: int
    pass_count: int
    fail_count: int
    blocked_count: int
    pass_rate: float
    kb_links_created: int
    message: str


class QAProgressResponse(BaseModel):
    """Current QA work progress."""

    workspace_id: str
    stage: str
    status: str
    test_cases: list[dict[str, Any]] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)


# ── Dependencies ──────────────────────────────────────────────────────────────

def _get_qa_offline_service() -> QAOfflineService:
    """DI shim — delegates to the central lru_cache provider in dependencies.py."""
    return _get_qa_svc()


PersonaDep = Annotated[str, Depends(get_current_persona)]


# ── Endpoints ────────────────────────────────────────────────────────────────

@router.post(
    "/{workspace_id}/qa/submit",
    summary="Submit offline QA test results (test cases, pass/fail, KB links)",
    response_model=QAWorkSubmissionResponse,
)
async def submit_qa_work(
    workspace_id: str,
    request: QAWorkSubmissionRequest,
    persona: PersonaDep,
    service: Annotated[QAOfflineService, Depends(_get_qa_offline_service)],
) -> QAWorkSubmissionResponse:
    """Submit offline QA work results.

    When handoff mode is enabled (FE_STORIES_HANDOFF_MODE_ENABLED=true):
      - QA works offline locally (executes tests, validates rules)
      - Posts results here with test cases, pass/fail status, KB links
      - Backend records artifacts, creates VALIDATES links, updates metrics

    Request body:
    {
      "status": "IN_PROGRESS" | "COMPLETED",
      "description": "Completed manual testing of AU New Business flow",
      "test_cases": [
        {
          "test_id": "TC-JAUTO-AU-NB-001",
          "scenario": "User creates new policy with valid data",
          "status": "PASS",
          "tested_components": ["GetPolicyComponent", "PolicyService"],
          "tested_stories": ["STR-JAUTO-001", "STR-JAUTO-002"],
          "notes": "All validations passed",
          "kb_references": ["BR-JAUTO-042", "FR-JAUTO-101"]
        },
        {
          "test_id": "TC-JAUTO-AU-NB-002",
          "scenario": "User creates policy with invalid coverage type",
          "status": "FAIL",
          "tested_components": ["GetPolicyComponent"],
          "tested_stories": ["STR-JAUTO-001"],
          "notes": "System allowed invalid coverage type (bug in validation)",
          "kb_references": ["BR-JAUTO-043"]
        }
      ],
      "pass_rate": 0.85,
      "blockers": "One validation rule not working correctly"
    }

    Response:
    {
      "workspace_id": "ws-abc123",
      "stage": "QA_TESTING",
      "status": "IN_PROGRESS",
      "test_cases_recorded": 2,
      "pass_count": 1,
      "fail_count": 1,
      "blocked_count": 0,
      "pass_rate": 0.85,
      "kb_links_created": 3,
      "message": "Recorded 2 test cases (1 passed, 1 failed, 0 blocked). Pass rate: 85.0%. Created 3 KB links (VALIDATES)."
    }

    Errors:
      - 404: Workspace not found
      - 409: Invalid workspace state (expected DEVELOPMENT for QA)
    """
    log.info(f"[api] POST /ws/{workspace_id}/qa/submit by {persona}")

    # Convert request to service model
    pass_rate = request.overall_pass_rate / 100.0 if request.overall_pass_rate is not None else request.pass_rate
    submission = OfflineQASubmission(
        workspace_id=workspace_id,
        status=request.status,
        description=request.effective_summary,
        test_cases=[dict(tc) for tc in request.test_cases],
        pass_rate=pass_rate,
        blockers=request.blockers,
    )

    try:
        result = await service.submit_qa_work(workspace_id, submission, persona)
        return QAWorkSubmissionResponse(**result)
    except ResourceNotFoundError:
        raise
    except ValidationError:
        raise
    except Exception as exc:  # noqa: BLE001
        log.error(f"[api] QA work submission failed for {workspace_id}: {exc}")
        raise ValidationError(
            f"Failed to submit QA work: {str(exc)}", {"workspace_id": workspace_id}
        ) from exc


@router.get(
    "/{workspace_id}/qa/progress",
    summary="Get current QA offline work progress",
    response_model=QAProgressResponse,
)
async def get_qa_progress(
    workspace_id: str,
    persona: PersonaDep,
    service: Annotated[QAOfflineService, Depends(_get_qa_offline_service)],
) -> QAProgressResponse:
    """Retrieve current QA work status for the workspace.

    Returns: current status (idle/in_progress/completed), test cases, metrics.

    Example response:
    {
      "workspace_id": "ws-abc123",
      "stage": "QA_TESTING",
      "status": "in_progress",
      "test_cases": [
        {
          "test_id": "TC-JAUTO-AU-NB-001",
          "scenario": "User creates new policy with valid data",
          "status": "PASS",
          "tested_components": ["GetPolicyComponent"],
          "kb_references": ["BR-JAUTO-042"]
          ...
        }
      ],
      "metrics": {
        "total_tests": 5,
        "pass_count": 4,
        "fail_count": 1,
        "pass_rate": 0.8,
        "kb_links_created": 7
      }
    }
    """
    log.info(f"[api] GET /ws/{workspace_id}/qa/progress by {persona}")

    try:
        progress = await service.get_qa_progress(workspace_id)
        return QAProgressResponse(**progress)
    except ResourceNotFoundError:
        raise
    except Exception as exc:  # noqa: BLE001
        log.error(f"[api] Get QA progress failed for {workspace_id}: {exc}")
        raise ValidationError(
            f"Failed to get QA progress: {str(exc)}", {"workspace_id": workspace_id}
        ) from exc
