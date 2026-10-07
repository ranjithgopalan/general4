"""Durable pipeline jobs (PRD FR-015) and the approval gate (FR-024).

"Durable" here means every state change is persisted before it is acted on, so
create / status / cancel / retry keep working across an API restart. On startup
`recover_orphans()` re-queues anything that was mid-flight, because a RUNNING row
with no live task is otherwise stuck forever -- the failure mode the knowledge base has today
with its process-local job dicts.

The approval gate is API-side and human-driven on purpose. It cannot live inside
the agent loop: in CLI mode the runner's permission callbacks are not a reliable
control point, so a gate implemented there would appear to work and silently pass
everything.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.agentic_platform.fe_core.config import Settings, get_settings
from app.agentic_platform.fe_core.kb.gateway import KbGateway, build_gateway
from app.agentic_platform.fe_core.kb.models import ApprovalDecision, ArtifactStatus, SdlcArtifact
from app.agentic_platform.fe_core.pipeline.eligibility import StageBlockedError, require_runnable
from app.agentic_platform.fe_core.pipeline.models import (
    InvalidStateTransition,
    Pipeline,
    StageRun,
    StageState,
)
from app.agentic_platform.fe_core.pipeline.registry import get_pipeline
from app.agentic_platform.fe_core.store import (
    ConcurrencyError,
    JsonFileStore,
    get_store,
    make_idempotency_key,
    new_run,
)

# KB sync stages — these stage keys trigger KB refresh after user approval.
# Add or remove stage keys here as the pipeline evolves.
_KB_SYNC_STAGES: frozenset[str] = frozenset({
    "prd", "srd", "fsd", "architecture", "hld", "lld",
})

logger = logging.getLogger(__name__)


class ApprovalError(RuntimeError):
    """Invalid approval request. Surfaced as HTTP 409."""


class JobService:
    def __init__(
        self,
        pipeline: Pipeline | None = None,
        settings: Settings | None = None,
        store: JsonFileStore | None = None,
        kb: KbGateway | None = None,
    ):
        self.settings = settings or get_settings()
        self.pipeline = pipeline or get_pipeline()
        self.store = store or get_store()
        self.kb = kb or build_gateway(self.settings)
        self._tasks: dict[str, asyncio.Task] = {}

    # -- creation ---------------------------------------------------------
    async def create_run(
        self,
        stage_key: str,
        *,
        kb_application: str | None = None,
        initiated_by: str | None = None,
        initiated_by_persona: str | None = None,
        workspace_id: str | None = None,
        idempotency_key: str | None = None,
        force: bool = False,
        execute: bool = True,
        module_scope: str | None = None,
        gear_id: str | None = None,
        business_area: str | None = None,
        role: str | None = None,
    ) -> StageRun:
        """Queue a stage in one workspace. Idempotent with `idempotency_key`.

        `workspace_id` identifies which instance this run belongs to (PRD 5.3).
        A Mini-tier stage requires one: running "EPIC's UI code" without saying
        which EPIC is not a defaultable question, so it is refused rather than
        guessed.
        """
        stage = self.pipeline.stage(stage_key)

        if idempotency_key:
            existing = self.store.find_run_by_idempotency_key(idempotency_key)
            if existing is not None:
                logger.info(
                    "Idempotent replay of %s returns existing run %s",
                    stage_key, existing.run_id,
                )
                return existing

        app_id = await self._resolve_app_id(kb_application, workspace_id)
        workspace = self._resolve_workspace(app_id, workspace_id, force=force)

        if not force:
            require_runnable(self.store, self.pipeline, stage, workspace.id)

        attempt = self.store.next_attempt(self.pipeline.name, stage_key,
                                          workspace_id=workspace.id)
        run = new_run(
            self.pipeline.name, stage_key, app_id,
            attempt=attempt,
            idempotency_key=idempotency_key or make_idempotency_key(
                app_id, self.pipeline.name, stage_key, workspace.id, str(attempt)
            ),
            initiated_by=initiated_by,
            required_runner=stage.required_runner,
        )
        run.workspace_id = workspace.id
        run.tier = workspace.tier.value
        run.epic_id = workspace.epic_id
        run.initiated_by_persona = initiated_by_persona
        run.module_scope = module_scope
        run.gear_id = gear_id or workspace.gear_id
        run.intake_source = workspace.intake_source
        run.business_area = business_area
        run.role = role
        self.store.create_run(run)
        logger.info(
            "Queued %s attempt %d as run %s in %s (initiated_by=%s)",
            stage_key, attempt, run.run_id, workspace.label,
            initiated_by or "unknown",
        )

        if execute:
            self._spawn(run)
        return run

    def _resolve_workspace(self, app_id: str, workspace_id: str | None, *, force: bool = False):
        """Find the workspace this run belongs to, opening the Global one if needed.

        A Mini-tier pipeline refuses to default: there is no sensible "current
        EPIC", and picking one would silently write EPIC 1's workspace while the
        caller believed they were running EPIC 6.
        """
        service = self.workspaces()
        if workspace_id:
            workspace = self.store.get_workspace(workspace_id)
            if workspace is None:
                raise StageBlockedError(f"unknown workspace: {workspace_id}")
            if not workspace.is_open:
                raise StageBlockedError(
                    f"workspace {workspace.id} is {workspace.status.value}; "
                    "reopen it or choose another"
                )
            # Global pipelines may intentionally run per-EPIC stages (feature,
            # user-story, coverage) against Mini Workspaces (FR-P4).  All other
            # tier mismatches are rejected unless the orchestrator bypasses with
            # force=True.
            global_onto_mini = (self.pipeline.tier == "global"
                                 and workspace.tier.value == "mini")
            if workspace.tier.value != self.pipeline.tier and not force and not global_onto_mini:
                raise StageBlockedError(
                    f"workspace {workspace.id} is tier '{workspace.tier.value}' but "
                    f"pipeline '{self.pipeline.name}' is tier '{self.pipeline.tier}'"
                )
            return workspace

        if self.pipeline.tier == "mini":
            open_minis = [
                w for w in self.store.list_workspaces(app_id, tier="mini")
                if w.is_open
            ]
            raise StageBlockedError(
                "this stage runs per EPIC, so `workspace_id` is required. "
                + (f"Open Mini Workspaces: {', '.join(w.id for w in open_minis)}"
                   if open_minis else
                   "No Mini Workspaces exist yet -- approve the Global "
                   "Workspace's EPIC set first (FR-P4).")
            )
        return service.ensure_global(app_id, self.pipeline.name)

    async def _resolve_app_id(
        self, kb_application: str | None, workspace_id: str | None
    ) -> str:
        """Return the canonical project/application id for this run.

        Fast path: workspace_id already carries the id — no KB round-trip needed.
        Slow path: resolve via KB, with a local-project fallback for RED-document
        and greenfield sources that are not in the catalogue.
        """
        if workspace_id:
            ws = self.store.get_workspace(workspace_id)
            if ws is None:
                raise StageBlockedError(f"unknown workspace: {workspace_id}")
            logger.info(
                "workspace_id '%s' provided; using stored kb_application_id "
                "'%s' — KB catalogue not consulted.",
                workspace_id, ws.kb_application_id,
            )
            return ws.kb_application_id

        app_ref = kb_application or self.pipeline.kb_application
        if not app_ref:
            raise StageBlockedError(
                "no KB application specified; set `kb_application` in the pipeline "
                "YAML or pass it on the request"
            )
        try:
            app = await self.kb.resolve_application(app_ref)
            return app.id
        except Exception as exc:  # noqa: BLE001
            return self._resolve_app_id_local(app_ref, exc)

    def _resolve_app_id_local(self, app_ref: str, original_exc: Exception) -> str:
        """Fallback to a locally-onboarded workspace when KB is unavailable."""
        from app.agentic_platform.fe_core.projects.models import local_project_id
        from app.agentic_platform.fe_core.workspaces.models import global_workspace_id

        local_id = local_project_id(app_ref)
        if self.store.get_workspace(global_workspace_id(local_id)) is not None:
            logger.info(
                "KB could not resolve '%s' (%s: %s); "
                "found a locally-onboarded workspace — using local id '%s'. "
                "This is expected for RED-document, greenfield, and git-repo "
                "intake sources.",
                app_ref, type(original_exc).__name__, original_exc, local_id,
            )
            return local_id
        raise StageBlockedError(f"KB application unresolved: {original_exc}") from original_exc

    def workspaces(self):
        from app.agentic_platform.fe_core.workspaces.service import WorkspaceService

        return WorkspaceService(self.store, self.pipeline.name)

    @property
    def worker_mode(self) -> bool:
        """FE_EXECUTION=worker: the API only enqueues; the fe-orchestrator/worker
        service claims QUEUED runs (SKIP LOCKED) and executes them."""
        return getattr(self.settings, "fe_execution", "inprocess") == "worker"

    def _spawn(self, run: StageRun) -> None:
        if self.worker_mode:
            logger.info("Run %s queued for the worker service (FE_EXECUTION=worker)", run.run_id)
            return
        if run.required_runner and run.required_runner != self.settings.fe_runner:
            logger.info(
                "Run %s requires runner=%s; this process has FE_RUNNER=%s — leaving queued "
                "for a compatible worker to claim",
                run.run_id, run.required_runner, self.settings.fe_runner,
            )
            return
        task = asyncio.create_task(self._execute(run.run_id), name=f"stage-{run.run_id}")
        self._tasks[run.run_id] = task
        task.add_done_callback(lambda _t: self._tasks.pop(run.run_id, None))

    async def execute_run(self, run_id: str) -> None:
        """Public entry for the worker service: execute one claimed run to completion."""
        await self._execute(run_id)

    async def _execute(self, run_id: str) -> None:
        run = self.store.get_run(run_id)
        if run is None:
            return
        try:
            executor = await self._executor()
            await executor.execute(run)
        except asyncio.CancelledError:
            run = self.store.get_run(run_id) or run
            try:
                run.transition(StageState.CANCELLED, error="cancelled by request",
                               error_code="cancelled")
            except InvalidStateTransition:
                pass
            self.store.save_run(run)
            raise
        except Exception as exc:  # noqa: BLE001
            run = self.store.get_run(run_id) or run
            if run.state not in (StageState.FAILED, StageState.CANCELLED):
                try:
                    run.transition(StageState.FAILED, error=str(exc),
                                   error_code=type(exc).__name__)
                except InvalidStateTransition:
                    pass
                self.store.save_run(run)
            logger.exception("Run %s failed", run_id)

    async def run_to_completion(self, stage_key: str, **kwargs: Any) -> StageRun:
        """Queue a stage and await it, returning the finished run.

        The HTTP route must return immediately and stream progress, but a CLI
        caller has nothing to stream to, and a script that fires a run and exits
        kills the asyncio task mid-execution. Both need to exist.
        """
        run = await self.create_run(stage_key, execute=False, **kwargs)
        await self._execute(run.run_id)
        return self.store.get_run(run.run_id) or run

    async def _executor(self):
        """Build a StageExecutor with a resolved runner.

        Imported lazily so the API package does not depend on the worker package
        at import time -- they are separate deployables (PRD 5.2).
        """
        from app.agentic_platform.worker.runners.factory import resolve_runner
        from app.agentic_platform.worker.stage_executor import StageExecutor

        runner, detail = await resolve_runner(
            self.settings.fe_runner,
            cli_binary=self.settings.claude_cli_path,
            verify_plugins_strict=self.settings.fe_verify_plugins,
        )
        logger.debug("Runner resolved: %s (%s)", runner.name, detail)
        return StageExecutor(
            runner,
            pipeline=self.pipeline,
            settings=self.settings,
            store=self.store,
            kb=self.kb,
        )

    # -- lifecycle --------------------------------------------------------
    def cancel(self, run_id: str) -> StageRun:
        run = self.store.get_run(run_id)
        if run is None:
            raise ApprovalError(f"unknown run: {run_id}")
        task = self._tasks.get(run_id)
        if task and not task.done():
            task.cancel()
            return run
        if self.worker_mode and run.state is StageState.RUNNING:
            # Another process owns it: flag it; the worker checks between events.
            run.cancel_requested = True
            self.store.save_run(run)
            return run
        run.transition(StageState.CANCELLED, error="cancelled before execution",
                       error_code="cancelled")
        self.store.save_run(run)
        return run

    def recover_orphans(self) -> list[str]:
        """Runs left mid-flight by a restart (FR-015).

        In-process mode: nothing can resume them, so they are marked FAILED with
        a clear reason. Worker mode: QUEUED runs are simply still queued, and a
        RUNNING run whose worker died is re-queued by the worker's own
        `requeue_stale` sweep -- so the API must NOT fail them here.
        """
        recovered: list[str] = []
        if self.worker_mode:
            return recovered
        for run in self.store.list_runs(self.pipeline.name):
            if run.state in (StageState.RUNNING, StageState.QUEUED):
                run.state = StageState.FAILED
                run.error = "interrupted by service restart"
                run.error_code = "restart"
                self.store.save_run(run)
                recovered.append(run.run_id)
        if recovered:
            logger.warning(
                "Marked %d interrupted run(s) as failed after restart: %s",
                len(recovered), ", ".join(recovered),
            )
        return recovered

    # -- approval ---------------------------------------------------------
    def revise_artifact(
        self,
        artifact: SdlcArtifact,
        *,
        content_md: str,
        editor: str | None,
        comment: str | None = None,
    ) -> SdlcArtifact:
        """Save a human-edited copy of ``artifact`` as its next version.

        Only a document still awaiting the reviewer's decision may be revised: the
        artefact's run must be ``WAITING_FOR_APPROVAL`` and the artefact itself a
        ``DRAFT`` / ``IN_REVIEW`` head (an approved or superseded one is immutable —
        use a re-run instead). The new version is written through the ArtifactStore
        and the run is repointed at it, so the subsequent approval carries the edit
        forward and unlocks the next stage. Raises :class:`ApprovalError` (HTTP 409)
        when the state forbids it.
        """
        from app.agentic_platform.fe_core.artifacts.revision import write_markdown_revision

        if not content_md or not content_md.strip():
            raise ApprovalError("the revised document is empty")
        if not artifact.run_id:
            raise ApprovalError(
                f"artifact {artifact.id} has no originating run and cannot be revised")
        run = self.store.get_run(artifact.run_id)
        if run is None:
            raise ApprovalError(f"run {artifact.run_id} for artifact {artifact.id} is missing")
        if run.state is not StageState.WAITING_FOR_APPROVAL:
            raise ApprovalError(
                f"run {run.run_id} is {run.state.value}; only a document awaiting "
                "approval can be edited")
        if artifact.status not in (ArtifactStatus.DRAFT, ArtifactStatus.IN_REVIEW):
            raise ApprovalError(
                f"artifact {artifact.id} is {artifact.status.value}; only a draft under "
                "review can be edited — re-run the stage to produce a fresh one")

        revision = write_markdown_revision(
            store=self.store, settings=self.settings, base=artifact,
            content_md=content_md, editor=editor, comment=comment,
        )
        # Repoint the run so the next approval approves the edited version, not the
        # original the agent produced.
        run.artifact_ids = [revision.id if aid == artifact.id else aid
                            for aid in run.artifact_ids]
        if revision.id not in run.artifact_ids:
            run.artifact_ids.append(revision.id)
        self.store.save_run(run)
        logger.info(
            "Artifact %s revised to %s v%d by %s on run %s",
            artifact.id, revision.id, revision.version, editor or "unknown", run.run_id,
        )
        return revision

    def approve(
        self,
        run_id: str,
        *,
        approver: str,
        decision: str = "approve",
        comment: str | None = None,
        expected_version: int | None = None,
        approver_persona: str | None = None,
    ) -> dict[str, Any]:
        """Record a human decision against every artefact the run produced.

        `approver` is mandatory. `expected_version` implements PRD 9.3: a
        mismatch is a conflict and the reviewer must reload.

        `approver_persona` implements FR-P3. It is optional here so that a
        service-to-service call and the existing tests keep working, but the API
        layer supplies it from the token, and a persona that does not own the
        stage's artefact tier is refused.
        """
        run = self.store.get_run(run_id)
        normalized = decision.strip().lower()
        self._check_approve_preconditions(run_id, run, approver, normalized)
        verdict = (ApprovalDecision.APPROVE if normalized == "approve"
                   else ApprovalDecision.REJECT)

        stage = self.pipeline.stage(run.stage_key)

        # FR-P3: the approving persona must own this stage's artefact tier.
        if approver_persona is not None:
            self._require_persona_authority(stage, approver_persona)

        approvals = self._record_artifact_approvals(
            run, stage, verdict, approver, comment, expected_version, approver_persona)

        run.transition(
            StageState.COMPLETED if verdict is ApprovalDecision.APPROVE
            else StageState.FAILED,
            error=None if verdict is ApprovalDecision.APPROVE else "rejected by reviewer",
            error_code=None if verdict is ApprovalDecision.APPROVE else "rejected",
        )
        self.store.save_run(run)

        logger.info(
            "Stage %s %s by %s (persona %s), %d artefact(s)",
            stage.key, normalized, approver,
            approver_persona or stage.approval_persona, len(approvals),
        )
        # FR-P4: approving the EPIC set opens one Mini Workspace per EPIC.
        opened = []
        if verdict is ApprovalDecision.APPROVE:
            opened = self._fan_out_if_epic_set(run)
            # After FRD approval: ensure the Architecture Workspace exists and is
            # queued to start. Mirrors _fan_out_if_epic_set for the arch tier.
            # In langgraph mode the graph's arch_sync_gate_node also handles this;
            # this hook covers manual-mode and the transition from manual→langgraph.
            self._trigger_architecture_if_frd(run)
            # After every mini-pipeline stage approval: check whether all per-EPIC
            # Mini Workspaces have completed their last stage. If so, open the
            # Epic Assembler Workspace and queue its first stage (epic-assemble).
            self._trigger_assembler_if_all_minis_done(run)
            self._auto_start_next_stage(run)
            self._signal_worker_stop(run)
            # KB sync: fire-and-forget after approving any KB-producing stage.
            self._trigger_kb_sync_after_approval(run)
            # Auto-promote KB to ACTIVE when FE_KB_AUTO_PROMOTE=true and stage has publish_kb.
            self._auto_promote_kb_if_needed(run, stage)

        nxt = self.pipeline.next_after(run.stage_key)
        return {
            "run": run,
            "approvals": approvals,
            "next_stage": nxt.key if nxt else None,
            "workspaces_opened": [
                {"id": w.id, "epic_id": w.epic_id, "epic_title": w.epic_title,
                 "label": w.label}
                for w in opened
            ],
        }

    def _check_approve_preconditions(
        self,
        run_id: str,
        run: StageRun | None,
        approver: str,
        normalized: str,
    ) -> None:
        if not approver or not approver.strip():
            raise ApprovalError(
                "an approver identity is required; anonymous approval is not permitted"
            )
        if run is None:
            raise ApprovalError(f"unknown run: {run_id}")
        if run.state is not StageState.WAITING_FOR_APPROVAL:
            raise ApprovalError(
                f"run {run_id} is {run.state.value}, not waiting_for_approval"
            )
        if normalized not in {"approve", "reject"}:
            raise ApprovalError("decision must be 'approve' or 'reject'")

    def _record_artifact_approvals(
        self,
        run: StageRun,
        stage,
        verdict: ApprovalDecision,
        approver: str,
        comment: str | None,
        expected_version: int | None,
        approver_persona: str | None,
    ) -> list:
        approvals = []
        for artifact_id in run.artifact_ids:
            artifact = self.store.get_artifact(artifact_id)
            if artifact is None:
                continue
            try:
                approvals.append(self.store.record_approval(
                    artifact,
                    decision=verdict,
                    decided_by=approver,
                    role=stage.approval_persona,
                    comment=comment,
                    expected_version=expected_version,
                ))
                # Persona provenance travels with the artefact, not only the
                # approval row, so a reader sees who signed it off without a join.
                artifact.approved_by_persona = (
                    approver_persona or stage.approval_persona)
                self.store.put_artifact(artifact)
            except ConcurrencyError as exc:
                if "superseded" not in str(exc):
                    raise ApprovalError(str(exc)) from exc
                # The artifact was superseded by an orphan run before the gate
                # was reached (LangGraph race). Restore it to IN_REVIEW so
                # record_approval can proceed on the same artifact ID.
                artifact.status = ArtifactStatus.IN_REVIEW
                self.store.put_artifact(artifact)
                logger.warning(
                    "Restored superseded artifact %s v%s to IN_REVIEW "
                    "for approval (stage=%s)", artifact.id, artifact.version, stage.key,
                )
                try:
                    approvals.append(self.store.record_approval(
                        artifact,
                        decision=verdict,
                        decided_by=approver,
                        role=stage.approval_persona,
                        comment=comment,
                        expected_version=None,
                    ))
                    artifact.approved_by_persona = (
                        approver_persona or stage.approval_persona)
                    self.store.put_artifact(artifact)
                except ConcurrencyError as exc2:
                    raise ApprovalError(str(exc2)) from exc2
        return approvals

    def _require_persona_authority(self, stage, approver_persona: str) -> None:
        """FR-P3, enforced against the stage's declared tier."""
        from app.agentic_platform.fe_core.auth.personas import (
            ArtifactTier, may_approve_tier, owners_of, persona_from_name,
        )

        persona = persona_from_name(approver_persona)
        if persona is None:
            raise ApprovalError(f"unknown persona: '{approver_persona}'")

        expected = persona_from_name(stage.approval_persona)
        if expected is not None and persona is not expected:
            raise ApprovalError(
                f"stage '{stage.key}' is approved by '{expected.value}', "
                f"not '{persona.value}'"
            )
        if stage.artifact_tier:
            try:
                tier = ArtifactTier(stage.artifact_tier)
            except ValueError:
                raise ApprovalError(
                    f"stage '{stage.key}' declares unknown artifact_tier "
                    f"'{stage.artifact_tier}'"
                ) from None
            if not may_approve_tier(persona, tier):
                allowed = sorted(p.value for p in owners_of(tier))
                raise ApprovalError(
                    f"persona '{persona.value}' may not approve '{tier.value}' "
                    f"artefacts. Owners: {', '.join(allowed)}"
                )

    def _trigger_architecture_if_frd(self, run: StageRun) -> None:
        """Queue the Architecture Workspace to start when FRD is approved.

        Analogous to _fan_out_if_epic_set: the approval stands even if this
        hook fails. In langgraph mode the graph's arch_sync_gate_node also
        creates and starts the Architecture Workspace; this hook is idempotent
        so the double-call is safe (start() skips already-running workspaces).
        """
        if run.stage_key != "frd":
            return
        ws = self.store.get_workspace(run.workspace_id) if run.workspace_id else None
        if ws is None or ws.tier.value != "global":
            return
        try:
            arch_ws = self.workspaces().ensure_architecture(ws.kb_application_id)
            orch = dict(arch_ws.orchestration or {})
            _active = ("running", "waiting", "handed_off", "fanned_out",
                       "pending_minis", "pending_sync", "pending_architecture", "completed")
            if orch.get("status") not in _active:
                orch["status"] = "requested"
                arch_ws.orchestration = orch
                self.store.put_workspace(arch_ws)
                logger.info(
                    "Architecture Workspace %s queued to start after FRD approval on %s",
                    arch_ws.id, ws.id,
                )
            # Tag the Global workspace so consume_arch_sync_checks() picks it up
            # once Architecture closes and automatically starts the epic-set stage.
            # In langgraph mode arch_sync_gate_node also sets this; the double-write
            # is idempotent. In manual mode this is the only place the flag is set.
            _skip_global = ("fanned_out", "pending_minis", "pending_sync", "completed")
            global_orch = dict(ws.orchestration or {})
            if global_orch.get("status") not in _skip_global:
                global_orch["status"] = "pending_architecture"
                global_orch["architecture_workspace_id"] = arch_ws.id
                ws.orchestration = global_orch
                self.store.put_workspace(ws)
                logger.info(
                    "Global Workspace %s tagged pending_architecture, "
                    "architecture_workspace_id=%s",
                    ws.id, arch_ws.id,
                )
            self._auto_queue_first_arch_stage(arch_ws.id)
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "FRD approved on %s but Architecture Workspace trigger failed: %s. "
                "The approval stands; start the Architecture Workspace manually via "
                "POST /api/v1/workspaces/architecture + /orchestrate",
                run.workspace_id, exc,
            )

    def _auto_queue_first_arch_stage(self, arch_workspace_id: str) -> None:
        """Queue ADR (first arch stage) when the architecture workspace opens."""
        from app.agentic_platform.fe_core.pipeline.registry import get_pipeline  # noqa: PLC0415
        try:
            arch_pl = get_pipeline("uw-cr-architecture")
            first = arch_pl.ordered[0]
            arch_svc = JobService(pipeline=arch_pl, settings=self.settings,
                                  store=self.store, kb=self.kb)
            asyncio.get_running_loop().create_task(
                arch_svc.create_run(first.key, workspace_id=arch_workspace_id),
                name=f"auto-start-{first.key}",
            )
            logger.info("Auto-queuing first architecture stage %s in %s",
                        first.key, arch_workspace_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Auto-queue first architecture stage failed: %s", exc)

    def _trigger_assembler_if_all_minis_done(self, run: StageRun) -> None:
        """Open the Epic Assembler Workspace when every per-EPIC Mini Workspace
        has its last stage approved.

        Fires from approve() after each mini-pipeline approval. Idempotent:
        ensure_assembler() is a no-op if the workspace already exists.
        """
        from app.agentic_platform.fe_core.pipeline.registry import get_pipeline  # noqa: PLC0415

        mini_name = getattr(self.settings, "fe_pipeline_mini", "uw-cr-mini")
        if self.pipeline.name != mini_name:
            return
        ws = self.store.get_workspace(run.workspace_id) if run.workspace_id else None
        if ws is None:
            return
        app_id = ws.kb_application_id
        try:
            mini_pl = get_pipeline(mini_name)
        except Exception:  # noqa: BLE001
            return
        last_seq = max(s.seq for s in mini_pl.stages)
        last_keys = {s.key for s in mini_pl.stages if s.seq == last_seq}
        all_minis = [w for w in self.store.list_workspaces(app_id, tier="mini")
                     if w.pipeline == mini_name]
        if not all_minis:
            return
        for mini_ws in all_minis:
            ws_runs = self.store.list_runs(workspace_id=mini_ws.id)
            for key in last_keys:
                stage_runs = [r for r in ws_runs if r.stage_key == key]
                if not stage_runs:
                    return
                if max(stage_runs, key=lambda r: r.queued_at).state != StageState.COMPLETED:
                    return
        try:
            asm_ws = self.workspaces().ensure_assembler(app_id)
            self._auto_queue_first_assembler_stage(asm_ws.id)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "All mini workspaces done but Epic Assembler trigger failed: %s. "
                "Open the assembler workspace manually.", exc,
            )

    def _auto_queue_first_assembler_stage(self, asm_workspace_id: str) -> None:
        """Queue epic-assemble (first assembler stage) when the workspace opens."""
        from app.agentic_platform.fe_core.pipeline.registry import get_pipeline  # noqa: PLC0415

        assembler_name = getattr(self.settings, "fe_pipeline_assembler", "uw-cr-epic-assembler")
        try:
            asm_pl = get_pipeline(assembler_name)
            first = asm_pl.ordered[0]
            asm_svc = JobService(pipeline=asm_pl, settings=self.settings,
                                 store=self.store, kb=self.kb)
            asyncio.get_running_loop().create_task(
                asm_svc.create_run(first.key, workspace_id=asm_workspace_id),
                name=f"auto-start-{first.key}",
            )
            logger.info("Auto-queuing first assembler stage %s in %s",
                        first.key, asm_workspace_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Auto-queue first assembler stage failed: %s", exc)

    def _auto_start_next_stage(self, run: StageRun) -> None:
        """After approving an architecture stage, auto-queue the next one."""
        if self.pipeline.tier != "architecture":
            return
        # LangGraph orchestrator manages stage advancement via make_run_node.
        # Spawning a second run here would race with the graph-managed run,
        # causing supersede_previous to mark the graph-tracked artifact as
        # SUPERSEDED and making it permanently unapproachable on approval.
        if run.graph_thread_id:
            return
        nxt = self.pipeline.next_after(run.stage_key)
        if nxt is None:
            self._queue_epic_set_after_arch(run)
            return
        try:
            asyncio.get_running_loop().create_task(
                self.create_run(nxt.key, workspace_id=run.workspace_id),
                name=f"auto-start-{nxt.key}",
            )
            logger.info("Auto-queuing %s after approval of %s", nxt.key, run.stage_key)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Auto-start next stage %s failed: %s", nxt.key, exc)

    def _queue_epic_set_after_arch(self, run: StageRun) -> None:
        """Queue epic-set on the Global workspace after the last architecture stage
        is approved (manual / non-LangGraph path).

        The Architecture Workspace is intentionally left open so the architect
        can rerun any stage. epic-set is queued directly rather than via
        consume_arch_sync_checks, which requires the arch workspace to be closed.

        force=False: if the user already manually triggered and approved epic-set,
        the StageBlockedError is caught and logged — no duplicate run is created.
        """
        try:
            arch_ws = self.store.get_workspace(run.workspace_id)
            if arch_ws is None:
                logger.warning("arch workspace %s not found; epic-set not queued", run.workspace_id)
                return
            from app.agentic_platform.fe_core.workspaces.models import global_workspace_id  # noqa: PLC0415
            global_ws_id = global_workspace_id(arch_ws.kb_application_id)
            global_pipeline_name = getattr(self.settings, "fe_pipeline_global", "uw-cr-global")
            global_svc = JobService(
                pipeline=get_pipeline(global_pipeline_name),
                settings=self.settings,
                store=self.store,
                kb=self.kb,
            )
            asyncio.get_running_loop().create_task(
                global_svc.create_run(
                    "epic-set",
                    workspace_id=global_ws_id,
                    initiated_by="arch-gate",
                    force=False,
                ),
                name="auto-start-epic-set",
            )
            logger.info(
                "Auto-queuing epic-set on Global %s after final arch stage '%s' approved",
                global_ws_id, run.stage_key,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Could not auto-queue epic-set after arch completion: %s. "
                "Trigger manually via POST /api/v1/pipeline-runs with stage=epic-set",
                exc,
            )

    def _signal_worker_stop(self, run: StageRun) -> None:
        """Write WORKER_STOP file after SRD approval so the local CLI worker exits."""
        if run.stage_key != "srd" or self.pipeline.tier != "architecture":
            return
        stop_file = self.settings.fe_workspace_root / "WORKER_STOP"
        try:
            stop_file.touch()
            logger.info("SRD approved — stop signal written to %s; CLI worker will exit",
                        stop_file)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not write worker stop signal %s: %s", stop_file, exc)

    def _trigger_kb_sync_after_approval(self, run: StageRun) -> None:
        """Schedule a fire-and-forget KB refresh after a KB-producing stage is approved.

        Uses asyncio.ensure_future so the sync runs in the background without
        blocking the approval response. The inner coroutine logs the outcome
        (ok/fail, delta counts, new KB version) so operators can verify injection
        without querying tables directly. Never raises.
        """
        stage_key = run.stage_key
        if stage_key not in _KB_SYNC_STAGES:
            return

        workspace_id = run.workspace_id

        async def _run_and_log() -> None:
            try:
                refresh = get_kb_refresh_service()
                result = await refresh.run(workspace_id, stage_key=stage_key)
                if result.ok:
                    logger.info(
                        "KB sync OK — stage=%s workspace=%s new_kb_version=%s "
                        "delta=%s — query KB tables WHERE workspace_id='%s' AND stage_key='%s'",
                        stage_key, workspace_id, result.new_kb_version,
                        result.delta_counts, workspace_id, stage_key,
                    )
                else:
                    logger.warning(
                        "KB sync failed — stage=%s workspace=%s reason=%s",
                        stage_key, workspace_id, result.message,
                    )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "KB sync raised unexpectedly — stage=%s workspace=%s error=%s",
                    stage_key, workspace_id, exc,
                )

        try:
            asyncio.ensure_future(_run_and_log())
            logger.info(
                "KB sync scheduled — stage=%s workspace=%s",
                stage_key, workspace_id,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Could not schedule KB sync after approval of stage %s: %s",
                stage_key, exc,
            )

    def _auto_promote_kb_if_needed(self, run: StageRun, stage: Any) -> None:
        """Auto-promote the STAGING KB to ACTIVE when FE_KB_AUTO_PROMOTE=true.

        Only fires when:
        - stage.publish_kb is True (a G1 / kb-extract stage)
        - FE_KB_AUTO_PROMOTE env var is "true" / "1"
        - The platform settings have fe_db_url configured (uses_database())

        Resolves the latest STAGING version for this app from fe_kb_versions and
        calls pg_sink.promote() in a background asyncio task (fire-and-forget).
        """
        import os as _os  # noqa: PLC0415
        auto = _os.environ.get("FE_KB_AUTO_PROMOTE", "").lower() in ("true", "1")
        if not auto:
            return
        if not getattr(stage, "publish_kb", False):
            return
        if not self.settings.uses_database():
            return

        app_id = run.kb_application_id or ""
        if not app_id:
            return

        async def _promote_bg() -> None:
            try:
                from app.agentic_platform.fe_core.kb import pg_sink  # noqa: PLC0415
                import psycopg  # noqa: PLC0415
                from psycopg.rows import dict_row  # noqa: PLC0415

                schema = _os.environ.get("FE_KB_DB_SCHEMA", "form_rationalization_anh")
                with psycopg.connect(self.settings.fe_db_url, row_factory=dict_row) as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            f"SELECT kb_version FROM {schema}.fe_kb_versions "
                            f"WHERE gear_id = %s AND status = 'STAGING' "
                            f"ORDER BY build_date DESC LIMIT 1",
                            (app_id,),
                        )
                        row = cur.fetchone()
                if not row:
                    logger.warning("auto-promote: no STAGING version found for gear_id=%s", app_id)
                    return
                kb_version = row["kb_version"]
                pg_sink.promote(kb_version, app_id)
                logger.info("auto-promote: %s → ACTIVE (gear_id=%s)", kb_version, app_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning("auto-promote: failed for %s: %s", app_id, exc)

        try:
            asyncio.ensure_future(_promote_bg())
        except Exception as exc:  # noqa: BLE001
            logger.warning("auto-promote: could not schedule background task: %s", exc)

    def _fan_out_if_epic_set(self, run: StageRun) -> list:
        """Open the Mini Workspaces when an EPIC set is approved.

        Wrapped so a fan-out failure cannot undo an approval that has already
        been recorded: the approval is the human decision and must stand, while
        fan-out is recoverable by re-approving or by the workspaces endpoint.
        """
        from app.agentic_platform.fe_core.workspaces.service import EPIC_SET_ARTIFACT_TYPES

        opened: list = []
        service = self.workspaces()
        mini_name = getattr(self.settings, "fe_pipeline_mini", "uw-cr-mini")
        for artifact_id in run.artifact_ids:
            artifact = self.store.get_artifact(artifact_id)
            if artifact is None or artifact.artifact_type not in EPIC_SET_ARTIFACT_TYPES:
                continue
            try:
                opened.extend(service.fan_out(artifact, pipeline=mini_name))
            except Exception as exc:  # noqa: BLE001
                logger.exception(
                    "EPIC-set %s approved but fan-out failed: %s. The approval "
                    "stands; retry via POST /api/v1/workspaces/fan-out",
                    artifact.id, exc,
                )
        return opened

    # -- read -------------------------------------------------------------
    def artifact_states(self) -> dict[str, str]:
        return {
            a.id: a.status.value
            for a in self.store.list_artifacts(self.pipeline.name)
            if a.status is not ArtifactStatus.SUPERSEDED
        }
