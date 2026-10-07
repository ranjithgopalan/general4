"""Stage eligibility — pure functions over the store.

Kept separate from execution so the rules can be unit-tested without a runner,
a KB, or a model. These are the rules that give the pipeline its governance
value, so they are the most important thing in the codebase to have covered.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.agentic_platform.fe_core.kb.models import ArtifactStatus
from app.agentic_platform.fe_core.pipeline.models import (
    OwnerKind,
    Pipeline,
    Stage,
    StageState,
    StageStatus,
)


class StageBlockedError(RuntimeError):
    """The stage cannot be queued yet. Surfaced as HTTP 409."""


@dataclass(frozen=True)
class Eligibility:
    stage_key: str
    state: StageState
    ready: bool
    unmet_prerequisites: tuple[str, ...] = ()
    reason: str | None = None


def readable_workspace_ids(store, workspace_id: str | None) -> list[str] | None:
    """Workspaces whose approved artefacts a run in `workspace_id` may consume.

    A Mini Workspace reads its own plus its parent Global Workspace's -- M1
    Feature needs the approved EPIC. It must never read a sibling EPIC's work
    (FR-P4). None means unscoped, which is correct only for reporting.
    """
    if workspace_id is None:
        return None
    from app.agentic_platform.fe_core.workspaces.service import WorkspaceService

    return WorkspaceService(store).readable_workspace_ids(workspace_id) or [workspace_id]


def unmet_prerequisites(store, pipeline: Pipeline, stage: Stage,
                        workspace_id: str | None = None) -> list[str]:
    """Artefact types the stage consumes with no APPROVED version (FR-025).

    Scoped to the workspace, so EPIC 4 being finished does not make EPIC 7's
    stages look ready.

    Three-pass lookup (extended for the Architecture Workspace):
      1. pipeline.name       -- artefacts this pipeline itself produced.
      2. pipeline.parent     -- inherited_inputs from the parent (Global) tier.
      3. architecture pipeline -- ADR/NFR/SDD/SRD produced by the Architecture
         Workspace singleton (Global epic-set consumes SRD; Mini LLD consumes SDD).
    """
    readable = readable_workspace_ids(store, workspace_id)
    approved = {
        a.artifact_type
        for a in store.approved_artifacts(
            pipeline.name, stage.consumes, workspace_ids=readable,
        )
    }
    # Inherited inputs live in the parent tier's pipeline, so they are stored
    # under the parent's pipeline name rather than this one's.
    still_missing = set(stage.consumes) - approved
    inherited = still_missing & set(pipeline.inherited_inputs)
    if inherited and pipeline.parent:
        parent_approved = {
            a.artifact_type
            for a in store.approved_artifacts(
                pipeline.parent, inherited, workspace_ids=readable,
            )
        }
        still_missing -= parent_approved
    # Artefacts produced by the Architecture Workspace are stored under the
    # architecture pipeline name; query that separately for anything still missing.
    if still_missing:
        from app.agentic_platform.fe_core.config import get_settings  # noqa: PLC0415
        arch_name = getattr(get_settings(), "fe_pipeline_architecture", None)
        if arch_name:
            arch_approved = {
                a.artifact_type
                for a in store.approved_artifacts(
                    arch_name, list(still_missing), workspace_ids=readable,
                )
            }
            still_missing -= arch_approved
    return sorted(still_missing)


def evaluate(store, pipeline: Pipeline, stage: Stage,
             workspace_id: str | None = None) -> Eligibility:
    """Compute the stage's current state and whether it may be queued."""
    run = store.latest_run(pipeline.name, stage.key, workspace_id=workspace_id)
    missing = tuple(unmet_prerequisites(store, pipeline, stage, workspace_id))

    # An in-flight or finished run owns the state.
    if run is not None and run.state not in (
        StageState.NOT_READY, StageState.READY,
    ):
        return Eligibility(
            stage_key=stage.key,
            state=run.state,
            ready=run.state in (StageState.FAILED, StageState.CANCELLED)
            and not missing and _capability_ok(stage) is None,
            unmet_prerequisites=missing,
            reason=run.error,
        )

    capability_problem = _capability_ok(stage)
    if capability_problem:
        return Eligibility(stage.key, StageState.NOT_READY, False, missing,
                           capability_problem)
    if missing:
        return Eligibility(
            stage.key, StageState.NOT_READY, False, missing,
            f"awaiting APPROVED upstream artefact(s): {', '.join(missing)}",
        )
    return Eligibility(stage.key, StageState.READY, True, (), None)


