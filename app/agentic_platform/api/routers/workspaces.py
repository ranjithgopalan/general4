"""Workspace instances: one Global, one Mini per EPIC (PRD 5.3, FR-P4).

The UI needs these endpoints to answer the question the two-tier model creates:
"which EPIC am I looking at?". Every artefact, run and readiness view is scoped
by workspace, so without this the console cannot address anything below the
Global tier.

`POST /fan-out` exists as a recovery path. Fan-out normally happens
automatically when the EPIC set is approved, but that runs *after* the approval
is committed and is deliberately allowed to fail without undoing it -- an
approval is a human decision and must stand. This endpoint re-runs it.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.agentic_platform.api.security import current_principal
from app.agentic_platform.api.services.jobs import JobService
from app.agentic_platform.fe_core.auth.roles import Principal
from app.agentic_platform.fe_core.config import get_settings
from app.agentic_platform.fe_core.kb.models import ArtifactStatus
from app.agentic_platform.fe_core.pipeline.eligibility import pipeline_status
from app.agentic_platform.fe_core.pipeline.registry import (
    PipelineNotFoundError,
    get_pipeline,
    registry,
)
from app.agentic_platform.fe_core.store import get_store
from app.agentic_platform.fe_core.workspaces.service import WorkspaceService, extract_epics

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/workspaces", tags=["workspaces"])


def _service(tier: str = "global") -> WorkspaceService:
    settings = get_settings()
    if tier == "mini":
        name = settings.fe_pipeline_mini
    elif tier == "architecture":
        name = getattr(settings, "fe_pipeline_architecture", "uw-cr-architecture")
    else:
        name = settings.fe_pipeline_global
    return WorkspaceService(get_store(), name)


def _describe(workspace, *, include_progress: bool = False) -> dict:
    row = {
        "id": workspace.id,
        "label": workspace.label,
        "tier": workspace.tier.value,
        "kb_application_id": workspace.kb_application_id,
        "pipeline": workspace.pipeline,
        "epic_id": workspace.epic_id,
        "epic_title": workspace.epic_title,
        "status": workspace.status.value,
        "parent_workspace_id": workspace.parent_workspace_id,
        "created_from_artifact_id": workspace.created_from_artifact_id,
        "opened_at": workspace.opened_at.isoformat() if workspace.opened_at else None,
        "closed_at": workspace.closed_at.isoformat() if workspace.closed_at else None,
        "gear_id": getattr(workspace, "gear_id", None),
        "intake_source": getattr(workspace, "intake_source", None),
    }
    if not include_progress:
        return row

    store = get_store()
    try:
        pipeline = get_pipeline(workspace.pipeline)
    except PipelineNotFoundError:
        return row
    rows = pipeline_status(store, pipeline, workspace.id)
    row["progress"] = {
        "stages": len(rows),
        "completed": sum(1 for r in rows if r["state"] == "completed"),
        "waiting_for_approval": sum(
            1 for r in rows if r["state"] == "waiting_for_approval"),
        "ready": [r["key"] for r in rows if r["ready"]],
        "gaps": sum(1 for r in rows if r["capability_status"] == "gap"),
    }
    return row


@router.get("")
async def list_workspaces(
    kb_application_id: str | None = None,
    tier: str | None = Query(default=None, pattern="^(global|mini|architecture)$"),
    status: str | None = Query(default=None, pattern="^(open|closed|cancelled)$"),
    include_progress: bool = False,
    principal: Principal = Depends(current_principal),
) -> dict:
    """Every workspace, Global first. `include_progress` adds per-stage counts."""
    rows = get_store().list_workspaces(kb_application_id, tier=tier, status=status)
    described = [_describe(w, include_progress=include_progress) for w in rows]
    return {
        "count": len(described),
        "global": [r for r in described if r["tier"] == "global"],
        "architecture": [r for r in described if r["tier"] == "architecture"],
        "mini": [r for r in described if r["tier"] == "mini"],
        "items": described,
    }


@router.get("/{workspace_id}")
async def get_workspace(
    workspace_id: str,
    principal: Principal = Depends(current_principal),
) -> dict:
    workspace = get_store().get_workspace(workspace_id)
    if workspace is None:
        raise HTTPException(status_code=404, detail=f"unknown workspace: {workspace_id}")
    return _describe(workspace, include_progress=True)


class WorkspaceMetaPatch(BaseModel):
    intake_source: str | None = Field(
        default=None,
        description="Intake source for this workspace (e.g. 're-graph', 'reverse-engineering', 'requirements').",
    )
    gear_id: str | None = Field(
        default=None,
        description="RE Graph gear identifier (e.g. '1429'). Required for RE Graph flow.",
    )
    category: str | None = Field(
        default=None,
        description="Project category: 'greenfield' or 'brownfield'.",
    )
    source_language: str | None = Field(
        default=None,
        description="Source language (e.g. 'vb6-vba', 'asp-vbscript').",
    )
    target_framework: str | None = Field(
        default=None,
        description="Target framework (e.g. 'angular-springboot').",
    )


@router.patch("/{workspace_id}/meta")
async def patch_workspace_meta(
    workspace_id: str,
    body: WorkspaceMetaPatch = Body(...),
    principal: Principal = Depends(current_principal),
) -> dict:
    """Update intake-related metadata fields on a workspace without re-onboarding.

    Only fields present in the request body (non-null) are updated; omitted
    fields are left unchanged. Useful when a workspace was created before a
    field was introduced (e.g. setting intake_source='re-graph' on an existing
    RE Engineering workspace).
    """
    store = get_store()
    workspace = store.get_workspace(workspace_id)
    if workspace is None:
        raise HTTPException(status_code=404, detail=f"unknown workspace: {workspace_id}")

    updates: dict = {}
    if body.intake_source is not None:
        updates["intake_source"] = body.intake_source
    if body.gear_id is not None:
        updates["gear_id"] = body.gear_id
    if body.category is not None:
        updates["category"] = body.category
    if body.source_language is not None:
        updates["source_language"] = body.source_language
    if body.target_framework is not None:
        updates["target_framework"] = body.target_framework

    if not updates:
        return _describe(workspace, include_progress=False)

    updated = workspace.model_copy(update=updates)
    store.put_workspace(updated)
    logger.info("workspace %s meta updated: %s", workspace_id, updates)
    return _describe(updated, include_progress=False)


@router.get("/{workspace_id}/traceability")
async def workspace_traceability(
    workspace_id: str,
    workspace_ids: str | None = Query(
        None,
        description=(
            "Comma-separated list of ALL workspace IDs to include in this traceability view "
            "(global, architecture, each mini/EPIC, developer, tester). "
            "When supplied the backend queries exactly those IDs instead of guessing "
            "sibling names by convention.  The caller (UI) knows the full list from the "
            "workspace store.  workspace_id is always included regardless."
        ),
    ),
    principal: Principal = Depends(current_principal),
) -> dict:
    """Full traceability data for a workspace — powers the 6-tab Traceability page.

    Returns:
      - artifacts:         artifact metadata from all related workspaces
      - relationships:     chain links from fe_artifact_relationships
                           (auto-written by stage_executor._record_lineage)
      - kb_grounding:      GROUNDS edges per KB card with coverage counts
      - coverage_matrix:   per-(kb_card, stage) grounding link counts

    Pass ``workspace_ids`` (comma-separated) from the UI workspace store so that
    global, architecture, developer and tester workspaces are all included.
    When omitted the endpoint falls back to the old convention-based derivation.
    """
    import asyncio as _asyncio  # noqa: PLC0415
    from app.dao.clarification_dao import ArtifactTraceabilityDAO  # noqa: PLC0415

    # Emit a fire-and-forget telemetry event for page-view tracking.
    try:
        from app.services.telemetry import get_telemetry_service  # noqa: PLC0415
        get_telemetry_service().emit(
            "traceability_page_viewed", {"workspace_id": workspace_id}
        )
    except Exception:
        pass  # telemetry is non-fatal

    # Build the full list of workspace IDs to query.
    # Prefer the explicit list from the UI (reliable); fall back to convention derivation.
    if workspace_ids:
        # Deduplicate while preserving order; always include the primary workspace.
        seen: set[str] = set()
        related_workspace_ids: list[str] = []
        for wid in [workspace_id] + [w.strip() for w in workspace_ids.split(",")]:
            if wid and wid not in seen:
                seen.add(wid)
                related_workspace_ids.append(wid)
    else:
        # Legacy fallback: derive siblings by name convention.
        related_workspace_ids = [
            workspace_id,
            f"{workspace_id}--architecture",
            f"{workspace_id}--developer",
            f"{workspace_id}--tester",
        ]

    dao = ArtifactTraceabilityDAO()
    artifacts, relationships, kb_grounding, coverage_matrix = await _asyncio.gather(
        dao.get_artifacts(related_workspace_ids),
        dao.get_relationship_chain(related_workspace_ids),
        dao.get_kb_grounding(related_workspace_ids),
        dao.get_coverage_matrix(related_workspace_ids),
    )

    return {
        "workspace_id":    workspace_id,
        "artifacts":       artifacts,
        "relationships":   relationships,
        "kb_grounding":    kb_grounding,
        "coverage_matrix": coverage_matrix,
    }


@router.get("/{workspace_id}/stages")
async def workspace_stages(
    workspace_id: str,
    principal: Principal = Depends(current_principal),
) -> dict:
    """Stage readiness *for this workspace*.

    Scoped deliberately: an unscoped Mini view would mix ten EPICs' states into
    one column and read as nonsense.
    """
    store = get_store()
    workspace = store.get_workspace(workspace_id)
    if workspace is None:
        raise HTTPException(status_code=404, detail=f"unknown workspace: {workspace_id}")
    try:
        pipeline = get_pipeline(workspace.pipeline)
    except PipelineNotFoundError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    rows = pipeline_status(store, pipeline, workspace.id)
    return {
        "workspace": _describe(workspace),
        "pipeline": pipeline.name,
        "tier": pipeline.tier,
        "title": pipeline.title,
        "summary": {
            "total": len(rows),
            "ready": sum(1 for r in rows if r["ready"]),
            "gaps": sum(1 for r in rows if r["capability_status"] == "gap"),
            "completed": sum(1 for r in rows if r["state"] == "completed"),
            "waiting_for_approval": sum(
                1 for r in rows if r["state"] == "waiting_for_approval"),
        },
        "stages": rows,
    }


class EnsureGlobalRequest(BaseModel):
    kb_application_id: str = Field(description="KB application id.")
    pipeline: str | None = None


@router.post("/global", status_code=200)
async def ensure_global(
    body: EnsureGlobalRequest = Body(...),
    principal: Principal = Depends(current_principal),
) -> dict:
    """Open the Global Workspace, or return the existing one.

    Idempotent because the id is derived from the application (FR-P4: exactly
    one per programme), so a concurrent call cannot create a second.
    """
    workspace = _service().ensure_global(body.kb_application_id, body.pipeline)
    return _describe(workspace, include_progress=True)


class EnsureArchitectureRequest(BaseModel):
    kb_application_id: str = Field(description="KB application id.")
    pipeline: str | None = None


class EnsureAssemblerRequest(BaseModel):
    kb_application_id: str = Field(description="KB application id.")


@router.post("/assembler", status_code=200)
async def ensure_assembler(
    body: EnsureAssemblerRequest = Body(...),
    principal: Principal = Depends(current_principal),
) -> dict:
    """Open the Epic Assembler Workspace, or return the existing one.

    Idempotent. Normally triggered automatically when all per-EPIC Mini
    Workspaces complete their last stage. Use this endpoint to open it
    manually (recovery path or testing).
    """
    workspace = _service().ensure_assembler(body.kb_application_id)
    return _describe(workspace, include_progress=True)


@router.post("/architecture", status_code=200)
async def ensure_architecture(
    body: EnsureArchitectureRequest = Body(...),
    principal: Principal = Depends(current_principal),
) -> dict:
    """Open the Architecture Workspace, or return the existing one.

    Idempotent. The Architecture Workspace is a singleton per programme
    (id = ``{app}--architecture``); the orchestrator also creates it lazily at
    arch_sync_gate after FRD is approved.
    """
    workspace = _service("architecture").ensure_architecture(
        body.kb_application_id, body.pipeline)
    return _describe(workspace, include_progress=True)


class FanOutRequest(BaseModel):
    artifact_id: str = Field(
        description="An APPROVED EPIC-set artefact. A Draft is refused.")
    pipeline: str | None = Field(
        default=None, description="Mini pipeline name. Defaults to FE_PIPELINE_MINI.")


@router.post("/fan-out", status_code=201)
async def fan_out(
    body: FanOutRequest = Body(...),
    principal: Principal = Depends(current_principal),
) -> dict:
    """Open one Mini Workspace per EPIC from an approved EPIC set (FR-P4).

    Normally automatic on approval; this is the recovery path for when that
    post-approval step failed. Idempotent, so calling it twice is safe.
    """
    store = get_store()
    artifact = store.get_artifact(body.artifact_id)
    if artifact is None:
        raise HTTPException(status_code=404,
                            detail=f"unknown artefact: {body.artifact_id}")
    if artifact.status is not ArtifactStatus.APPROVED:
        raise HTTPException(
            status_code=409,
            detail=(
                f"artefact {artifact.id} is {artifact.status.value}, not APPROVED. "
                "Fan-out is triggered by approval: opening workspaces for EPICs "
                "the Product Owner has not accepted would strand work if the set "
                "is rejected."
            ),
        )

    settings = get_settings()
    mini = body.pipeline or settings.fe_pipeline_mini
    epics = extract_epics(artifact)
    opened = _service("mini").fan_out(artifact, pipeline=mini)
    if not opened:
        raise HTTPException(
            status_code=422,
            detail=(
                f"no EPICs could be read from {artifact.path}. Expected a JSON "
                "file with an `epics` array, or Markdown headings of the form "
                "'## EPIC-1: Title'."
            ),
        )
    return {
        "artifact_id": artifact.id,
        "epics_found": len(epics),
        "count": len(opened),
        "items": [_describe(w) for w in opened],
    }


@router.post("/{workspace_id}/close")
async def close_workspace(
    workspace_id: str,
    cancelled: bool = Query(
        default=False,
        description="true when the EPIC was dropped rather than accepted."),
    principal: Principal = Depends(current_principal),
) -> dict:
    """Close a workspace. A cancelled EPIC stays distinguishable from a finished
    one, because an audit must not read them the same way."""
    workspace = _service().close(workspace_id, cancelled=cancelled)
    if workspace is None:
        raise HTTPException(status_code=404, detail=f"unknown workspace: {workspace_id}")
    return _describe(workspace)


@router.get("/{workspace_id}/personas")
async def workspace_personas(
    workspace_id: str,
    principal: Principal = Depends(current_principal),
) -> dict:
    """The eight personas and what each owns in this workspace (PRD 5.1).

    Driven off the pipeline rather than a hardcoded list, so adding a stage
    updates this automatically (FR-018).
    """
    from app.agentic_platform.fe_core.auth.personas import PERSONA_APPROVES, PERSONA_SCOPE, Persona

    store = get_store()
    workspace = store.get_workspace(workspace_id)
    if workspace is None:
        raise HTTPException(status_code=404, detail=f"unknown workspace: {workspace_id}")
    try:
        pipeline = get_pipeline(workspace.pipeline)
    except PipelineNotFoundError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    items = []
    for persona in Persona:
        approves = [s.key for s in pipeline.ordered
                    if s.approval_persona == persona.value]
        contributes = [s.key for s in pipeline.ordered
                       if persona.value in s.contributing_personas]
        items.append({
            "persona": persona.value,
            "access_scope": PERSONA_SCOPE[persona].value,
            "can_see_code": PERSONA_SCOPE[persona].value != "global",
            "approves_tiers": sorted(t.value for t in PERSONA_APPROVES[persona]),
            "approves_stages": approves,
            "contributes_to_stages": contributes,
        })
    return {
        "workspace": _describe(workspace),
        "count": len(items),
        "note": "All eight personas join every workspace (PRD 5.3).",
        "items": items,
    }


# --- orchestration (FE_ORCHESTRATOR=langgraph) ------------------------------------
@router.get("/{workspace_id}/orchestration")
async def orchestration_status(workspace_id: str) -> dict:
    """Where the workspace's graph thread is: running | waiting (persona) | handed_off | completed | failed."""
    from app.agentic_platform.fe_core.store import get_store  # noqa: PLC0415
    ws = get_store().get_workspace(workspace_id)
    if ws is None:
        raise HTTPException(status_code=404, detail=f"unknown workspace: {workspace_id}")
    from app.agentic_platform.fe_core.config import get_settings  # noqa: PLC0415
    info = dict(ws.orchestration or {}); info.setdefault("status", "idle")
    info["workspace_id"] = workspace_id
    info["mode"] = getattr(get_settings(), "fe_orchestrator", "manual")
    return info


