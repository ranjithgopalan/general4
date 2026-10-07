"""Developer offline work submission endpoints.

Routes for submitting offline dev work:
  POST /ws/{id}/dev/submit — submit offline dev work (files, classes, KB links)
  GET  /ws/{id}/dev/progress — retrieve current dev work status
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.core.dependencies import get_current_persona, get_developer_offline_service as _get_dev_svc
from app.lifecycle.stages.developer.offline_service import DeveloperOfflineService, DeveloperOfflineSubmission
from app.utils.exceptions import ResourceNotFoundError, ValidationError
from app.utils.logging import log

router = APIRouter(prefix="/ws", tags=["dev_offline"])


# ── Request/Response Models ──────────────────────────────────────────────────

class DevWorkItemRequest(BaseModel):
    """A single dev work item: file/class change."""

    file_path: str = Field(..., description="File path (e.g., 'src/app/services/PolicyService.ts')")
    class_or_component: str = Field(..., description="Class/Component name (e.g., 'PolicyService')")
    change_type: str = Field(..., description="'CREATED' | 'MODIFIED' | 'DELETED'")
    description: str = Field(..., description="What changed (1-2 sentences)")
    kb_references: list[str] = Field(default_factory=list, description="KB IDs: ['BR-JAUTO-042', 'FR-JAUTO-101']")
    lines_changed: int = Field(default=0, description="Approximate lines added/modified")


class DevWorkSubmissionRequest(BaseModel):
    """Developer offline work submission."""

    status: str = Field(..., description="'IN_PROGRESS' | 'COMPLETED'")
    description: str = Field(..., description="Overall summary of dev work done")
    items: list[DevWorkItemRequest] = Field(default_factory=list, description="File/class changes")
    time_spent_minutes: int = Field(default=0, description="Estimated dev time")
    blockers: str | None = Field(default=None, description="Any blockers encountered")


class DevWorkSubmissionResponse(BaseModel):
    """Response from dev work submission."""

    workspace_id: str
    stage: str
    status: str
    items_recorded: int
    files_changed: int
    classes_modified: int
    lines_changed: int
    kb_links_created: int
    message: str


class DevProgressResponse(BaseModel):
    """Current dev work progress."""

    workspace_id: str
    stage: str
    status: str
    items: list[dict[str, Any]] = Field(default_factory=list)
    metrics: dict[str, int] = Field(default_factory=dict)


# ── Dependencies ──────────────────────────────────────────────────────────────

def get_dev_offline_service() -> DeveloperOfflineService:
    """DI provider: DeveloperOfflineService (singleton via lru_cache in dependencies)."""
    return _get_dev_svc()


PersonaDep = Annotated[str, Depends(get_current_persona)]


# ── Endpoints ────────────────────────────────────────────────────────────────

@router.post(
    "/{workspace_id}/dev/submit",
    summary="Submit offline developer work (files, classes, KB links)",
    response_model=DevWorkSubmissionResponse,
)
async def submit_dev_work(
    workspace_id: str,
    request: DevWorkSubmissionRequest,
    persona: PersonaDep,
    service: Annotated[DeveloperOfflineService, Depends(get_dev_offline_service)],
) -> DevWorkSubmissionResponse:
    """Submit offline developer work results.

    When handoff mode is enabled (FE_STORIES_HANDOFF_MODE_ENABLED=true):
      - Dev works offline locally (generates code, updates classes)
      - Posts results here with file paths, classes modified, KB links
      - Backend records artifacts, creates IMPLEMENTS links, updates metrics

    Request body:
    {
      "status": "IN_PROGRESS" | "COMPLETED",
      "description": "Summary of dev work",
      "items": [
        {
          "file_path": "src/app/services/PolicyService.ts",
          "class_or_component": "PolicyService",
          "change_type": "MODIFIED",
          "description": "Added get-policy-by-id method",
          "kb_references": ["BR-JAUTO-042", "FR-JAUTO-101"],
          "lines_changed": 45
        }
      ],
      "time_spent_minutes": 120,
      "blockers": null
    }

    Response:
    {
      "workspace_id": "ws-abc123",
      "stage": "DEVELOPMENT",
      "status": "IN_PROGRESS",
      "items_recorded": 1,
      "files_changed": 1,
      "classes_modified": 1,
      "lines_changed": 45,
      "kb_links_created": 2,
      "message": "Recorded 1 dev changes (1 files, 1 classes, 45 lines). Created 2 KB links (IMPLEMENTS)."
    }

    Errors:
      - 404: Workspace not found
      - 409: Invalid workspace state (not at STORIES for dev work)
    """
    log.info(f"[api] POST /ws/{workspace_id}/dev/submit by {persona}")

    # Convert request to service model
    submission = DeveloperOfflineSubmission(
        workspace_id=workspace_id,
        status=request.status,
        description=request.description,
        items=[dict(item) for item in request.items],
        time_spent_minutes=request.time_spent_minutes,
        blockers=request.blockers,
    )

    try:
        result = await service.submit_dev_work(workspace_id, submission, persona)
        return DevWorkSubmissionResponse(**result)
    except ResourceNotFoundError:
        raise
    except ValidationError:
        raise
    except Exception as exc:  # noqa: BLE001
        log.error(f"[api] Dev work submission failed for {workspace_id}: {exc}")
        raise ValidationError(
            f"Failed to submit dev work: {str(exc)}", {"workspace_id": workspace_id}
        ) from exc


@router.get(
    "/{workspace_id}/dev/progress",
    summary="Get current developer offline work progress",
    response_model=DevProgressResponse,
)
async def get_dev_progress(
    workspace_id: str,
    persona: PersonaDep,
    service: Annotated[DeveloperOfflineService, Depends(get_dev_offline_service)],
) -> DevProgressResponse:
    """Retrieve current dev work status for the workspace.

    Returns: current status (idle/in_progress/completed), items, metrics.

    Example response:
    {
      "workspace_id": "ws-abc123",
      "stage": "DEVELOPMENT",
      "status": "in_progress",
      "items": [
        {
          "file_path": "src/app/services/PolicyService.ts",
          "class_or_component": "PolicyService",
          "change_type": "MODIFIED",
          ...
        }
      ],
      "metrics": {
        "files_changed": 1,
        "classes_modified": 1,
        "lines_changed": 45,
        "kb_links_created": 2
      }
    }
    """
    log.info(f"[api] GET /ws/{workspace_id}/dev/progress by {persona}")

    try:
        progress = await service.get_dev_progress(workspace_id)
        return DevProgressResponse(**progress)
    except ResourceNotFoundError:
        raise
    except Exception as exc:  # noqa: BLE001
        log.error(f"[api] Get dev progress failed for {workspace_id}: {exc}")
        raise ValidationError(
            f"Failed to get dev progress: {str(exc)}", {"workspace_id": workspace_id}
        ) from exc