def _capability_ok(stage: Stage) -> str | None:
    """None when the stage is implementable, else why not."""
    if stage.status is StageStatus.GAP and stage.owner.kind is not OwnerKind.BUILTIN:
        return f"capability gap: {stage.blocked_reason or 'not implemented'}"
    if stage.owner.kind is OwnerKind.EXTERNAL:
        return (
            "owned by an external harness "
            f"({stage.owner.note or 'see pipeline YAML'}); record the outcome via "
            "the approval endpoint"
        )
    return None


def require_runnable(store, pipeline: Pipeline, stage: Stage,
                     workspace_id: str | None = None) -> None:
    """Raise StageBlockedError unless the stage may be queued now."""
    verdict = evaluate(store, pipeline, stage, workspace_id)
    if verdict.ready:
        return
    if verdict.state in (StageState.QUEUED, StageState.RUNNING):
        raise StageBlockedError(
            f"stage '{stage.key}' is already {verdict.state.value}"
        )
    if verdict.state is StageState.WAITING_FOR_APPROVAL:
        raise StageBlockedError(
            f"stage '{stage.key}' is awaiting approval; approve or reject it first"
        )
    if verdict.state is StageState.COMPLETED:
        raise StageBlockedError(
            f"stage '{stage.key}' is already completed; a rerun creates a new "
            "version -- pass force=true to start one"
        )
    raise StageBlockedError(f"stage '{stage.key}' is not ready: {verdict.reason}")


def pipeline_status(store, pipeline: Pipeline,
                    workspace_id: str | None = None) -> list[dict]:
    """Per-stage view for the UI and CLI, for one workspace."""
    rows: list[dict] = []
    for stage in pipeline.ordered:
        verdict = evaluate(store, pipeline, stage, workspace_id)
        run = store.latest_run(pipeline.name, stage.key, workspace_id=workspace_id)
        # Fallback: legacy runs were persisted with workspace_id=null before the
        # workspace-scoped store was introduced.  If the workspace-filtered query
        # found nothing, try without the filter so run_id / timing still surface.
        if run is None and workspace_id:
            run = store.latest_run(pipeline.name, stage.key)
        artifacts = store.list_artifacts(pipeline.name, stage.key,
                                         workspace_id=workspace_id)
        rows.append({
            "tier": pipeline.tier,
            "workspace_id": workspace_id,
            "seq": stage.seq,
            "key": stage.key,
            "name": stage.name,
            "owner": stage.owner.qualified_skill or stage.owner.kind.value,
            "capability_status": stage.status.value,
            "state": verdict.state.value,
            "ready": verdict.ready,
            "unmet_prerequisites": list(verdict.unmet_prerequisites),
            "reason": verdict.reason,
            "approval_persona": stage.approval_persona,
            "approval_role": stage.approval_persona,   # retained alias
            "artifact_tier": stage.artifact_tier,
            "contributing_personas": list(stage.contributing_personas),
            "agent_group": stage.agent_group,
            "target": stage.target,
            "model": stage.model,
            "allowed_tools": stage.permissions.allow,
            "permission_policy": stage.permissions.policy,
            "run_id": run.run_id if run else None,
            "attempt": run.attempt if run else 0,
            "artifact_versions": {
                a.artifact_type: {"version": a.version, "status": a.status.value}
                for a in artifacts
                if a.status is not ArtifactStatus.SUPERSEDED
            },
        })
    return rows
