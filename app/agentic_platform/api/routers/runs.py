"""Pipeline runs, artefacts, approvals and traceability (PRD 9.1).

Status codes are load-bearing:
  409  pipeline or artefact state forbids the request (not ready, already
       approved, expected-version mismatch)
  424  a required GATHER plugin failed to load, so the output cannot be trusted
  503  the knowledge base is unreachable
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import io
import re
import json
import logging
import mimetypes
import shutil
import tempfile
import uuid
import zipfile
from pathlib import Path

from fastapi import APIRouter, Body, Depends, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.agentic_platform.api.security import current_principal, require_roles
from app.agentic_platform.api.services.jobs import ApprovalError, JobService
from app.agentic_platform.fe_core.auth.roles import Persona, Principal, code_artifact
from app.agentic_platform.fe_core.artifacts.store import artifact_key, get_artifact_store
from app.agentic_platform.fe_core.kb.models import ArtifactStatus, SdlcArtifact
from app.agentic_platform.fe_core.config import get_settings
from app.agentic_platform.fe_core.pipeline.eligibility import StageBlockedError
from app.agentic_platform.fe_core.pipeline.models import StageState
from app.agentic_platform.fe_core.pipeline.registry import (
    PipelineNotFoundError,
    get_pipeline,
    registry,
)
from app.agentic_platform.fe_core.ports.claude_runner import PluginVerificationError
from app.agentic_platform.fe_core.store import get_store

logger = logging.getLogger(__name__)
router = APIRouter()

_TERMINAL = {
    StageState.COMPLETED, StageState.FAILED, StageState.CANCELLED,
    StageState.WAITING_FOR_APPROVAL, StageState.SUPERSEDED,
}


class CreateRunRequest(BaseModel):
    stage: str = Field(description="Stage key, e.g. 'prd'")
    pipeline: str | None = None
    kb_application: str | None = None
    workspace_id: str | None = Field(
        default=None,
        description=(
            "ID of the workspace to run this stage in. "
            "When provided the workspace's stored kb_application_id is used "
            "directly and the KB catalogue is not consulted — this is required "
            "for projects onboarded via RED document or greenfield intake that "
            "have no KB catalogue entry."
        ),
    )
    epic_workspace_id: str | None = Field(
        default=None,
        description=(
            "Mini workspace to scope a per-EPIC global-pipeline stage (feature, "
            "user-story, coverage) to. When set, this workspace is used for run "
            "scoping instead of workspace_id, so {N} in the system prompt resolves "
            "to the correct EPIC number. workspace_id is still used for pipeline "
            "resolution when pipeline is not supplied explicitly."
        ),
    )
    force: bool = Field(
        default=False,
        description="Start a rerun even when the stage already completed. "
                    "Creates a new version; never edits the previous one.",
    )
    execute: bool = Field(default=True, description="Set false to queue only.")
    module_scope: str | None = Field(
        default=None,
        description=(
            "RE Graph extraction scope for the PRD stage. "
            "e.g. 'admin' filters nodes whose category/source_locus contains this string. "
            "None = whole application."
        ),
    )
    gear_id: str | None = Field(
        default=None,
        description=(
            "RE Graph gear identifier (e.g. 'japan'). If not provided, reads from workspace.gear_id. "
            "Can be overridden per-run."
        ),
    )
    business_area: str | None = Field(
        default=None,
        description="Business area context for RE Graph /fe/query/raw (e.g. 'UW Credit Risk System Maintenance').",
    )
    role: str | None = Field(
        default=None,
        description="Role context for PRD agent (e.g. 'admin'). Not sent to RE Graph API.",
    )


class ApprovalRequest(BaseModel):
    approver: str = Field(min_length=1, description="Identity of the human deciding.")
    decision: str = Field(default="approve", pattern="^(approve|reject)$")
    comment: str | None = None
    expected_version: int | None = Field(
        default=None,
        description="Artefact version the reviewer saw. A mismatch returns 409.",
    )
    persona: str | None = Field(default=None, description="Approving persona (FR-P3); defaults to the token's.")


def _service(pipeline_name: str | None) -> JobService:
    try:
        return JobService(pipeline=get_pipeline(pipeline_name))
    except PipelineNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _service_for_run(run_id: str, pipeline_name: str | None) -> JobService:
    """The service for the pipeline a run actually belongs to.

    A run records its pipeline; defaulting to the Global pipeline made every
    approve/cancel of a Mini-tier run fail with `unknown stage: feature`.
    """
    if pipeline_name is None:
        run = get_store().get_run(run_id)
        if run is not None and getattr(run, "pipeline", None):
            pipeline_name = run.pipeline
    return _service(pipeline_name)


@router.post("/pipeline-runs", tags=["runs"], status_code=202)
async def create_run(
    body: CreateRunRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    x_user: str | None = Header(default=None, alias="X-User"),
) -> dict:
    pipeline_name = body.pipeline
    if pipeline_name is None and body.workspace_id:
        # A workspace knows its tier's pipeline; defaulting to the Global one
        # rejected every Mini-tier stage with `unknown stage: feature`.
        ws = get_store().get_workspace(body.workspace_id)
        if ws is not None and getattr(ws, "pipeline", None):
            pipeline_name = ws.pipeline
    service = _service(pipeline_name)
    # Per-EPIC stages (feature, user-story, coverage) run inside the Global
    # thread but must be scoped to a Mini Workspace.  Accepting a bare
    # workspace_id silently registers the run under the Global workspace,
    # producing the "registered under a different workspace" mismatch the UI
    # surfaces as a backend gap.  Reject early so the caller fixes the request
    # rather than creating a broken run.
    _PER_EPIC_STAGES = frozenset({"feature", "user-story", "coverage"})
    if body.stage in _PER_EPIC_STAGES and body.epic_workspace_id is None:
        ws_row = get_store().get_workspace(body.workspace_id) if body.workspace_id else None
        if ws_row is not None and getattr(ws_row, "tier", None) == "global":
            raise HTTPException(
                status_code=422,
                detail=(
                    f"stage '{body.stage}' is a per-EPIC stage and must be scoped to a "
                    "Mini Workspace. Supply 'epic_workspace_id' with the target Mini "
                    "Workspace ID alongside 'workspace_id' (used only for pipeline resolution)."
                ),
            )
    # epic_workspace_id scopes per-EPIC global-pipeline stages to a specific
    # Mini Workspace so {N} in the system prompt resolves correctly.
    effective_workspace_id = body.epic_workspace_id or body.workspace_id

    # Validate PRD prerequisites: KB must be approved first
    if body.stage == "prd" and effective_workspace_id:
        from app.agentic_platform.fe_core.pipeline.eligibility import require_runnable

        store = get_store()
        try:
            pipeline = get_pipeline(pipeline_name)
            stage = pipeline.stage("prd")
            require_runnable(store, pipeline, stage, effective_workspace_id)
        except StageBlockedError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    try:
        run = await service.create_run(
            body.stage,
            kb_application=body.kb_application,
            workspace_id=effective_workspace_id,
            initiated_by=x_user,
            idempotency_key=idempotency_key,
            force=body.force,
            execute=body.execute,
            module_scope=body.module_scope,
            gear_id=body.gear_id,
            business_area=body.business_area,
            role=body.role,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"unknown stage: {body.stage}") from exc
    except StageBlockedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except PluginVerificationError as exc:
        raise HTTPException(status_code=424, detail=str(exc)) from exc
    return {
        "run": run.model_dump(mode="json"),
        "events": f"/api/v1/pipeline-runs/{run.run_id}/events",
    }


@router.get("/pipeline-runs", tags=["runs"])
async def list_runs(
    pipeline: str | None = None,
    stage: str | None = None,
    workspace_id: str | None = None,
    limit: int = Query(default=50, ge=1, le=500),
) -> dict:
    """Runs, newest first. `workspace_id` scopes to one workspace across both
    tiers; without it `pipeline` (default: the Global pipeline) is the scope.
    Before `workspace_id` was honoured, a Mini workspace's console listed the
    Global tier's runs and its own were invisible."""
    if workspace_id:
        ws = get_store().get_workspace(workspace_id)
        # An explicit `pipeline` wins: a Global workspace also hosts runs of the
        # re-kb-build pipeline (its card KB), which are invisible when the
        # workspace's own tier pipeline is assumed.
        if pipeline:
            name = get_pipeline(pipeline).name
        else:
            name = ws.pipeline if ws is not None else None
        runs = get_store().list_runs(name, stage, workspace_id=workspace_id)[:limit]
    else:
        name = get_pipeline(pipeline).name
        runs = get_store().list_runs(name, stage)[:limit]
    return {"pipeline": name, "count": len(runs),
            "items": [r.model_dump(mode="json") for r in runs]}


