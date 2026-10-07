"""Traceability view endpoints for offline work.

Unified view of all traceability links across stages:
  GET /ws/{id}/traceability — get all links (stories → dev → qa → KB)
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.core.dependencies import get_current_persona
from app.services.workspace_service import WorkspaceService
from app.utils.exceptions import ResourceNotFoundError, ValidationError
from app.utils.logging import log

router = APIRouter(prefix="/ws", tags=["traceability"])


# ── Request/Response Models ──────────────────────────────────────────────────

class TraceLink(BaseModel):
    """A traceability link."""

    from_id: str = Field(..., description="Source ID (artifact or story)")
    to_id: str = Field(..., description="Target KB card ID")
    link_type: str = Field(..., description="IMPLEMENTS | VALIDATES | GROUNDS | DERIVES_FROM")
    stage: str | None = Field(default=None, description="Stage where link was created")
    confidence: float = Field(default=0.0, description="Confidence score (0.0-1.0)")


class TraceabilityViewResponse(BaseModel):
    """Unified traceability view for all stages."""

    workspace_id: str
    stage: str = Field(..., description="Current workspace stage")
    story_links: list[TraceLink] = Field(default_factory=list, description="Stories → KB cards")
    dev_links: list[TraceLink] = Field(default_factory=list, description="Dev changes → KB cards")
    qa_links: list[TraceLink] = Field(default_factory=list, description="QA tests → KB cards")
    total_links: int = Field(default=0, description="Total link count")
    coverage: float = Field(default=0.0, description="KB coverage % (0.0-100.0)")
    summary: str = Field(default="", description="Human-readable summary")


# ── Dependencies ──────────────────────────────────────────────────────────────

async def get_workspace_service() -> WorkspaceService:
    """Inject WorkspaceService (TODO: wire from DI container)."""
    # TODO: Get from app context / DI container
    return WorkspaceService()


PersonaDep = Annotated[str, Depends(get_current_persona)]


# ── Endpoints ────────────────────────────────────────────────────────────────

@router.get(
    "/{workspace_id}/traceability",
    summary="Get unified traceability view (stories, dev, QA → KB)",
    response_model=TraceabilityViewResponse,
)
async def get_traceability(
    workspace_id: str,
    persona: PersonaDep,
    service: Annotated[WorkspaceService, Depends(get_workspace_service)],
) -> TraceabilityViewResponse:
    """Retrieve unified traceability view for the workspace.

    Shows all GROUNDS/IMPLEMENTS/VALIDATES links across stages:
      - Stories → KB cards (GROUNDS)
      - Dev changes → KB cards (IMPLEMENTS)
      - QA tests → KB cards (VALIDATES)

    Returns summary statistics:
      - Total links created
      - KB coverage % (linked / total KB cards referenced)

    Example response:
    {
      "workspace_id": "ws-abc123",
      "stage": "DEVELOPMENT",
      "story_links": [
        {
          "from_id": "STR-JAUTO-001",
          "to_id": "BR-JAUTO-042",
          "link_type": "GROUNDS",
          "stage": "STORIES",
          "confidence": 0.92
        },
        {
          "from_id": "STR-JAUTO-001",
          "to_id": "FR-JAUTO-101",
          "link_type": "GROUNDS",
          "stage": "STORIES",
          "confidence": 0.88
        }
      ],
      "dev_links": [
        {
          "from_id": "PolicyService",
          "to_id": "BR-JAUTO-042",
          "link_type": "IMPLEMENTS",
          "stage": "DEVELOPMENT",
          "confidence": 0.85
        }
      ],
      "qa_links": [
        {
          "from_id": "TC-JAUTO-AU-NB-001",
          "to_id": "BR-JAUTO-042",
          "link_type": "VALIDATES",
          "stage": "QA_TESTING",
          "confidence": 1.0
        }
      ],
      "total_links": 4,
      "coverage": 75.5,
      "summary": "4 total links across 3 stages. Stories: 2 links. Dev: 1 link. QA: 1 link."
    }

    Errors:
      - 404: Workspace not found
    """
    log.info(f"[api] GET /ws/{workspace_id}/traceability by {persona}")

    try:
        # Validate workspace exists
        ws = await service.get(workspace_id)
        if not ws:
            raise ResourceNotFoundError(f"Workspace not found: {workspace_id}", {"workspace_id": workspace_id})

        # TODO: Query fe_workspace_links/fe_workspace_grounds for all links
        # Group by: GROUNDS (stories), IMPLEMENTS (dev), VALIDATES (qa)
        # For now, return placeholder

        story_links = []
        dev_links = []
        qa_links = []
        total_links = 0
        coverage = 0.0

        summary = (
            f"{total_links} total links across {len([story_links, dev_links, qa_links])} stages. "
            f"Stories: {len(story_links)} links. Dev: {len(dev_links)} links. QA: {len(qa_links)} links."
        )

        return TraceabilityViewResponse(
            workspace_id=workspace_id,
            stage=ws.state.value if ws.state else "unknown",
            story_links=story_links,
            dev_links=dev_links,
            qa_links=qa_links,
            total_links=total_links,
            coverage=coverage,
            summary=summary,
        )
    except ResourceNotFoundError:
        raise
    except Exception as exc:  # noqa: BLE001
        log.error(f"[api] Get traceability failed for {workspace_id}: {exc}")
        raise ValidationError(
            f"Failed to get traceability: {str(exc)}", {"workspace_id": workspace_id}
        ) from exc
