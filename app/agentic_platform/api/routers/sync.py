"""Artefact synchronization — the server side of the post-generation KB sync hook.

PRD 9.1: `POST /api/v1/sync/artifacts` — "Idempotently publish a generated
artifact from worker or VSCode."

Why this endpoint matters more than it looks
-------------------------------------------
It is what makes drawio tab 1's two workspace modes real. A developer generating
in VSCode (Mode B) has no access to the worker's in-process outbox, so without an
HTTP entry point their output never reaches the KB and the Web UI (Mode A) shows
nothing. That is the difference between "generate in VSCode, review in the Web UI,
and carry on in VSCode" and two disconnected tools.

Idempotency
-----------
Publication is idempotent twice over: this service deduplicates on
(pipeline, stage, artefact type, checksum), and the knowledge base deduplicates ingestion on
SHA-256. Re-running the hook after a network failure is safe.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Body, Header, HTTPException, Query
from pydantic import BaseModel, Field

from app.agentic_platform.api.services.jobs import JobService
from app.agentic_platform.fe_core.kb.models import ArtifactStatus, SdlcArtifact
from app.agentic_platform.fe_core.pipeline.registry import PipelineNotFoundError, get_pipeline
from app.agentic_platform.fe_core.store import get_store, make_idempotency_key, sha256

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/sync", tags=["sync"])


class SyncArtifactRequest(BaseModel):
    """A generated artefact published from outside the worker."""

    pipeline: str | None = Field(default=None, description="Defaults to FE_PIPELINE.")
    stage: str = Field(description="Stage key, e.g. 'ui-code'.")
    artifact_type: str = Field(description="Declared output type, e.g. 'ui-source'.")
    kb_application: str = Field(description="KB application name or id.")

    workspace: str | None = Field(
        default=None,
        description="Directory holding the generated files. Read to build content.",
    )
    workspace_id: str | None = Field(
        default=None,
        description=(
            "Which workspace instance this belongs to (PRD 5.3). Required for a "
            "Mini-tier stage: a developer working on EPIC 3 in VSCode must not "
            "publish into EPIC 1."
        ),
    )
    persona: str | None = Field(
        default=None,
        description="Persona that produced the artefact (FR-P3).",
    )
    content: str | None = Field(
        default=None,
        description="Inline content. Use instead of `workspace` for a single document.",
    )
    run_id: str | None = Field(
        default=None, description="Originating run, when there was one."
    )
    parent_artifact_id: str | None = None
    plugin: str | None = None
    plugin_version: str | None = None
    model: str | None = None

    # --- git-ref artefacts (Mini workspace code produced on a laptop) ---------
    # The body lives in Git; the platform records the reference. No local path
    # is needed or read, so this works from any laptop against an ECS API.
    git_repo: str | None = Field(default=None, description="Repository URL/slug, e.g. org/agentic_platform-angular")
    git_branch: str | None = None
    git_commit: str | None = Field(default=None, description="Commit SHA the artefact corresponds to")
    pr_url: str | None = None
    files: list[dict] = Field(default_factory=list,
                              description="[{path, sha256, size?, content?}] — content (optional) is stored "
                                          "in the artefact store so the UI can preview without Git access")
    summary: str | None = Field(default=None, description="SUMMARY.md text (stored when given)")


@router.post("/artifacts", status_code=202)
async def sync_artifact(
    body: SyncArtifactRequest = Body(...),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    x_user: str | None = Header(default=None, alias="X-User"),
) -> dict:
    """Register a generated artefact as a Draft and queue it for KB publication.

    Returns 202: the artefact is committed synchronously, publication is queued.
    Blocking on chunking and embedding would make a slow KB look like a failed
    hook, and hooks that appear to fail get disabled.
    """
    if not (body.workspace or body.content or body.git_commit or body.files):
        raise HTTPException(
            status_code=422,
            detail="supply a git reference (`git_repo` + `git_commit`), `files`, `content` (inline text) "
                   "or `workspace` (a directory the API can read)",
        )

    try:
        pipeline = get_pipeline(body.pipeline)
    except PipelineNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    try:
        stage = pipeline.stage(body.stage)
    except KeyError as exc:
        raise HTTPException(
            status_code=404, detail=f"unknown stage: {body.stage}"
        ) from exc

    if body.artifact_type not in stage.produces:
        raise HTTPException(
            status_code=422,
            detail=(
                f"stage '{stage.key}' declares outputs {stage.produces}; "
                f"'{body.artifact_type}' is not one of them. Publishing an "
                "undeclared type would break downstream eligibility."
            ),
        )

    service = JobService(pipeline=pipeline)
    store = get_store()

    # Resolve the project locally first: a workspace_id or an onboarded project
    # is authoritative here (the laptop is talking to THIS platform), and only
    # then fall back to the KB catalogue.
    from types import SimpleNamespace  # noqa: PLC0415
    app = None
    if body.workspace_id:
        ws_row = store.get_workspace(body.workspace_id)
        if ws_row is not None:
            app = SimpleNamespace(id=ws_row.kb_application_id, name=ws_row.kb_application_id)
    if app is None:
        for w in store.list_workspaces():
            if w.kb_application_id == body.kb_application:
                app = SimpleNamespace(id=w.kb_application_id, name=w.kb_application_id)
                break
    if app is None:
        try:
            app = await service.kb.resolve_application(body.kb_application)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=503, detail=f"KB unresolved: {exc}") from exc

    # Resolve the workspace before writing anything. A Mini-tier stage without a
    # workspace_id is refused rather than defaulted: silently landing EPIC 3's
    # code in EPIC 1 is worse than a 422 the hook can report.
    try:
        workspace = service._resolve_workspace(app.id, body.workspace_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if body.git_commit:
        checksum = sha256(f"git:{body.git_repo}@{body.git_commit}:{body.artifact_type}")
    elif body.files:
        checksum = sha256("|".join(f"{f.get('path')}:{f.get('sha256')}" for f in body.files))
    else:
        checksum = sha256(body.content or f"workspace:{body.workspace}")

    existing = store.find_artifact_by_checksum(
        pipeline.name, stage.key, body.artifact_type, checksum,
        workspace_id=workspace.id,
    )
    if existing is not None:
        # Idempotent replay: return the existing reference, create nothing.
        return {
            "artifact": existing.model_dump(mode="json"),
            "created": False,
            "detail": "identical content already registered; no new version created",
        }

    superseded = store.supersede_previous(
        pipeline.name, stage.key, body.artifact_type,
        workspace_id=workspace.id,
    )
    artifact = SdlcArtifact(
        id=uuid.uuid4().hex[:12],
        kb_application_id=app.id,
        pipeline=pipeline.name,
        stage_key=stage.key,
        sdlc_stage=stage.key,
        artifact_type=body.artifact_type,
        version=store.next_version(pipeline.name, stage.key, body.artifact_type,
                                   workspace_id=workspace.id),
        supersedes_id=superseded,
        parent_id=body.parent_artifact_id,
        workspace_id=workspace.id,
        tier=workspace.tier.value,
        epic_id=workspace.epic_id,
        produced_by_persona=body.persona,
        artifact_tier=stage.artifact_tier,
        path=body.workspace or "(inline)",
        content_uri=body.workspace,
        checksum=checksum,
        content_sha256=checksum,
        status=ArtifactStatus.DRAFT,
        source_workspace=body.workspace,
        run_id=body.run_id,
        plugin=body.plugin,
        plugin_version=body.plugin_version,
        model=body.model,
        runner="external",
        created_by=x_user or "vscode",
        created_at=datetime.now(timezone.utc),
        created_by_run=body.run_id,
    )
    # Where the body lives.
    if body.git_commit:
        artifact.storage_kind = "git"
        artifact.git_repo, artifact.git_branch, artifact.git_commit, artifact.pr_url = (
            body.git_repo, body.git_branch, body.git_commit, body.pr_url)
        artifact.content_uri = f"git+https://{(body.git_repo or '').removeprefix('https://')}@{body.git_commit}"
        artifact.path = artifact.content_uri
        artifact.files = [{k: v for k, v in f.items() if k != "content"} for f in body.files]
    elif body.content:
        artifact.storage_kind = "inline"
    # Optional preview material (SUMMARY.md, small files with content) -> artefact store,
    # so the console can show them without Git access.
    preview = [f for f in body.files if f.get("content")]
    if body.summary or preview:
        try:
            import tempfile  # noqa: PLC0415
            from pathlib import Path  # noqa: PLC0415
            from app.agentic_platform.fe_core.artifacts.store import artifact_key, get_artifact_store  # noqa: PLC0415
            settings = service.settings
            astore = get_artifact_store(settings)
            key = artifact_key(settings.fe_s3_prefix if astore.kind == "s3" else "fe", app.id,
                               workspace.id, body.artifact_type, artifact.version, artifact.id)
            with tempfile.TemporaryDirectory(prefix="fe-sync-") as tmp:
                root = Path(tmp)
                if body.summary:
                    (root / "SUMMARY.md").write_text(body.summary, encoding="utf-8")
                for f in preview:
                    dest = root / str(f["path"]).lstrip("/")
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_text(str(f["content"]), encoding="utf-8")
                manifest = astore.put_tree(root, key, artifact_id=artifact.id, artifact_type=body.artifact_type,
                                           version=artifact.version, project_id=app.id, workspace_id=workspace.id)
            artifact.manifest_uri = astore.uri(f"{key}/manifest.json")
            if not body.git_commit:
                artifact.storage_kind = astore.kind
                artifact.content_uri = astore.uri(key)
                artifact.path = astore.uri(key)
                artifact.files = [{"path": f.path, "sha256": f.sha256, "size": f.size} for f in manifest.files]
        except Exception as exc:  # noqa: BLE001
            logger.warning("sync: preview upload failed for %s: %s", artifact.id, exc)
    store.put_artifact(artifact)

    logger.info(
        "Synced %s v%d from %s (stage %s)",
        artifact.artifact_type, artifact.version, artifact.created_by, stage.key,
    )
    audit = getattr(get_store(), "audit", None)
    if audit is not None:
        try:
            audit(action="artifact.synced", actor=artifact.created_by, persona=body.persona,
                  workspace_id=artifact.workspace_id, artifact_id=artifact.id,
                  after={"status": artifact.status.value, "version": artifact.version},
                  detail={"stage": stage.key, "storage_kind": artifact.storage_kind,
                          "git_commit": artifact.git_commit, "pr_url": artifact.pr_url})
        except Exception:  # noqa: BLE001
            pass
    return {
        "artifact": artifact.model_dump(mode="json"),
        "created": True,
        "next_action": (
            f"awaiting approval by '{stage.approval_persona}' -- "
            f"POST /api/v1/artifacts/{artifact.id}/approvals"
        ),
    }