@router.get("/pipeline-runs/{run_id}", tags=["runs"])
async def get_run(run_id: str) -> dict:
    run = get_store().get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"unknown run: {run_id}")
    store = get_store()
    return {
        "run": run.model_dump(mode="json"),
        "artifacts": [
            store.get_artifact(a).model_dump(mode="json")
            for a in run.artifact_ids if store.get_artifact(a)
        ],
    }


@router.get("/pipeline-runs/{run_id}/events", tags=["runs"])
async def stream_events(run_id: str, poll_seconds: float = 1.0) -> StreamingResponse:
    """Normalised run events over SSE (FR-021).

    Deliberately emits status transitions and counters only -- never prompts or
    credentials, which FR-021 forbids putting on this channel.
    """
    store = get_store()
    if store.get_run(run_id) is None:
        raise HTTPException(status_code=404, detail=f"unknown run: {run_id}")

    async def generate():
        last: str | None = None
        while True:
            run = store.get_run(run_id)
            if run is None:
                break
            snapshot = json.dumps({
                "run_id": run.run_id,
                "stage": run.stage_key,
                "state": run.state.value,
                "attempt": run.attempt,
                "files_written": len(run.files_written),
                "tools_invoked": len(run.tools_invoked),
                "tools_denied": run.tools_denied,
                "input_tokens": run.input_tokens,
                "output_tokens": run.output_tokens,
                "cache_read_input_tokens": run.cache_read_input_tokens,
                "cache_creation_input_tokens": run.cache_creation_input_tokens,
                "cost_usd": run.cost_usd,
                "num_turns": run.num_turns,
                "max_turns": run.max_turns,
                "error_code": run.error_code,
            }, sort_keys=True)
            if snapshot != last:
                yield f"event: status\ndata: {snapshot}\n\n"
                last = snapshot
            if run.state in _TERMINAL:
                yield "event: end\ndata: {}\n\n"
                break
            await asyncio.sleep(poll_seconds)

    return StreamingResponse(generate(), media_type="text/event-stream")


@router.post("/pipeline-runs/{run_id}/recover", tags=["runs"])
async def recover_run(
    run_id: str,
    principal: Principal = Depends(current_principal),
) -> dict:
    """Operator recovery for a run the turn cap failed (`error_code=max_turns`)
    whose deliverable is complete on disk: re-applies the post-run checks
    (deliverables, grounding, KB gate, persistence, indexing) without spending
    tokens, marks the run `capped`, and moves it to WAITING_FOR_APPROVAL."""
    store = get_store()
    run = store.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"unknown run: {run_id}")
    service = _service_for_run(run_id, None)
    try:
        executor = await service._executor()
        from app.agentic_platform.worker.stage_executor import StageExecutionError  # noqa: PLC0415
        try:
            run = await executor.recover(run, by=principal.email or principal.subject)
        except StageExecutionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"recover failed: {exc}") from exc
    return {"run": run.model_dump(mode="json")}


@router.post("/pipeline-runs/{run_id}/cancel", tags=["runs"])
async def cancel_run(run_id: str, pipeline: str | None = None) -> dict:
    service = _service_for_run(run_id, pipeline)
    try:
        run = service.cancel(run_id)
    except ApprovalError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"run": run.model_dump(mode="json")}


# ---------------------------------------------------------------------------
# CLI worker lifecycle endpoints (claim / heartbeat / complete / upload)
# ---------------------------------------------------------------------------

class ClaimRunRequest(BaseModel):
    worker_id: str = Field(min_length=1, description="Email or identifier of the CLI worker process")


class CompleteRunRequest(BaseModel):
    artifact_ids: list[str] = Field(default_factory=list, description="Artifact IDs uploaded via POST /artifacts/upload")
    files_written: list[str] = Field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float | None = None
    num_turns: int | None = None
    error: str | None = None