@router.post("/{workspace_id}/orchestrate", status_code=202)
async def orchestrate(workspace_id: str, initiated_by: str | None = None) -> dict:
    """Ask the worker to start (or restart) the workspace's graph thread.

    Recorded as an `orchestrate` intent on the workspace; the worker picks it up
    on its next poll. In manual mode this returns 409.
    """
    from app.agentic_platform.fe_core.config import get_settings  # noqa: PLC0415
    from app.agentic_platform.fe_core.store import get_store  # noqa: PLC0415
    if getattr(get_settings(), "fe_orchestrator", "manual") != "langgraph":
        raise HTTPException(status_code=409, detail="FE_ORCHESTRATOR is 'manual'; start stages via POST /pipeline-runs")
    store = get_store()
    ws = store.get_workspace(workspace_id)
    if ws is None:
        raise HTTPException(status_code=404, detail=f"unknown workspace: {workspace_id}")
    info = dict(ws.orchestration or {})
    if info.get("status") in ("running", "waiting", "handed_off"):
        return {"workspace_id": workspace_id, "status": info["status"], "detail": "already running"}
    from datetime import datetime, timezone  # noqa: PLC0415
    info.update({"status": "requested", "requested_by": initiated_by, "updated_at": datetime.now(timezone.utc).isoformat()})
    ws.orchestration = info
    store.put_workspace(ws)
    return {"workspace_id": workspace_id, "status": "requested"}


