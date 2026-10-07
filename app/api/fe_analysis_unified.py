"""API endpoints for workspace analysis (systems, scope, metrics unified)."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core.dependencies import get_analysis_dao
from app.lifecycle.traceability.analysis_dao import WorkspaceAnalysisDAO
from app.utils.logging import log

router = APIRouter(tags=["workspace-analysis"])


# ── Request/Response Models ───────────────────────────────────────────────

class SystemItem(BaseModel):
    """A system affected by workspace changes."""
    system_id: str
    system_name: str
    impact_level: str = "medium"
    components_changed: list[str] = []
    rationale: str | None = None
    confidence: float = 0.85


class ScopeItem(BaseModel):
    """A scope categorization item."""
    item_id: str
    item_type: str | None = None
    item_description: str
    scope_category: str  # IN_SCOPE | OUT_OF_SCOPE | DEFERRED
    rationale: str | None = None
    priority: str | None = None


class AnalysisResponse(BaseModel):
    """Complete workspace analysis data."""
    workspace_id: str
    systems: list[SystemItem] = []
    scope: dict = {"in_scope": [], "out_of_scope": [], "deferred": []}
    metrics: dict = {}


# ── Endpoints ──────────────────────────────────────────────────────────

@router.get("/ws/{workspace_id}/analysis")
async def get_analysis(
    workspace_id: str,
    dao: WorkspaceAnalysisDAO = Depends(get_analysis_dao),
) -> AnalysisResponse:
    """Get complete analysis (systems + scope + metrics)."""
    try:
        analysis = await dao.get(workspace_id)

        if not analysis:
            # Return empty analysis if none exists yet
            analysis = {
                "workspace_id": workspace_id,
                "systems_extracted": [],
                "scope_items": {"in_scope": [], "out_of_scope": [], "deferred": []},
                "metrics": {},
            }

        return AnalysisResponse(
            workspace_id=workspace_id,
            systems=analysis.get("systems_extracted", []),
            scope=analysis.get("scope_items", {}),
            metrics=analysis.get("metrics", {}),
        )
    except Exception as e:
        log.error(f"[api] Failed to get analysis for {workspace_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to get analysis") from e


# ── Systems Endpoints ──────────────────────────────────────────────────

@router.get("/ws/{workspace_id}/analysis/systems")
async def get_systems(
    workspace_id: str,
    dao: WorkspaceAnalysisDAO = Depends(get_analysis_dao),
) -> dict:
    """Get systems extracted for workspace."""
    try:
        systems = await dao.get_systems(workspace_id)
        return {
            "workspace_id": workspace_id,
            "count": len(systems),
            "systems": systems,
        }
    except Exception as e:
        log.error(f"[api] Failed to get systems: {e}")
        raise HTTPException(status_code=500, detail="Failed to get systems") from e


@router.post("/ws/{workspace_id}/analysis/systems")
async def update_systems(
    workspace_id: str,
    systems: list[dict],
    dao: WorkspaceAnalysisDAO = Depends(get_analysis_dao),
) -> dict:
    """Update systems list."""
    try:
        analysis = await dao.update_systems(workspace_id, systems)
        if not analysis:
            raise HTTPException(status_code=500, detail="Failed to update systems")
        return {
            "workspace_id": workspace_id,
            "status": "updated",
            "count": len(systems),
        }
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"[api] Failed to update systems: {e}")
        raise HTTPException(status_code=500, detail="Failed to update systems") from e


# ── Scope Endpoints ────────────────────────────────────────────────────

@router.get("/ws/{workspace_id}/analysis/scope")
async def get_scope(
    workspace_id: str,
    dao: WorkspaceAnalysisDAO = Depends(get_analysis_dao),
) -> dict:
    """Get scope items (IN_SCOPE | OUT_OF_SCOPE | DEFERRED)."""
    try:
        scope = await dao.get_scope(workspace_id)
        total = (
            len(scope.get("in_scope", []))
            + len(scope.get("out_of_scope", []))
            + len(scope.get("deferred", []))
        )
        return {
            "workspace_id": workspace_id,
            "count": total,
            "in_scope": scope.get("in_scope", []),
            "out_of_scope": scope.get("out_of_scope", []),
            "deferred": scope.get("deferred", []),
        }
    except Exception as e:
        log.error(f"[api] Failed to get scope: {e}")
        raise HTTPException(status_code=500, detail="Failed to get scope") from e


@router.post("/ws/{workspace_id}/analysis/scope")
async def update_scope(
    workspace_id: str,
    scope: dict,
    dao: WorkspaceAnalysisDAO = Depends(get_analysis_dao),
) -> dict:
    """Update scope items."""
    try:
        analysis = await dao.update_scope(workspace_id, scope)
        if not analysis:
            raise HTTPException(status_code=500, detail="Failed to update scope")

        total = (
            len(scope.get("in_scope", []))
            + len(scope.get("out_of_scope", []))
            + len(scope.get("deferred", []))
        )
        return {
            "workspace_id": workspace_id,
            "status": "updated",
            "count": total,
        }
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"[api] Failed to update scope: {e}")
        raise HTTPException(status_code=500, detail="Failed to update scope") from e


# ── Metrics Endpoints ──────────────────────────────────────────────────

@router.get("/ws/{workspace_id}/analysis/metrics")
async def get_metrics(
    workspace_id: str,
    dao: WorkspaceAnalysisDAO = Depends(get_analysis_dao),
) -> dict:
    """Get coverage metrics."""
    try:
        metrics = await dao.get_metrics(workspace_id)
        return {
            "workspace_id": workspace_id,
            "metrics": metrics,
        }
    except Exception as e:
        log.error(f"[api] Failed to get metrics: {e}")
        raise HTTPException(status_code=500, detail="Failed to get metrics") from e


@router.post("/ws/{workspace_id}/analysis/metrics")
async def update_metrics(
    workspace_id: str,
    metrics: dict,
    dao: WorkspaceAnalysisDAO = Depends(get_analysis_dao),
) -> dict:
    """Update metrics."""
    try:
        analysis = await dao.update_metrics(workspace_id, metrics)
        if not analysis:
            raise HTTPException(status_code=500, detail="Failed to update metrics")
        return {
            "workspace_id": workspace_id,
            "status": "updated",
            "metrics": metrics,
        }
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"[api] Failed to update metrics: {e}")
        raise HTTPException(status_code=500, detail="Failed to update metrics") from e