@router.post("/pipeline-runs/{run_id}/claim", tags=["runs"])
async def claim_run(run_id: str, body: ClaimRunRequest) -> dict:
    """Atomically claim a queued CLI run and transition it to RUNNING.

    Returns 409 when the run is not queued, already claimed, or not a CLI run.
    The worker must call POST /pipeline-runs/{id}/heartbeat at least every 90 s
    so a stale-heartbeat watchdog can requeue abandoned runs in the future.
    """
    store = get_store()
    run = store.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"unknown run: {run_id}")
    if getattr(run, "required_runner", None) != "cli":
        raise HTTPException(status_code=409, detail=f"run {run_id} is not a CLI run")
    if run.state is not StageState.QUEUED:
        raise HTTPException(status_code=409, detail=f"run {run_id} is {run.state.value}, not queued")
    if getattr(run, "claimed_by", None):
        raise HTTPException(status_code=409, detail=f"run {run_id} already claimed by {run.claimed_by}")
    now = datetime.now(timezone.utc)
    run.claimed_by = body.worker_id
    run.claimed_at = now
    run.heartbeat_at = now
    run.started_at = now
    run.transition(StageState.RUNNING)
    store.save_run(run)
    return {"run": run.model_dump(mode="json")}


@router.post("/pipeline-runs/{run_id}/heartbeat", tags=["runs"])
async def heartbeat_run(run_id: str) -> dict:
    """Update heartbeat timestamp to signal the worker is still alive."""
    store = get_store()
    run = store.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"unknown run: {run_id}")
    if run.state is not StageState.RUNNING:
        raise HTTPException(status_code=409, detail=f"run {run_id} is {run.state.value}, not running")
    run.heartbeat_at = datetime.now(timezone.utc)
    store.save_run(run)
    return {"run_id": run_id, "heartbeat_at": run.heartbeat_at.isoformat()}


@router.post("/pipeline-runs/{run_id}/complete", tags=["runs"])
async def complete_run(run_id: str, body: CompleteRunRequest) -> dict:
    """Transition a RUNNING CLI run to WAITING_FOR_APPROVAL (or FAILED).

    Upload artifacts first via POST /artifacts/upload and pass the returned
    artifact_ids here so they are linked to the run before the gate fires.
    """
    store = get_store()
    run = store.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"unknown run: {run_id}")
    if getattr(run, "required_runner", None) != "cli":
        raise HTTPException(status_code=409, detail=f"run {run_id} is not a CLI run")
    if run.state is StageState.QUEUED and body.artifact_ids:
        # Run was requeued by the stale-heartbeat watchdog while Claude was executing.
        # The worker finished and uploaded artifacts (non-empty artifact_ids is proof) —
        # recover by re-entering RUNNING so the normal completion transition can proceed.
        # claimed_by is NULL after the watchdog clears it, so it cannot be used as proof.
        run.state = StageState.RUNNING
    if run.state is not StageState.RUNNING:
        raise HTTPException(status_code=409, detail=f"run {run_id} is {run.state.value}, not running")
    now = datetime.now(timezone.utc)
    run.files_written = body.files_written
    run.input_tokens = body.input_tokens
    run.output_tokens = body.output_tokens
    run.cost_usd = body.cost_usd
    run.num_turns = body.num_turns
    run.finished_at = now
    for art_id in body.artifact_ids:
        if art_id not in run.artifact_ids:
            run.artifact_ids.append(art_id)
    if body.error:
        run.error = body.error
        run.error_code = "cli_error"
        run.transition(StageState.FAILED)
    else:
        run.transition(StageState.WAITING_FOR_APPROVAL)
    store.save_run(run)
    return {"run": run.model_dump(mode="json")}


@router.post("/artifacts/upload", tags=["artifacts"])
async def upload_artifact(
    file: UploadFile = File(..., description="ZIP of generated files"),
    workspace_id: str = Form(...),
    run_id: str = Form(...),
    stage_key: str = Form(...),
    artifact_type: str = Form(...),
    pipeline: str = Form(...),
) -> dict:
    """Accept a ZIP of generated stage output, upload to the artifact store, and create a record.

    Returns the new artifact_id. Call POST /pipeline-runs/{id}/complete afterward
    with this ID so the run's artifact_ids are linked before the approval gate opens.
    """
    store = get_store()
    run = store.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"unknown run: {run_id}")

    settings = get_settings()
    astore = get_artifact_store()
    art_id = str(uuid.uuid4())
    s3_prefix = getattr(settings, "fe_s3_prefix", "fe")

    # Get next version for this workspace/stage/artifact-type combo (FR-026: immutable versions)
    artifact_version = store.next_version(pipeline, stage_key, artifact_type, workspace_id=workspace_id)

    key_prefix = artifact_key(
        s3_prefix, run.kb_application_id, workspace_id, artifact_type, artifact_version, art_id
    )

    tmp = Path(tempfile.mkdtemp(prefix="aidlc_upload_"))
    try:
        zip_bytes = await file.read()
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            zf.extractall(tmp)

        manifest = astore.put_tree(
            tmp, key_prefix,
            artifact_id=art_id,
            artifact_type=artifact_type,
            version=artifact_version,
            project_id=run.kb_application_id,
            workspace_id=workspace_id,
        )

        if astore.kind == "s3":
            bucket = getattr(astore, "bucket", settings.fe_s3_bucket)
            manifest_uri = f"s3://{bucket}/{key_prefix}/manifest.json"
        else:
            root = str(getattr(astore, "root", "."))
            manifest_uri = f"file:///{root}/{key_prefix}/manifest.json"

        artifact = SdlcArtifact(
            id=art_id,
            kb_application_id=run.kb_application_id,
            pipeline=pipeline,
            stage_key=stage_key,
            artifact_type=artifact_type,
            version=artifact_version,
            workspace_id=workspace_id,
            storage_kind=astore.kind,
            manifest_uri=manifest_uri,
            files=[{"path": f.path, "sha256": f.sha256, "size": f.size, "content_type": f.content_type}
                   for f in manifest.files],
            status=ArtifactStatus.IN_REVIEW,
        )
        store.put_artifact(artifact)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    return {"artifact_id": art_id, "files": len(manifest.files), "key_prefix": key_prefix}