class PromoteRequest(BaseModel):
    comment: str | None = None
    decided_by: str | None = None


@router.post("/{workspace_id}/promote", status_code=202)
async def promote_kb_refresh(
    workspace_id: str,
    body: PromoteRequest | None = Body(default=None),
    principal: Principal = Depends(current_principal),
) -> dict:
    """MERGE -> PENDING_SYNC -> CLOSED (Plan v3 C): the human promote of the STAGING
    KB refresh built after all Mini workspaces closed. Architect or Product Owner.
    Records the decision (audit + `orchestration.promote`), asks the KB gateway in
    `api` mode, and lets the worker resume the Global thread which closes the
    workspace. In manual mode the ACTIVE flip is done in the KB tool."""
    from app.agentic_platform.fe_core.auth.personas import Persona  # noqa: PLC0415
    from app.agentic_platform.fe_core.kb.models import ArtifactStatus as _AS  # noqa: PLC0415

    store = get_store()
    ws = store.get_workspace(workspace_id)
    if ws is None:
        raise HTTPException(status_code=404, detail=f"unknown workspace: {workspace_id}")
    if not (principal.has_persona(Persona.ARCHITECT) or principal.has_persona(Persona.PRODUCT_OWNER)):
        raise HTTPException(status_code=403, detail="promote requires the architect or product-owner persona")
    info = dict(ws.orchestration or {})
    if info.get("status") != "pending_sync":
        raise HTTPException(status_code=409, detail=f"workspace is '{info.get('status', 'idle')}', not pending_sync")
    art_id = info.get("kb_refresh_artifact_id")
    art = store.get_artifact(art_id) if art_id else None
    if art is None:
        raise HTTPException(status_code=409, detail="no kb-refresh artefact recorded for this workspace")
    who = (body.decided_by if body and body.decided_by else None) or principal.email or principal.subject
    # approve the kb-refresh artefact itself (append-only approval + audit)
    if art.status is not _AS.APPROVED:
        from app.agentic_platform.fe_core.kb.models import ApprovalDecision  # noqa: PLC0415
        store.record_approval(art, decision=ApprovalDecision.APPROVE, decided_by=who,
                              role="architect" if principal.has_persona(Persona.ARCHITECT) else "product-owner",
                              comment=(body.comment if body else None))
        store.put_artifact(art)
    result: dict = {"mode": "manual", "result": "recorded"}
    now = datetime.now(timezone.utc).isoformat()
    promo = dict(info.get("promote") or {})
    promo.update({"state": "approved", "decided_by": who, "decided_at": now, "result": result,
                  "comment": body.comment if body else None})
    info["promote"] = promo
    info["updated_at"] = now
    ws.orchestration = info
    store.put_workspace(ws)
    audit = getattr(store, "audit", None)
    if audit is not None:
        try:
            audit(action="kb.promote", actor=who, workspace_id=ws.id, artifact_id=art.id,
                  before={"kb_status": "STAGING"}, after={"kb_status": "ACTIVE" if result.get("mode") == "api" else "promote_requested"},
                  detail={"kb_version": info.get("kb_version"), **result})
        except Exception:  # noqa: BLE001
            pass
    return {"workspace_id": ws.id, "kb_version": info.get("kb_version"), "artifact_id": art.id,
            "promote": promo, "next": "the worker resumes the Global thread and closes the workspace"}