@router.post("/pipeline-runs/{run_id}/approvals", tags=["approvals"])
async def approve_run(
    run_id: str, body: ApprovalRequest, pipeline: str | None = None
) -> dict:
    """Approve or reject every artefact a run produced.

    In langgraph mode a run that belongs to a graph thread is NOT approved here:
    the decision is recorded on the run and the worker feeds it to the graph as
    Command(resume=...), which performs the approval inside the gate node. That
    keeps graph and stage execution in one process (single checkpointer).
    """
    service = _service_for_run(run_id, pipeline)
    run = service.store.get_run(run_id)
    if (run is not None and getattr(run, "graph_thread_id", None)
            and getattr(service.settings, "fe_orchestrator", "manual") == "langgraph"):
        if run.state is not StageState.WAITING_FOR_APPROVAL:
            raise HTTPException(status_code=409, detail=f"run {run_id} is {run.state.value}, not waiting_for_approval")
        run.resume_intent = {"decision": body.decision, "approver": body.approver, "comment": body.comment,
                             "persona": getattr(body, "persona", None), "expected_version": body.expected_version,
                             "recorded_at": datetime.now(timezone.utc).isoformat()}
        service.store.save_run(run)
        return {"run": run.model_dump(mode="json"), "approvals": [], "next_stage": None,
                "orchestrated": True, "detail": "decision recorded; the orchestrator applies it and continues the thread"}
    try:
        outcome = service.approve(
            run_id,
            approver=body.approver,
            decision=body.decision,
            comment=body.comment,
            expected_version=body.expected_version,
        )
    except ApprovalError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "run": outcome["run"].model_dump(mode="json"),
        "approvals": [a.model_dump(mode="json") for a in outcome["approvals"]],
        "next_stage": outcome["next_stage"],
    }


@router.post("/artifacts/{artifact_id}/approvals", tags=["approvals"])
async def approve_artifact(
    artifact_id: str, body: ApprovalRequest, pipeline: str | None = None
) -> dict:
    """Approve or reject one artefact version (PRD 9.1/9.3)."""
    store = get_store()
    artifact = store.get_artifact(artifact_id)
    if artifact is None:
        raise HTTPException(status_code=404, detail=f"unknown artifact: {artifact_id}")
    if not artifact.run_id:
        raise HTTPException(
            status_code=409,
            detail="artifact has no originating run; approve through the run endpoint",
        )
    return await approve_run(
        artifact.run_id, body, pipeline or artifact.pipeline
    )


def _visible_to(rows, pipeline_obj, principal: Principal):
    """FR-P1: Global-workspace roles never see code artefacts.

    Filtered here, in the read path, rather than per-route. A route that forgets a
    dependency then fails closed instead of leaking source code, and the same rule
    covers list, detail and traceability without being restated.
    """
    if principal.can_see_code:
        return rows, 0
    visible = []
    withheld = 0
    for artifact in rows:
        try:
            target = pipeline_obj.stage(artifact.stage_key).target
        except KeyError:
            target = None
        if code_artifact(artifact.artifact_type, target):
            withheld += 1
            continue
        visible.append(artifact)
    return visible, withheld


@router.get("/artifacts", tags=["artifacts"])
async def list_artifacts(
    pipeline: str | None = None,
    stage: str | None = None,
    artifact_type: str | None = None,
    status: str | None = None,
    workspace_id: str | None = None,
    epic_id: str | None = None,
    tier: str | None = None,
    include_superseded: bool = False,
    principal: Principal = Depends(current_principal),
) -> dict:
    """Artefacts across both tiers.

    `pipeline` omitted means *every* pipeline, not the active one. With two tiers
    a default of "Global" would silently hide all Mini Workspace work, and the
    caller would have no way to tell an empty EPIC from a filtered view.
    """
    store = get_store()
    parsed = ArtifactStatus(status.upper()) if status else None

    if pipeline:
        pipelines = [get_pipeline(pipeline)]
    else:
        pipelines = list(registry().values())

    rows = []
    withheld = 0
    for pipeline_obj in pipelines:
        found = store.list_artifacts(
            pipeline_obj.name, stage, artifact_type, parsed,
            workspace_id=workspace_id, epic_id=epic_id, tier=tier,
        )
        if not include_superseded:
            found = [a for a in found if a.status is not ArtifactStatus.SUPERSEDED]
        visible, withheld_here = _visible_to(found, pipeline_obj, principal)
        rows.extend(visible)
        withheld += withheld_here

    payload = {
        "pipelines": [p.name for p in pipelines],
        "workspace_id": workspace_id,
        "epic_id": epic_id,
        "count": len(rows),
        "items": [a.model_dump(mode="json") for a in rows],
    }
    if withheld:
        # Say that something was withheld rather than silently shortening the list:
        # a business user seeing 4 of 9 artefacts with no explanation files a bug.
        payload["withheld"] = {
            "count": withheld,
            "reason": "code artefacts are not visible to the Global Workspace",
        }
    return payload


@router.get("/artifacts/{artifact_id}", tags=["artifacts"])
async def get_artifact(
    artifact_id: str,
    principal: Principal = Depends(current_principal),
) -> dict:
    store = get_store()
    artifact = store.get_artifact(artifact_id)
    if artifact is None:
        raise HTTPException(status_code=404, detail=f"unknown artifact: {artifact_id}")

    if not principal.can_see_code:
        try:
            target = get_pipeline(artifact.pipeline).stage(artifact.stage_key).target
        except Exception:  # noqa: BLE001
            target = None
        if code_artifact(artifact.artifact_type, target):
            raise HTTPException(
                status_code=403,
                detail=(
                    "this artefact is source code, which the Global Workspace "
                    "cannot access. A Hybrid Workspace role is required."
                ),
            )
    # Where the body lives and how to fetch it without going through the API:
    # presigned GET per file (S3) or file:// URIs (local). Git-ref artefacts
    # expose the repo/commit/PR instead. Short TTL; the UI re-fetches on expiry.
    body: dict = {"storage_kind": artifact.storage_kind or "worktree", "files": []}
    try:
        if artifact.storage_kind in ("s3", "local") and artifact.manifest_uri:
            from app.agentic_platform.fe_core.artifacts.reader import artifact_prefix  # noqa: PLC0415
            from app.agentic_platform.fe_core.artifacts.store import get_artifact_store  # noqa: PLC0415
            astore = get_artifact_store()
            prefix = artifact_prefix(artifact)
            if prefix:
                body["manifest_url"] = astore.presign_get(f"{prefix}/manifest.json")
                for f in artifact.files or []:
                    body["files"].append({**f, "url": astore.presign_get(f"{prefix}/{f['path']}")})
                body["expires_in_seconds"] = 900
        elif artifact.storage_kind == "git":
            body.update({"git_repo": artifact.git_repo, "git_branch": artifact.git_branch,
                         "git_commit": artifact.git_commit, "pr_url": artifact.pr_url})
    except Exception as exc:  # noqa: BLE001
        body["detail"] = f"presign unavailable: {exc}"
    return {
        "artifact": artifact.model_dump(mode="json"),
        "approvals": [a.model_dump(mode="json")
                      for a in store.approvals_for(artifact_id)],
        "body": body,
    }


# `inputs` and `.upstream` are the materialised upstream artefacts, `.mcp-config.json`
# the session's tool config -- none of it is this artefact's content.
_DOWNLOAD_SKIP = {".git", "__pycache__", ".claude", "node_modules", "inputs", ".upstream", ".mcp-config.json"}

#: Native content types for the document formats the pipeline produces.
#: `mimetypes.guess_type` is the fallback; these entries cover the ones it gets
#: wrong or platform-dependently (markdown, drawio, docx on a bare registry).
_NATIVE_MIME = {
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".drawio": "application/xml",
    ".docx": ("application/vnd.openxmlformats-officedocument"
              ".wordprocessingml.document"),
    ".xlsx": ("application/vnd.openxmlformats-officedocument"
              ".spreadsheetml.sheet"),
    ".yaml": "application/yaml",
    ".yml": "application/yaml",
    ".feature": "text/plain",
    ".rego": "text/plain",
}


def _local_body(artifact) -> Path | None:
    """Local directory/file for an artefact body via the ArtifactStore (S3/local),
    falling back to the worktree path. None for git-ref artefacts."""
    from app.agentic_platform.fe_core.artifacts.reader import materialise  # noqa: PLC0415
    try:
        local = materialise(artifact)
    except Exception:  # noqa: BLE001
        local = None
    if local is not None:
        return Path(local)
    return Path(artifact.path) if artifact.path else None


@router.get("/artifacts/{artifact_id}/versions", tags=["artifacts"])
async def list_artifact_versions(artifact_id: str) -> dict:
    """List all versions of an artifact for version history display."""
    store = get_store()

    # Get the artifact to find its pipeline, stage, and type
    artifact = store.get_artifact(artifact_id)
    if not artifact:
        raise HTTPException(status_code=404, detail=f"Artifact {artifact_id} not found")

    # Get all versions for this stage/type/workspace combo
    all_artifacts = store.list_artifacts(
        artifact.pipeline,
        artifact.stage_key,
        artifact.artifact_type,
        workspace_id=artifact.workspace_id
    )

    # All returned artifacts share the same stage/type/workspace — these are the versions
    versions = [
        {
            "version": a.version,
            "status": a.status.value,
            "created_at": a.created_at.isoformat() if a.created_at else None,
            "artifact_id": a.id,
        }
        for a in all_artifacts
    ]

    # Sort by version descending (newest first)
    versions.sort(key=lambda x: x["version"], reverse=True)

    return {"versions": versions}