# --- developer cockpit: bundle + dev/pr (Phase 5) ---------------------------------------
@router.get("/{workspace_id}/bundle")
async def workspace_bundle(
    workspace_id: str,
    principal: Principal = Depends(current_principal),
) -> dict:
    """Everything a laptop needs to work on this workspace, with no local-path
    dependency: workspace + EPIC, the latest APPROVED input per consumed type
    (presigned / file URLs + inline Markdown when small), the project domain pack
    and agent-config overrides, the stage list (which are laptop stages), and the
    orchestration status. Hybrid-scope personas only (code flows here).
    """
    from app.agentic_platform.fe_core.artifacts.reader import artifact_prefix, materialise, read_markdown_files  # noqa: PLC0415
    from app.agentic_platform.fe_core.artifacts.store import get_artifact_store  # noqa: PLC0415
    from app.agentic_platform.fe_core.pipeline.eligibility import readable_workspace_ids  # noqa: PLC0415
    from app.agentic_platform.fe_core.pipeline.project_config import load_project_config  # noqa: PLC0415

    store = get_store()
    ws = store.get_workspace(workspace_id)
    if ws is None:
        raise HTTPException(status_code=404, detail=f"unknown workspace: {workspace_id}")
    if not principal.can_see_code:
        raise HTTPException(status_code=403, detail="the developer bundle requires a Hybrid Workspace role")

    pipeline = get_pipeline(ws.pipeline)
    settings = get_settings()
    astore = get_artifact_store(settings)
    readable = readable_workspace_ids(store, ws.id)

    # which artefact types this workspace consumes (all stages) + inherited from the parent
    wanted: list[str] = []
    for st in pipeline.stages:
        for t in st.consumes:
            if t not in wanted:
                wanted.append(t)
    inputs: list[dict] = []
    presence: list[dict] = []          # AIDLC-style: every wanted kind, present or not
    citations: list[str] = []          # artefact ids + KB chunk ids the inputs were grounded on
    for atype in wanted:
        cands = store.approved_artifacts(pipeline.name, [atype], workspace_ids=readable)
        if not cands and pipeline.parent:
            cands = store.approved_artifacts(pipeline.parent, [atype], workspace_ids=readable)
        if not cands:
            presence.append({"kind": atype, "present": False})
            continue
        art = max(cands, key=lambda a: a.version)
        presence.append({"kind": atype, "present": True, "artifact_id": art.id, "version": art.version})
        for cid in [art.id, *(art.source_artifact_ids or [])]:
            if cid not in citations:
                citations.append(cid)
        for ln in art.links or []:
            ref = ln.get("kb_chunk_id") or ln.get("artifact_id")
            if ref and ref not in citations:
                citations.append(ref)
        entry = {"artifact_type": atype, "artifact_id": art.id, "version": art.version,
                 "storage_kind": art.storage_kind, "content_uri": art.content_uri, "files": []}
        try:
            prefix = artifact_prefix(art)
            if prefix and art.storage_kind in ("s3", "local"):
                for f in art.files or []:
                    entry["files"].append({**f, "url": astore.presign_get(f"{prefix}/{f['path']}")})
            local = materialise(art, settings=settings)
            if local is not None:
                md = read_markdown_files(local, max_chars=40_000)
                entry["markdown"] = md                     # inline for the small docs
        except Exception as exc:  # noqa: BLE001
            entry["detail"] = f"body unavailable: {exc}"
        inputs.append(entry)

    cfg = load_project_config(settings.fe_workspace_root, ws.kb_application_id)
    from app.agentic_platform.worker.orchestrator.state import LAPTOP_TARGETS  # noqa: PLC0415
    stages = [{"key": s.key, "name": s.name, "target": s.target, "produces": s.produces,
               "consumes": s.consumes, "approval_persona": s.approval_persona,
               "plugin": s.owner.plugin, "skill": s.owner.skill,
               "where": "laptop" if s.target in LAPTOP_TARGETS else "server"} for s in pipeline.ordered]
    return {
        "workspace": ws.model_dump(mode="json"),
        "pipeline": pipeline.name,
        "epic": {"id": ws.epic_id, "title": ws.epic_title} if ws.epic_id else None,
        "stages": stages,
        "inputs": inputs,
        "artifacts": presence,
        "citations": citations,
        "repo_hints": _repo_hints(cfg, stages),
        "pinned_kb_version": (ws.orchestration or {}).get("pinned_kb_version")
                             or (cfg.domain_pack.get("knowledge_base") or {}).get("version"),
        "domain_pack": cfg.domain_pack or None,
        "agent_config": {"stages": cfg.stage_instructions, "agents": cfg.agent_instruction_files},
        "orchestration": ws.orchestration or {"status": "idle"},
        "handover_brief": f"/api/v1/workspaces/{ws.id}/dev/export.md",
        "sync": {"endpoint": "/api/v1/sync/artifacts", "dev_pr": f"/api/v1/workspaces/{ws.id}/dev/pr"},
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


_LANE_WORDS = {"ui": ("ui", "front", "screen"), "api": ("service", "api"), "db": ("storage", "data", "db"),
               "tests": ("test", "qa")}


def _repo_hints(cfg, stages: list[dict]) -> list[dict]:
    """One hint per laptop lane (ui/api/db/tests): which stages run there and which
    target-architecture band/technology it maps to. The plugin's `fe.config.json`
    `repo_map` keys are these lane names -- the platform never sends local paths."""
    bands = ((cfg.domain_pack.get("target_architecture") or {}).get("bands") or {})
    hints: dict[str, dict] = {}
    for st in stages:
        if st.get("where") != "laptop":
            continue
        lane = st["target"]
        h = hints.setdefault(lane, {"lane": lane, "stages": [], "technology": None, "band": None})
        h["stages"].append(st["key"])
        if h["technology"] is None:
            for bkey, band in bands.items():
                name = str(band.get("name", "")).lower()
                if any(w in name for w in _LANE_WORDS.get(lane, (lane,))):
                    h["technology"] = band.get("technology")
                    h["band"] = bkey
                    break
    return list(hints.values())


@router.get("/{workspace_id}/dev/export.md")
async def workspace_handover_brief(
    workspace_id: str,
    principal: Principal = Depends(current_principal),
):
    """The developer handover brief (AIDLC `dev/export.md` equivalent): one Markdown
    document a developer -- or Claude Code -- can read top-to-bottom before touching
    code. Sections are generic; the project's `agent-config/handover.md` (when present)
    is inserted verbatim as the project-specific guidance."""
    from fastapi.responses import PlainTextResponse  # noqa: PLC0415
    from app.agentic_platform.fe_core.artifacts.reader import materialise, read_markdown_files  # noqa: PLC0415
    from app.agentic_platform.fe_core.pipeline.eligibility import readable_workspace_ids  # noqa: PLC0415
    from app.agentic_platform.fe_core.pipeline.project_config import load_project_config  # noqa: PLC0415

    store = get_store()
    ws = store.get_workspace(workspace_id)
    if ws is None:
        raise HTTPException(status_code=404, detail=f"unknown workspace: {workspace_id}")
    if not principal.can_see_code:
        raise HTTPException(status_code=403, detail="the handover brief requires a Hybrid Workspace role")
    pipeline = get_pipeline(ws.pipeline)
    settings = get_settings()
    cfg = load_project_config(settings.fe_workspace_root, ws.kb_application_id)
    readable = readable_workspace_ids(store, ws.id)
    from app.agentic_platform.worker.orchestrator.state import LAPTOP_TARGETS  # noqa: PLC0415

    def latest(atype: str):
        cands = store.approved_artifacts(pipeline.name, [atype], workspace_ids=readable)
        if not cands and pipeline.parent:
            cands = store.approved_artifacts(pipeline.parent, [atype], workspace_ids=readable)
        return max(cands, key=lambda a: a.version) if cands else None

    def body(art, limit=20_000) -> str:
        try:
            local = materialise(art, settings=settings)
            if local is None:
                return "_(git-ref artefact -- see repository)_"
            md = read_markdown_files(local, max_chars=limit)
            primary = md.get(f"{art.artifact_type}.md") or (next(iter(md.values())) if md else "")
            return primary or "_(no Markdown body)_"
        except Exception as exc:  # noqa: BLE001
            return f"_(body unavailable: {exc})_"

    laptop_stages = [s for s in pipeline.ordered if s.target in LAPTOP_TARGETS]
    server_stages = [s for s in pipeline.ordered if s.target not in LAPTOP_TARGETS]
    consumed = []
    for st in laptop_stages:
        for t in st.consumes:
            if t not in consumed:
                consumed.append(t)
    lines: list[str] = []
    lines += [f"# Developer handover brief -- {ws.epic_title or ws.id}", "",
              f"Workspace `{ws.id}` · pipeline `{pipeline.name}` · project `{ws.kb_application_id}`"
              + (f" · EPIC {ws.epic_id}" if ws.epic_id else ""),
              f"Generated {datetime.now(timezone.utc).isoformat()} · orchestration: "
              f"{(ws.orchestration or {}).get('status', 'idle')}", ""]
    lines += ["## 1. Change summary", "",
              (ws.epic_title or "See the EPIC and feature documents below."), ""]
    dp = cfg.domain_pack or {}
    lines += ["## 2. Target architecture and hard constraints", ""]
    ta = dp.get("target_architecture") or {}
    if ta.get("style"):
        lines.append(f"- Style: {ta['style']}")
    for bkey, band in (ta.get("bands") or {}).items():
        lines.append(f"- {bkey} **{band.get('name')}** -- {band.get('technology', '')}")
    hc = dp.get("hard_constraints") or []
    if hc:
        lines += ["", "**Hard constraints (never violate):**"] + [f"- {c}" for c in hc]
    lines.append("")
    lines += ["## 3. What runs where", "",
              "| Stage | Where | Produces | Approved by |", "|---|---|---|---|"]
    for st in pipeline.ordered:
        where = "laptop (you)" if st.target in LAPTOP_TARGETS else "server"
        lines.append(f"| {st.key} | {where} | {', '.join(st.produces)} | {st.approval_persona} |")
    lines.append("")
    lines += ["## 4. Approved inputs (read these first)", ""]
    for t in consumed:
        art = latest(t)
        if art is None:
            lines += [f"### {t} -- MISSING (not yet approved; stop and ask)", ""]
            continue
        lines += [f"### {t} v{art.version} (`{art.id}`, approved by {art.approved_by})", "", body(art), ""]
    lines += ["## 5. Project-specific guidance (agent-config)", ""]
    for st in laptop_stages:
        txt = cfg.stage_instructions.get(st.key)
        if txt:
            lines += [f"### {st.key}", "", txt.strip(), ""]
    handover_md = None
    if cfg.agent_config_dir:
        p = Path(cfg.agent_config_dir) / "handover.md"
        if p.exists():
            handover_md = p.read_text(encoding="utf-8")
    if handover_md:
        lines += [handover_md.strip(), ""]
    elif not any(cfg.stage_instructions.get(s.key) for s in laptop_stages):
        lines += ["_(none -- add `agent-config/<stage>.md` or `agent-config/handover.md`)_", ""]
    lines += ["## 6. Definition of done and write-back", "",
              "1. Work on a branch `fe/<workspace-id>-<slug>`; commit and push; open a PR.",
              "2. `/fe-develop` step 6 (or `fe_sync.py`) posts the git ref to "
              f"`POST /api/v1/workspaces/{ws.id}/dev/pr` (also accepts the AIDLC `PrSyncRequest` shape).",
              "3. Never send local paths; the platform records repo/branch/commit/PR and the developer "
              "approval resumes the workspace thread.",
              "4. Anything the KB got wrong goes into `kb_drift[]` -- it becomes a review item, never a silent edit.", ""]
    remaining = [s.key for s in server_stages if s.key not in [x.key for x in pipeline.ordered[:1]]]
    lines += ["## 7. What happens after your PR", "",
              "Server-side stages that still run after the laptop lane: "
              + ", ".join(k for k in remaining if k not in consumed) + ".", ""]
    text = "\n".join(lines)
    return PlainTextResponse(text, media_type="text/markdown; charset=utf-8",
                             headers={"Content-Disposition": f'inline; filename="{ws.id}-handover.md"'})


class DevPrRequest(BaseModel):
    """Our shape plus the AIG `PrSyncRequest` aliases (`repo`, `branch`, `commit`,
    `files_changed`) so the aidlc-develop plugin can post here unchanged."""
    stage: str = "ui-code"
    git_repo: str | None = None
    git_branch: str | None = None
    git_commit: str | None = None
    repo: str | None = None
    branch: str | None = None
    commit: str | None = None
    files_changed: list[str] = Field(default_factory=list)
    pr_url: str | None = None
    artifact_type: str | None = None
    persona: str | None = "developer"
    summary: str | None = None
    files: list[dict] = Field(default_factory=list)
    kb_drift: list[dict] = Field(default_factory=list)
    advance: bool = True
    #: Tests-green gate (fe_verify.py report): {passed, commands:[{command,exit_code,...}],
    #: override_reason?}. A failing report advances the hand-off only with an explicit
    #: override_reason, which is recorded as a review item -- accurate code is proven by
    #: execution, not by prose. None = unverified (recorded as such).
    verification: dict | None = None


@router.post("/{workspace_id}/dev/pr", status_code=202)
async def workspace_dev_pr(
    workspace_id: str,
    body: DevPrRequest,
    principal: Principal = Depends(current_principal),
) -> dict:
    """Record a laptop-produced code stage as a git-ref Draft artefact (via the
    same path as /sync/artifacts) and mark the hand-off as 'in review'. KB-drift
    notes become review items on the workspace. Nothing is approved here — the
    developer/tester approves in the console, which resumes the Mini thread."""
    from app.agentic_platform.api.routers.sync import SyncArtifactRequest, sync_artifact  # noqa: PLC0415

    store = get_store()
    ws = store.get_workspace(workspace_id)
    if ws is None:
        raise HTTPException(status_code=404, detail=f"unknown workspace: {workspace_id}")
    pipeline = get_pipeline(ws.pipeline)
    stage = pipeline.stage(body.stage)
    atype = body.artifact_type or (stage.produces[0] if stage.produces else body.stage)
    git_repo = body.git_repo or body.repo
    git_branch = body.git_branch or body.branch
    git_commit = body.git_commit or body.commit
    files = list(body.files) + [{"path": p} for p in body.files_changed if not any(f.get("path") == p for f in body.files)]
    if not git_commit and not files and not body.summary:
        raise HTTPException(status_code=422, detail="supply a git commit (git_commit/commit) or files/summary")
    verification = dict(body.verification or {})
    if body.advance and verification and verification.get("passed") is False             and not verification.get("override_reason"):
        failed = [c.get("command") for c in verification.get("commands", []) if c.get("exit_code")]
        raise HTTPException(
            status_code=422,
            detail=("tests-green gate: the verification report shows failing checks "
                    f"({', '.join(map(str, failed)) or 'see report'}). Fix them and re-run "
                    "fe_verify.py, or pass an explicit override_reason (recorded as a review item)."))
    req = SyncArtifactRequest(
        pipeline=pipeline.name, stage=body.stage, artifact_type=atype, kb_application=ws.kb_application_id,
        workspace_id=ws.id, persona=body.persona, git_repo=git_repo, git_branch=git_branch,
        git_commit=git_commit, pr_url=body.pr_url, files=files, summary=body.summary,
    )
    result = await sync_artifact(body=req, idempotency_key=None,
                                 x_user=principal.email or principal.subject)
    info = dict(ws.orchestration or {})
    if body.kb_drift:
        info.setdefault("review_items", []).extend(
            [{**d, "stage": body.stage, "raised_by": principal.email or principal.subject} for d in body.kb_drift])
    if verification.get("passed") is False:
        info.setdefault("review_items", []).append({
            "kind": "verification_override", "stage": body.stage,
            "reason": verification.get("override_reason"),
            "failed": [c.get("command") for c in verification.get("commands", []) if c.get("exit_code")],
            "raised_by": principal.email or principal.subject})
    if body.advance:
        info["hand_off"] = {"stage": body.stage, "artifact_id": result["artifact"]["id"], "pr_url": body.pr_url,
                            "state": "in_review",
                            "verification": ("passed" if verification.get("passed") else
                                             "overridden" if verification else "unverified")}
    ws.orchestration = info
    store.put_workspace(ws)
    audit = getattr(store, "audit", None)
    if audit is not None:
        try:
            audit(action="dev.pr_synced", actor=principal.email or principal.subject, persona=body.persona,
                  workspace_id=ws.id, artifact_id=result["artifact"]["id"],
                  after={"hand_off": info.get("hand_off")},
                  detail={"stage": body.stage, "repo": git_repo, "branch": git_branch, "commit": git_commit,
                          "pr_url": body.pr_url, "files": len(files), "kb_drift": len(body.kb_drift),
                          "advance": body.advance,
                          "verification": verification.get("passed") if verification else None})
        except Exception:  # noqa: BLE001
            pass
    # AIG PrSyncResult-compatible keys (workspace_id / pr_url / advanced) alongside ours.
    return {"workspace_id": ws.id, "pr_url": body.pr_url, "advanced": bool(body.advance),
            "artifact_id": result["artifact"]["id"], "state": "in_review" if body.advance else "recorded",
            "created": result.get("created"), "drift_recorded": len(body.kb_drift),
            "next_action": result.get("next_action")}


# --- workspace-level SSE (orchestration + current run steps) ---------------------------
@router.get("/{workspace_id}/events")
async def workspace_events(workspace_id: str, poll_seconds: float = 1.0):
    """SSE for the console: `status` (orchestration block: status / current_stage /
    waiting_on_persona / run_id), `step` (new lines of the current run's log — the
    agent's tool calls and messages, never prompts or secrets), `end` when the thread
    reaches completed / failed. Mirrors the AIDLC `generate/stream` vocabulary."""
    import asyncio  # noqa: PLC0415
    import json  # noqa: PLC0415
    from fastapi.responses import StreamingResponse  # noqa: PLC0415

    store = get_store()
    if store.get_workspace(workspace_id) is None:
        raise HTTPException(status_code=404, detail=f"unknown workspace: {workspace_id}")

    async def generate():
        last_status: str | None = None
        seen_log: dict[str, int] = {}
        announced: set[str] = set()
        idle_ticks = 0
        while True:
            ws = store.get_workspace(workspace_id)
            if ws is None:
                break
            info = dict(ws.orchestration or {}); info.setdefault("status", "idle")
            snap = json.dumps({k: info.get(k) for k in ("status", "current_stage", "waiting_on_persona",
                                                        "run_id", "last_error", "hand_off", "laptop_stages_pending")},
                              sort_keys=True, default=str)
            if snap != last_status:
                yield f"event: status\ndata: {snap}\n\n"
                last_status = snap
            run_id = info.get("run_id")
            if run_id:
                run = store.get_run(run_id)
                if run is not None:
                    n = seen_log.get(run_id, 0)
                    for line in run.log[n:]:
                        yield "event: step\ndata: " + json.dumps({"run_id": run_id, "stage": run.stage_key, "line": line}) + "\n\n"
                    seen_log[run_id] = len(run.log)
                    # `artifact` once per run when its deliverables exist (AIDLC vocabulary):
                    # ids + storage refs so the console can refresh the card list.
                    if run.artifact_ids and run_id not in announced:
                        arts = []
                        for aid in run.artifact_ids:
                            a = store.get_artifact(aid)
                            if a is not None:
                                arts.append({"id": a.id, "type": a.artifact_type, "version": a.version,
                                             "status": a.status.value, "storage_kind": a.storage_kind,
                                             "manifest_uri": a.manifest_uri, "pr_url": a.pr_url})
                        if arts:
                            announced.add(run_id)
                            yield "event: artifact\ndata: " + json.dumps(
                                {"run_id": run_id, "stage": run.stage_key, "artifacts": arts}) + "\n\n"
            if info.get("status") == "failed":
                yield "event: error\ndata: " + json.dumps(
                    {"stage": info.get("current_stage"), "detail": info.get("last_error")}) + "\n\n"
            if info.get("status") in ("completed", "failed"):
                yield "event: end\ndata: " + json.dumps({"status": info.get("status")}) + "\n\n"
                break
            idle_ticks += 1
            if idle_ticks > 3600:                       # ~1h safety cap for idle consoles
                yield "event: end\ndata: {\"status\": \"timeout\"}\n\n"
                break
            await asyncio.sleep(poll_seconds)

    return StreamingResponse(generate(), media_type="text/event-stream")