@router.get("/artifacts/{artifact_id}/download", tags=["artifacts"])
async def download_artifact(
    artifact_id: str,
    file: str | None = Query(default=None, description="a specific file inside the artefact"),
    all: bool = Query(default=False, description="true = every file as a zip"),
    principal: Principal = Depends(current_principal),
) -> StreamingResponse:
    """Download an artifact in its original document format.

    A single-file artifact (the common case: one .md, .docx, .json ...) is
    streamed as that file, with its native content type and original filename,
    so the browser saves a document rather than an archive. Only a multi-file
    artifact falls back to a zip of the run worktree, excluding VCS and cache
    directories. Filenames carry the artifact type and version so multiple
    downloads stay identifiable.
    """
    store = get_store()
    artifact = store.get_artifact(artifact_id)
    if artifact is None:
        raise HTTPException(status_code=404, detail=f"unknown artifact: {artifact_id}")

    # FR-P1: code artefacts are hidden from Global-workspace callers.
    if not principal.can_see_code:
        try:
            target = get_pipeline(artifact.pipeline).stage(artifact.stage_key).target
        except Exception:  # noqa: BLE001
            target = None
        if code_artifact(artifact.artifact_type, target):
            raise HTTPException(
                status_code=403,
                detail="code artefacts require a Hybrid Workspace role",
            )

    artifact_path = _local_body(artifact)
    if not artifact_path or not artifact_path.exists():
        raise HTTPException(
            status_code=404,
            detail=(
                f"artifact files not found on disk at '{artifact.path}'. "
                "The run worktree may have been cleaned up."
            ),
        )

    if artifact_path.is_dir():
        files = [
            fp for fp in sorted(artifact_path.rglob("*"))
            if fp.is_file()
            and not any(part in _DOWNLOAD_SKIP for part in fp.parts)
        ]
    else:
        files = [artifact_path]

    if not files:
        raise HTTPException(
            status_code=404,
            detail=f"artifact '{artifact_id}' has no downloadable files",
        )

    safe_type = artifact.artifact_type.replace("/", "_").replace(" ", "-")

    # Default to the primary document (`<type>.md`, or the one file asked for) so
    # the browser saves a document, not an archive; `?all=true` still zips.
    if not all:
        chosen = None
        if file:
            chosen = next((fp for fp in files if fp.name == file
                           or (artifact_path.is_dir() and str(fp.relative_to(artifact_path)).replace("\\", "/") == file)), None)
            if chosen is None:
                raise HTTPException(status_code=404, detail=f"no file '{file}' in artifact {artifact_id}")
        elif len(files) > 1:
            primary = [fp for fp in files if fp.stem == artifact.artifact_type and fp.suffix.lower() in (".md", ".docx", ".json", ".yaml", ".yml", ".drawio")]
            chosen = primary[0] if primary else None
        if chosen is not None:
            files = [chosen]

    if len(files) == 1:
        # Original document, original type — no archive wrapper.
        single = files[0]
        suffix = single.suffix.lower()
        media_type = (
            _NATIVE_MIME.get(suffix)
            or mimetypes.guess_type(single.name)[0]
            or "application/octet-stream"
        )
        filename = f"{safe_type}-v{artifact.version}{single.suffix}"
        return StreamingResponse(
            io.BytesIO(single.read_bytes()),
            media_type=media_type,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    # Multiple files: a zip is the only faithful single download.
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for file_path in files:
            arcname = (
                file_path.relative_to(artifact_path)
                if artifact_path.is_dir() else file_path.name
            )
            zf.write(file_path, arcname)
    buf.seek(0)

    filename = f"{safe_type}-v{artifact.version}.zip"
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# Text extensions we try to read as document content.
_TEXT_EXTENSIONS = {
    ".md", ".txt", ".rst", ".html", ".htm", ".json", ".yaml", ".yml",
    ".csv", ".xml", ".toml", ".ini", ".cfg",
}


@router.get("/artifacts/{artifact_id}/content", tags=["artifacts"])
async def artifact_content(
    artifact_id: str,
    principal: Principal = Depends(current_principal),
) -> dict:
    """Return the text content of an artifact's files for in-browser viewing.

    Reads every text file in the artifact's worktree path and returns them
    as a list of ``{filename, content}`` objects. Binary files are skipped.
    Returns 404 when the artifact path no longer exists on disk (the worktree
    may have been cleaned up after download).
    """
    store = get_store()
    artifact = store.get_artifact(artifact_id)
    if artifact is None:
        raise HTTPException(status_code=404, detail=f"unknown artifact: {artifact_id}")

    if not principal.can_see_code:
        try:
            target = get_pipeline(artifact.pipeline).stage(artifact.stage_key).target
        except Exception:  # noqa: BLE001
            target = None
        if code_artifact(artifact.artifact_type, target):
            raise HTTPException(
                status_code=403,
                detail="code artefacts require a Hybrid Workspace role",
            )

    artifact_path = _local_body(artifact)
    if not artifact_path or not artifact_path.exists():
        raise HTTPException(
            status_code=404,
            detail=(
                f"artifact files not found on disk at '{artifact.path}'. "
                "The run worktree may have been cleaned up."
            ),
        )

    files: list[dict] = []
    paths = (
        sorted(artifact_path.rglob("*"))
        if artifact_path.is_dir()
        else [artifact_path]
    )
    for fp in paths:
        if not fp.is_file():
            continue
        if any(part in _DOWNLOAD_SKIP for part in fp.parts):
            continue
        if fp.suffix.lower() not in _TEXT_EXTENSIONS:
            continue
        try:
            content = fp.read_text(encoding="utf-8", errors="replace")
            rel = str(fp.relative_to(artifact_path)) if artifact_path.is_dir() else fp.name
            files.append({"filename": rel, "content": content})
        except OSError:
            continue

    return {
        "artifact_id": artifact_id,
        "artifact_type": artifact.artifact_type,
        "version": artifact.version,
        "files": files,
    }


class ReviseArtifactRequest(BaseModel):
    """Inline edit of a document that is awaiting approval."""

    content: str = Field(min_length=1, description="Full edited Markdown for the document")
    comment: str | None = Field(default=None, description="Optional per-document note")


def _revise(artifact_id: str, content_md: str, comment: str | None,
            principal: Principal) -> dict:
    """Shared body for the inline-edit (PUT) and file-upload (POST) revision routes.

    Creates a human-authored next version of the artefact and repoints its run at it;
    the editor identity is taken from the token, never the client body.
    """
    store = get_store()
    artifact = store.get_artifact(artifact_id)
    if artifact is None:
        raise HTTPException(status_code=404, detail=f"unknown artifact: {artifact_id}")

    if not principal.can_see_code:
        try:
            target = get_pipeline(artifact.pipeline).stage(artifact.stage_key).target
        except Exception:  # noqa: BLE001
            target = None
        if code_artifact(artifact.artifact_type, target):
            raise HTTPException(
                status_code=403,
                detail="code artefacts require a Hybrid Workspace role",
            )

    service = _service(artifact.pipeline)
    editor = principal.email or principal.subject
    try:
        revision = service.revise_artifact(
            artifact, content_md=content_md, editor=editor, comment=comment)
    except ApprovalError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"artifact": revision.model_dump(mode="json")}


@router.put("/artifacts/{artifact_id}/content", tags=["artifacts"])
async def revise_artifact_content(
    artifact_id: str,
    body: ReviseArtifactRequest,
    principal: Principal = Depends(current_principal),
) -> dict:
    """Save a reviewer's inline Markdown edit as the artefact's next version."""
    return _revise(artifact_id, body.content, body.comment, principal)


@router.post("/artifacts/{artifact_id}/revision", tags=["artifacts"])
async def upload_artifact_revision(
    artifact_id: str,
    file: UploadFile = File(...),
    comment: str | None = Form(default=None),
    principal: Principal = Depends(current_principal),
) -> dict:
    """Save an uploaded edited file (.md/.txt/.html/.docx/.pdf) as the next version.

    The upload is converted to Markdown with the same extractor the RED-intake path
    uses, off the event loop because extraction is CPU-bound.
    """
    from app.agentic_platform.fe_core.rag.extract import UnreadableUpload, extract_text

    raw = await file.read()
    try:
        text, _how = await asyncio.to_thread(extract_text, raw, file.filename or "upload")
    except UnreadableUpload as exc:
        raise HTTPException(status_code=415, detail=str(exc)) from exc
    return _revise(artifact_id, text, comment, principal)


@router.get("/traceability/{artifact_id}", tags=["artifacts"])
async def traceability(artifact_id: str) -> dict:
    """Upstream and downstream links through the SDLC chain (FR-027)."""
    store = get_store()
    if store.get_artifact(artifact_id) is None:
        raise HTTPException(status_code=404, detail=f"unknown artifact: {artifact_id}")
    lineage = store.lineage(artifact_id)
    art = store.get_artifact(artifact_id)
    return {
        "artifact_id": artifact_id,
        "upstream": [a.model_dump(mode="json") for a in lineage["upstream"]],
        "downstream": [a.model_dump(mode="json") for a in lineage["downstream"]],
        "links": list(art.links or []) if art else [],
    }


@router.get("/artifacts/{artifact_id}/lineage", tags=["artifacts"])
async def artifact_lineage(
    artifact_id: str,
    direction: str = Query("up", pattern="^(up|down)$"),
    workspace_id: str | None = Query(default=None),
    max_depth: int = Query(default=20, ge=1, le=50),
) -> dict:
    """Full lineage chain via PostgreSQL recursive CTE over fe_artifact_relationships.

    direction="up"   — ancestors (reverse: find BRD from test artifact)
    direction="down" — descendants (forward: find tests from BRD)

    Each hop in "chain" includes: artifact_id, kind, status, depth,
    relationship_type, workspace_id, workflow_run_id, model, created_at.

    Falls back to an empty chain when the DB pool is unavailable.
    """
    from app.services.traceability import traverse_lineage  # noqa: PLC0415
    chain = await traverse_lineage(
        artifact_id,
        direction=direction,  # type: ignore[arg-type]
        workspace_id=workspace_id,
        max_depth=max_depth,
    )
    return {
        "artifact_id": artifact_id,
        "direction": direction,
        "depth": max(h["depth"] for h in chain) if chain else 0,
        "chain": chain,
    }


@router.get("/artifacts/{artifact_id}/orphan-check", tags=["artifacts"])
async def artifact_orphan_check(artifact_id: str, workspace_id: str = Query(...)) -> dict:
    """Check whether an artifact has any recorded upstream or downstream relationships.

    Returns orphan=True when the artifact has no entry in fe_artifact_relationships.
    Falls back to orphan=None when the DB pool is unavailable.
    """
    from app.services.traceability import orphan_artifacts  # noqa: PLC0415
    orphans = await orphan_artifacts(workspace_id)
    return {
        "artifact_id": artifact_id,
        "workspace_id": workspace_id,
        "orphan": artifact_id in orphans,
    }


@router.get("/artifacts/{artifact_id}/links", tags=["artifacts"])
async def artifact_links(artifact_id: str, direction: str = Query("both", pattern="^(from|to|both)$")) -> dict:
    """Traceability links (AIDLC `fe_artifact_links` equivalent). `from`: links this
    artefact carries (DERIVES_FROM upstream artefacts, GROUNDS KB chunks); `to`:
    artefacts whose links point at this one."""
    store = get_store()
    art = store.get_artifact(artifact_id)
    if art is None:
        raise HTTPException(status_code=404, detail=f"unknown artifact: {artifact_id}")
    out: dict = {"artifact_id": artifact_id}
    if direction in ("from", "both"):
        out["from"] = list(art.links or []) + [
            {"type": "DERIVES_FROM", "artifact_id": sid, "implicit": True}
            for sid in (art.source_artifact_ids or [])
            if not any(l.get("artifact_id") == sid for l in (art.links or []))]
    if direction in ("to", "both"):
        out["to"] = [{"type": l.get("type"), "artifact_id": a.id, "artifact_type": a.artifact_type,
                      "version": a.version}
                     for a in store.list_artifacts()
                     for l in (a.links or [])
                     if l.get("artifact_id") == artifact_id]
    return out


@router.get("/outbox", tags=["artifacts"])
async def outbox(limit: int = Query(default=50, ge=1, le=500)) -> dict:
    """Pending indexing work (FR-029). Visible so a stalled outbox is noticed."""
    rows = get_store().pending_outbox(limit)
    return {"pending": len(rows), "items": [e.model_dump(mode="json") for e in rows]}



@router.get("/artifacts/{artifact_id}/export.html", tags=["artifacts"])
async def export_artifact_html(
    artifact_id: str,
    file: str | None = Query(default=None, description="a specific .md inside the artefact"),
    principal: Principal = Depends(current_principal),
):
    """Render the artefact's Markdown deliverable(s) as a self-contained HTML page,
    viewable inline in the browser (no download). Every `[CARD-ID]` citation is
    highlighted and linked to the card KB endpoint, so a reviewer can see what a
    document is grounded on. Multiple `.md` files (e.g. prd + brd +
    business-rules) are offered as tabs; `?file=` picks one."""
    from html import escape  # noqa: PLC0415
    from fastapi.responses import HTMLResponse  # noqa: PLC0415

    store = get_store()
    artifact = store.get_artifact(artifact_id)
    if artifact is None:
        raise HTTPException(status_code=404, detail=f"unknown artifact: {artifact_id}")
    if not principal.can_see_code:
        try:
            target = get_pipeline(artifact.pipeline).stage(artifact.stage_key).target
        except Exception:  # noqa: BLE001
            target = None
        if code_artifact(artifact.artifact_type, target):
            raise HTTPException(status_code=403,
                                detail="source-code artefacts are not viewable from the Global Workspace")
    root = _local_body(artifact)
    if root is None or not root.exists():
        raise HTTPException(status_code=404, detail="artefact body is not available (git-ref or missing)")
    if root.is_file():
        docs = [root]
    else:
        primary = root / f"{artifact.artifact_type}.md"
        others = sorted(p for p in root.rglob("*.md")
                        if p != primary and "inputs" not in p.parts and ".upstream" not in p.parts)
        docs = ([primary] if primary.exists() else []) + others
    if not docs:
        raise HTTPException(status_code=422, detail="artefact has no Markdown deliverable to view")
    chosen = docs[0]
    if file:
        chosen = next((d for d in docs if d.name == file or str(d.relative_to(root)) == file), chosen)

    try:
        import markdown  # noqa: PLC0415
        body_html = markdown.markdown(
            chosen.read_text(encoding="utf-8", errors="replace"),
            extensions=["tables", "fenced_code", "toc", "sane_lists"])
    except ImportError:
        body_html = "<pre>" + escape(chosen.read_text(encoding="utf-8", errors="replace")) + "</pre>"

    # highlight [CARD-ID] citations and link them to the card KB
    from app.agentic_platform.fe_core.kb.cards import CARD_ID_RE, CITATION_RE  # noqa: PLC0415
    kb_base = f"{get_settings().fe_api_prefix}/projects/{artifact.kb_application_id}/kb/cards/"

    def _cite(m: "re.Match[str]") -> str:
        inner = m.group(1)
        linked = CARD_ID_RE.sub(
            lambda c: f'<a class="cite" href="{kb_base}{c.group(1)}" target="_blank" title="open card">{c.group(1)}</a>',
            inner)
        return f'<span class="citation">[{linked}]</span>'
    body_html = CITATION_RE.sub(_cite, body_html)

    tabs = "".join(
        f'<a class="tab{" active" if d == chosen else ""}" href="?file={escape(d.name)}">{escape(d.name)}</a>'
        for d in docs)
    title = f"{artifact.artifact_type} v{artifact.version} — {artifact.stage_key}"
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>{escape(title)}</title>
<style>
 body{{font-family:-apple-system,Segoe UI,Roboto,sans-serif;max-width:1000px;margin:24px auto;padding:0 20px;color:#1e293b;line-height:1.55}}
 .meta{{color:#64748b;font-size:12px;margin-bottom:12px}} .tabs{{margin:8px 0 16px;border-bottom:1px solid #e2e8f0}}
 .tab{{display:inline-block;padding:6px 12px;margin-right:4px;border:1px solid #e2e8f0;border-bottom:none;border-radius:6px 6px 0 0;color:#0369a1;text-decoration:none;font-size:13px}}
 .tab.active{{background:#e0f2fe;font-weight:600}}
 table{{border-collapse:collapse;width:100%;font-size:13px;margin:10px 0}} th,td{{border:1px solid #e2e8f0;padding:6px 8px;vertical-align:top}} th{{background:#f1f5f9}}
 code{{background:#f1f5f9;padding:1px 4px;border-radius:3px;font-size:12px}} pre{{background:#0f172a;color:#e2e8f0;padding:12px;border-radius:6px;overflow-x:auto}} pre code{{background:none;color:inherit}}
 h1,h2,h3{{color:#1a3a5c}} .citation{{background:#fef9c3;border-radius:3px;padding:0 3px;font-size:12px}} a.cite{{color:#92400e;font-weight:600;text-decoration:none}} a.cite:hover{{text-decoration:underline}}
 blockquote{{border-left:3px solid #cbd5e1;margin:8px 0;padding:4px 12px;color:#475569}}
</style></head><body>
<div class="meta">{escape(artifact.workspace_id or '')} &middot; {escape(artifact.stage_key)} &middot; {escape(artifact.artifact_type)} v{artifact.version} &middot; {escape(artifact.status.value)} &middot; artefact {escape(artifact.id)} &middot; <span class="citation">[ID]</span> = knowledge-card citation (click to open the card)</div>
<div class="tabs">{tabs}</div>
{body_html}
</body></html>"""
    return HTMLResponse(page)


@router.get("/artifacts/{artifact_id}/export.docx", tags=["artifacts"])
async def export_artifact_docx(
    artifact_id: str,
    principal: Principal = Depends(current_principal),
) -> StreamingResponse:
    """Render the artefact's primary Markdown deliverable (`<type>.md`, else the
    first .md) as a DOCX -- the console's per-stage document export (mirrors the
    AIDLC `/{stage}/export.docx`). Renderer: `src/api/scripts/md_to_docx.py`.
    Branding templates come later."""
    import importlib.util  # noqa: PLC0415
    import sys  # noqa: PLC0415
    import tempfile  # noqa: PLC0415

    store = get_store()
    artifact = store.get_artifact(artifact_id)
    if artifact is None:
        raise HTTPException(status_code=404, detail=f"unknown artifact: {artifact_id}")
    if not principal.can_see_code:
        try:
            target = get_pipeline(artifact.pipeline).stage(artifact.stage_key).target
        except Exception:  # noqa: BLE001
            target = None
        if code_artifact(artifact.artifact_type, target):
            raise HTTPException(status_code=403,
                                detail="source-code artefacts are not exportable from the Global Workspace")
    root = _local_body(artifact)
    if root is None or not root.exists():
        raise HTTPException(status_code=404, detail="artefact body is not available (git-ref or missing)")
    src: Path | None = root if root.is_file() else None
    if src is None:
        candidates = [root / f"{artifact.artifact_type}.md"] + sorted(root.rglob("*.md"))
        src = next((c for c in candidates if c.exists()), None)
    if src is None:
        raise HTTPException(status_code=422, detail="artefact has no Markdown deliverable to export")

    from app.agentic_platform.fe_core.config import API_ROOT  # noqa: PLC0415
    script = Path(API_ROOT) / "scripts" / "md_to_docx.py"
    spec = importlib.util.spec_from_file_location("fe_md_to_docx", script)
    if spec is None or spec.loader is None:
        raise HTTPException(status_code=500, detail="md_to_docx renderer not found")
    mod = sys.modules.get("fe_md_to_docx")
    if mod is None:
        mod = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(mod)
        except ImportError as exc:                      # python-docx missing
            raise HTTPException(status_code=501, detail=f"DOCX export unavailable: {exc}") from exc
        sys.modules["fe_md_to_docx"] = mod

    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / f"{artifact.artifact_type}-v{artifact.version}.docx"
        try:
            mod.build(src, out, title=None,
                      subtitle=(f"{artifact.workspace_id} - {artifact.stage_key} - "
                                f"v{artifact.version} - {artifact.status.value}"),
                      toc=True)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=f"DOCX render failed: {exc}") from exc
        data = out.read_bytes()
    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition":
                 f'attachment; filename="{artifact.artifact_type}-v{artifact.version}.docx"'},
    )


@router.get("/audit", tags=["audit"])
async def audit_log(
    workspace_id: str | None = None,
    artifact_id: str | None = None,
    limit: int = Query(200, ge=1, le=2000),
    principal: Principal = Depends(current_principal),
) -> dict:
    """Append-only audit trail (approvals, orchestration transitions, dev/pr syncs)
    -- the AIDLC `fe_audit_log` equivalent, backed by `fe_state_audit` (postgres)
    or the `audit[]` block of state.json."""
    store = get_store()
    fn = getattr(store, "audit_log", None)
    rows = fn(workspace_id=workspace_id, artifact_id=artifact_id, limit=limit) if fn else []
    return {"count": len(rows), "entries": rows}
