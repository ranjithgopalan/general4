"""Build the StateGraph for a pipeline.

Node = stage (intake → run → outtake → gate), reusing:
  JobService.create_run(execute=False)  -> queued StageRun
  JobService.execute_run(run_id)        -> StageExecutor: prompt, runner, verify, store
  interrupt({...})                      -> the human gate (Command(resume=decision))
  JobService.approve(...)               -> approval, tier D->C, fan-out on EPIC set

Edges: START -> first server-side stage -> ... ; after the last Global stage a
`fan_out` node records the Mini workspaces the approval opened; in a Mini
pipeline the first laptop stage is replaced by one `hand_off` gate that waits
until the laptop artefacts for those stages are approved, then continues with
the remaining server-side stages (e.g. security-opa, docs-uat-deploy).
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Callable

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, Send, interrupt

from app.agentic_platform.fe_core.kb.models import ArtifactStatus
from app.agentic_platform.fe_core.pipeline.models import Pipeline, Stage, StageState
from app.agentic_platform.worker.orchestrator.state import LAPTOP_TARGETS, MAX_REJECT_RETRIES, GraphState
from app.utils.request_context import set_workflow_run_id

logger = logging.getLogger(__name__)

HAND_OFF = "hand_off"
FAN_OUT = "fan_out"
MERGE = "merge"
ARCH_SYNC = "arch_sync_gate"
OPEN_MINIS = "open_minis"
LOOP_HEAD = "loop_head"

#: Stage keys promoted from the Mini thread to the Global per-EPIC loop so they
#: run via Bedrock / LangGraph rather than the Claude Code CLI. The global graph
#: drives: epic-set → open_minis → loop_head → feature → user-story → coverage
#: → loop_head (next EPIC) → fan_out → merge.
PER_EPIC_KEYS: frozenset[str] = frozenset({"feature", "user-story", "coverage"})


def split_stages(pipeline: Pipeline) -> tuple[list[Stage], list[Stage], list[Stage]]:
    """(server stages before hand-off, laptop stages, server stages after hand-off).

    Stages that declare ``parallel_group`` are always server-side: the orchestrator
    graph drives them with a fork/join (``Send`` fan-out + barrier node), regardless
    of their ``target`` value.  Without this override, ``target: ui/api`` would route
    them to the laptop bucket and the parallel LangGraph nodes would never be wired.
    """
    ordered = pipeline.ordered
    parallel_keys = {s.key for s in ordered if s.parallel_group}
    laptop = [s for s in ordered if s.target in LAPTOP_TARGETS and s.key not in parallel_keys]
    if not laptop:
        return ordered, [], []
    first = min(ordered.index(s) for s in laptop)
    last = max(ordered.index(s) for s in laptop)
    before = [s for s in ordered[:first]]
    after = [s for s in ordered[last + 1:] if s.target not in LAPTOP_TARGETS or s.key in parallel_keys]
    # server-side stages interleaved between laptop stages (rare) run after hand-off
    middle_server = [s for s in ordered[first:last + 1]
                     if s.target not in LAPTOP_TARGETS or s.key in parallel_keys]
    return before, laptop, middle_server + after


def _parallel_groups(stages: list[Stage]) -> dict[str, list[Stage]]:
    """Return ``{group_name: [Stage, ...]}`` for groups with two or more members.

    Groups with only one member (a ``parallel_group`` value that no other stage
    shares) are excluded — they would produce a barrier that never receives a
    second arrival, which would deadlock the graph.
    """
    result: dict[str, list[Stage]] = {}
    for s in stages:
        if s.parallel_group:
            result.setdefault(s.parallel_group, []).append(s)
    return {k: v for k, v in result.items() if len(v) > 1}


def _build_server_keys(
    stages: list[Stage],
    groups: dict[str, list[Stage]],
) -> list[str]:
    """Build the ordered node-key list used by ``_next_of``.

    Parallel groups are collapsed to a single ``barrier__{group_name}`` slot so
    ``_next_of`` can produce a linear routing table.  Individual parallel stage
    keys (e.g. ``"ui-code"``, ``"api-code"``) do NOT appear in this list; the
    gate nodes for those stages use ``_next_of`` which special-cases them to
    return the barrier key directly.
    """
    seen_seqs: set[int] = set()
    keys: list[str] = []
    for s in stages:
        if s.seq in seen_seqs:
            continue
        seen_seqs.add(s.seq)
        if s.parallel_group and s.parallel_group in groups:
            keys.append(f"barrier__{s.parallel_group}")
        else:
            keys.append(s.key)
    return keys


def make_barrier_node(group_name: str, group_stages: list[Stage], next_node: str):
    """Return an async node that joins all branches of a parallel group.

    The node is called once per arriving branch.  It reads the accumulated
    ``parallel_sync_counts[group_name]`` from state (merged by the additive
    ``_merge_int_counters`` reducer in state.py) and increments it by 1 for
    this invocation.  Only when the total reaches ``len(group_stages)`` does it
    issue ``goto=next_node``; earlier arrivals update the counter and return
    without a ``goto``, parking the branch until the last one fires.

    The counter is reset by writing a negative offset so subsequent reruns of the
    same pipeline start from zero.
    """
    expected = len(group_stages)

    async def node(state: GraphState) -> Command:
        counts = state.get("parallel_sync_counts") or {}
        # +1 counts this current invocation; the reducer has already applied
        # all prior branches' increments into the state we receive.
        arrived = counts.get(group_name, 0) + 1
        if arrived < expected:
            return Command(update={"parallel_sync_counts": {group_name: 1}})
        # All branches arrived: reset counter and route forward.
        return Command(
            update={"parallel_sync_counts": {group_name: -(arrived - 1)}},
            goto=next_node,
        )

    node.__name__ = f"barrier__{group_name}"
    return node


def build_graph(pipeline: Pipeline, *, service_factory: Callable[[], Any], store,
                checkpointer=None, mini_pipeline_name: str | None = None,
                architecture_pipeline_name: str | None = None,
                pipelines: dict[str, Pipeline] | None = None):
    """Compile the graph for `pipeline`. `service_factory()` returns a JobService.

    When a Global pipeline is paired with an Architecture pipeline (via
    `architecture_pipeline_name` + `pipelines`), an `arch_sync_gate` node is
    inserted after the architecture pipeline's `arch_after` stage: the Global
    thread parks there until the Architecture Workspace closes (SRD approved).
    """

    before, laptop, after = split_stages(pipeline)

    # Global pipelines: separate per-EPIC stages from normal server-side stages.
    # Per-EPIC stages (feature/user-story/coverage) run inside the global thread
    # scoped to each Mini Workspace ID via a loop_head conditional node.
    if pipeline.tier == "global":
        per_epic = [s for s in before if s.key in PER_EPIC_KEYS]
        before = [s for s in before if s.key not in PER_EPIC_KEYS]
    else:
        per_epic = []

    # Parallel groups in the server-side stages (after per-EPIC filtering).
    # For the Mini pipeline this yields {"code-gen": [ui-code, api-code]}.
    # For Global / Architecture pipelines this is empty (no parallel_group stages).
    groups = _parallel_groups(before + after)

    def _svc():
        return service_factory()

    def gate_key(stage_key: str) -> str:
        return f"gate__{stage_key}"

    def make_run_node(stage: Stage):
        """intake + run + outtake. Persists the run id in graph state BEFORE the
        gate node interrupts, so a resume never re-creates the run."""
        async def node(state: GraphState) -> Command:
            svc = _svc()
            run_ids = dict(state.get("run_ids") or {})
            ws = state["workspace_id"]
            # workflow_run_id is stable for the lifetime of this BRD→delivery run.
            # Generate once on the first node execution and carry it through state.
            wf_run_id = state.get("workflow_run_id") or str(uuid.uuid4())
            set_workflow_run_id(wf_run_id)
            run = store.get_run(run_ids[stage.key]) if run_ids.get(stage.key) else None
            if run is not None and run.state in (StageState.WAITING_FOR_APPROVAL, StageState.RUNNING,
                                                 StageState.QUEUED, StageState.COMPLETED):
                return Command(update={"current_stage": stage.key, "workflow_run_id": wf_run_id},
                               goto=gate_key(stage.key))
            run = await svc.create_run(stage.key, kb_application=state["project_id"], workspace_id=ws,
                                       initiated_by=state.get("initiated_by") or "orchestrator",
                                       force=True, execute=False)
            run.graph_thread_id = ws
            run.workflow_run_id = wf_run_id
            store.save_run(run)
            run_ids[stage.key] = run.run_id
            _set_ws_status(store, ws, status="running", current_stage=stage.key, run_id=run.run_id)
            await svc.execute_run(run.run_id)
            run = store.get_run(run.run_id)
            if run.state is StageState.FAILED:
                _set_ws_status(store, ws, status="failed", current_stage=stage.key, run_id=run.run_id,
                               last_error=run.error)
                return Command(update={"run_ids": run_ids, "current_stage": stage.key,
                                       "workflow_run_id": wf_run_id,
                                       "error": f"{stage.key}: {run.error}", "finished": True}, goto=END)
            return Command(update={"run_ids": run_ids, "current_stage": stage.key,
                                   "workflow_run_id": wf_run_id}, goto=gate_key(stage.key))
        node.__name__ = f"run_{stage.key}"
        return node

    def make_gate_node(stage: Stage):
        """The human gate: interrupt until the named persona decides; approve
        through JobService (tier D->C, fan-out); reject -> re-run (v+1)."""
        async def node(state: GraphState) -> Command:
            svc = _svc()
            run_ids = dict(state.get("run_ids") or {})
            approved = dict(state.get("approved") or {})
            retries = dict(state.get("retries") or {})
            ws = state["workspace_id"]
            run = store.get_run(run_ids.get(stage.key, ""))
            if run is None:
                return Command(update={"error": f"{stage.key}: run missing", "finished": True}, goto=END)
            if run.state is StageState.WAITING_FOR_APPROVAL:
                _set_ws_status(store, ws, status="waiting", current_stage=stage.key, run_id=run.run_id,
                               waiting_on_persona=stage.approval_persona)
                decision = interrupt({"kind": "approval", "stage": stage.key, "run_id": run.run_id,
                                      "persona": stage.approval_persona, "artifact_ids": list(run.artifact_ids)})
                verdict = (decision or {}).get("decision", "approve")
                svc.approve(run.run_id, approver=(decision or {}).get("approver") or "orchestrator",
                            decision=verdict, comment=(decision or {}).get("comment"),
                            approver_persona=(decision or {}).get("persona"))
                run = store.get_run(run.run_id)
            if run.state is StageState.COMPLETED:
                approved[stage.key] = True
                nxt = _next_of(stage.key)
                if nxt == END:                       # last server-side stage of a Mini thread
                    _set_ws_status(store, ws, status="completed", current_stage=stage.key,
                                   run_id=run.run_id, waiting_on_persona=None)
                    return Command(update={"approved": approved, "current_stage": stage.key,
                                           "finished": True}, goto=END)
                goto_target = _build_goto(stage.key, state)
                return Command(update={"approved": approved, "current_stage": stage.key},
                               goto=goto_target)
            # rejected (FAILED) -> re-run the stage, bounded
            retries[stage.key] = retries.get(stage.key, 0) + 1
            if retries[stage.key] > MAX_REJECT_RETRIES:
                _set_ws_status(store, ws, status="failed", current_stage=stage.key, run_id=run.run_id,
                               last_error=f"rejected {retries[stage.key]} times")
                return Command(update={"retries": retries, "current_stage": stage.key,
                                       "error": f"{stage.key}: rejected {retries[stage.key]} times",
                                       "finished": True}, goto=END)
            logger.info("stage %s rejected (%d); re-running", stage.key, retries[stage.key])
            run_ids.pop(stage.key, None)
            return Command(update={"run_ids": run_ids, "retries": retries, "current_stage": stage.key},
                           goto=stage.key)
        node.__name__ = f"gate_{stage.key}"
        return node

    # A Global graph gains an arch_sync_gate when an architecture pipeline is
    # registered and declares `arch_after` pointing at one of this pipeline's stages.
    _arch_pipeline = (pipelines or {}).get(architecture_pipeline_name or "")
    _arch_after_key: str | None = None
    if pipeline.tier == "global" and _arch_pipeline is not None:
        _arch_after_key = getattr(_arch_pipeline, "arch_after", None)
        if _arch_after_key and _arch_after_key not in {s.key for s in before + after}:
            logger.warning(
                "architecture pipeline arch_after='%s' not found in global stages; "
                "arch_sync_gate will not be inserted", _arch_after_key)
            _arch_after_key = None

    # Build the flat node-key list, collapsing parallel groups to barrier__ slots.
    # Individual parallel stage keys (ui-code, api-code) are absent; _next_of
    # handles them specially below.
    # Preserve the original before / HAND_OFF / after ordering so that server-side
    # stages in `after` (e.g. security-opa) still run AFTER the laptop hand-off.
    stage_keys_before = _build_server_keys(before, groups)
    stage_keys_after = _build_server_keys(after, groups)
    raw_server_keys = stage_keys_before + ([HAND_OFF] if laptop else []) + stage_keys_after
    # When a per-EPIC loop is present, append OPEN_MINIS so that _next_of for
    # the last normal stage (epic-set) routes to open_minis instead of fan_out.
    if per_epic:
        raw_server_keys = raw_server_keys + [OPEN_MINIS]
    if _arch_after_key and _arch_after_key in raw_server_keys:
        split_idx = raw_server_keys.index(_arch_after_key)
        server_keys = (raw_server_keys[:split_idx + 1] + [ARCH_SYNC]
                       + raw_server_keys[split_idx + 1:])
    else:
        server_keys = raw_server_keys

    # Architecture tier ends at END (no fan_out / merge for this tier).
    tail = FAN_OUT if pipeline.tier == "global" else END

    def _next_of(key: str) -> str:
        """Return the next node key after ``key`` in the linear server_keys list.

        For a parallel stage (``parallel_group`` set), the next node is always
        the group's barrier, regardless of where the stage appears in the list —
        parallel stage keys are not present in ``server_keys`` at all.
        """
        stage_obj = next((s for s in before + after if s.key == key), None)
        if stage_obj and stage_obj.parallel_group and stage_obj.parallel_group in groups:
            return f"barrier__{stage_obj.parallel_group}"
        try:
            i = server_keys.index(key)
            return server_keys[i + 1] if i + 1 < len(server_keys) else tail
        except ValueError:
            return tail

    def _is_fanout_point(key: str) -> str | None:
        """Return the parallel group name when ``key``'s approval fans out to multiple stages.

        Returns ``None`` for all ordinary sequential stages.
        """
        stage_obj = next((s for s in before + after if s.key == key), None)
        if not stage_obj:
            return None
        successor_seq = stage_obj.seq + 1
        successors = [s for s in before + after if s.seq == successor_seq]
        if len(successors) > 1 and successors[0].parallel_group:
            return successors[0].parallel_group
        return None

    def _build_goto(key: str, state: GraphState) -> str | list:
        """Compute the ``goto`` target for the gate node of ``key``.

        When the next seq group is a parallel group, returns a list of ``Send``
        objects so LangGraph fans out to all members concurrently.  Otherwise
        returns the next node key string (ordinary sequential routing).
        """
        fanout_group = _is_fanout_point(key)
        if fanout_group:
            return [Send(ps.key, state) for ps in groups[fanout_group]]
        return _next_of(key)

    async def hand_off_node(state: GraphState) -> Command:
        """Mini tier: laptop stages (Claude Code CLI). Wait until each has an
        APPROVED artefact in this workspace, then continue server-side."""
        ws = state["workspace_id"]
        needed = {s.key: s.produces for s in laptop}
        while True:
            missing = [k for k, types in needed.items()
                       if not store.approved_artifacts(pipeline.name, types, workspace_ids=[ws])]
            if not missing:
                break
            _set_ws_status(store, ws, status="handed_off", current_stage=HAND_OFF,
                           waiting_on_persona="developer/tester", last_error=None,
                           laptop_stages_pending=missing)
            interrupt({"kind": "hand_off", "workspace_id": ws, "laptop_stages_pending": missing,
                       "how": "run /fe-develop and /fe-test on a laptop; approve the git-ref artefacts; "
                              "the console then resumes this thread"})
        return Command(update={"handed_off": True, "current_stage": HAND_OFF}, goto=_next_of(HAND_OFF))

    async def arch_sync_gate_node(state: GraphState) -> Command:
        """Global tier: create the Architecture Workspace (singleton) if it does
        not yet exist, then park until it closes (SRD approved). Mirrors merge_node:
        interrupt in a loop until the condition is met.

        Setting status 'pending_architecture' lets OrchestratorService._after_step
        start the arch thread and consume_arch_sync_checks() resume this one once
        the arch workspace closes.
        """
        from app.agentic_platform.fe_core.workspaces.service import WorkspaceService  # noqa: PLC0415
        ws = state["workspace_id"]
        project = state["project_id"]
        arch_ws = WorkspaceService(store).ensure_architecture(project)
        while arch_ws.is_open:
            _set_ws_status(store, ws, status="pending_architecture", current_stage=ARCH_SYNC,
                           architecture_workspace_id=arch_ws.id, waiting_on_persona="architect")
            interrupt({"kind": "arch_sync_wait", "workspace_id": ws,
                       "architecture_workspace_id": arch_ws.id,
                       "how": ("the Architecture Workspace is running ADR->NFR->SDD->SRD; "
                               "approve SRD in the Architecture Workspace to resume")})
            arch_ws = store.get_workspace(arch_ws.id) or arch_ws
        _set_ws_status(store, ws, status="running", current_stage=ARCH_SYNC,
                       waiting_on_persona=None, architecture_workspace_id=arch_ws.id)

        # Record cross-workspace DERIVES_FROM: the approved SRD from the Architecture
        # Workspace is the source for everything the Global thread produces next.
        # This edge makes the Architecture→Global handoff queryable as a lineage graph.
        try:
            from app.lifecycle.traceability.relationships_dao import ArtifactRelationshipDAO  # noqa: PLC0415
            from app.agentic_platform.fe_core.kb.models import ArtifactStatus  # noqa: PLC0415 (already imported at top)

            _srd_artifacts = store.list_artifacts_by_stage(arch_ws.id, stage_key="srd") if hasattr(store, "list_artifacts_by_stage") else []
            _approved_srd = [a for a in _srd_artifacts if getattr(a, "status", None) == ArtifactStatus.APPROVED]
            if _approved_srd:
                _rel_dao = ArtifactRelationshipDAO()
                for _srd in _approved_srd:
                    import asyncio as _asyncio  # noqa: PLC0415
                    _asyncio.create_task(
                        _rel_dao.create(
                            workspace_id=ws,
                            source_artifact_id=ws,
                            target_artifact_id=_srd.id,
                            relationship_type="derives_from",
                            description=(f"Global workspace '{ws}' continues from SRD "
                                         f"'{_srd.id}' approved in Architecture Workspace '{arch_ws.id}'"),
                        )
                    )
        except Exception as _exc:
            logger.debug("[arch_sync_gate] lineage record skipped (non-fatal): %s", _exc)

        return Command(update={"current_stage": ARCH_SYNC, "architecture_workspace_id": arch_ws.id},
                       goto=_next_of(ARCH_SYNC))

    # ------------------------------------------------------------------ per-EPIC loop
    # The three nodes below (open_minis, loop_head, make_mini_*) are only
    # wired into the graph when per_epic is non-empty (global tier only).

    async def open_minis_node(state: GraphState) -> Command:
        """Global tier: collect Mini workspace IDs opened by epic-set approval
        and initialise the per-EPIC loop counter. Does NOT start Mini threads;
        that happens in _after_step once fan_out sets status='fanned_out'."""
        ws = state["workspace_id"]
        project = state["project_id"]
        minis = [w.id for w in store.list_workspaces(project, tier="mini") if w.is_open]
        _set_ws_status(store, ws, status="running", current_stage=OPEN_MINIS)
        return Command(update={"epic_workspace_ids": minis, "current_epic_idx": 0,
                               "current_stage": OPEN_MINIS}, goto=LOOP_HEAD)

    async def loop_head_node(state: GraphState) -> Command:
        """Conditional: route to the first per-EPIC stage if EPICs remain, else
        proceed to fan_out so Mini LangGraph threads can start from LLD."""
        epic_ids = state.get("epic_workspace_ids") or []
        idx = state.get("current_epic_idx") or 0
        ws = state["workspace_id"]
        _set_ws_status(store, ws, status="running", current_stage=LOOP_HEAD)
        if idx < len(epic_ids) and per_epic:
            return Command(update={"current_stage": LOOP_HEAD}, goto=per_epic[0].key)
        return Command(update={"current_stage": LOOP_HEAD}, goto=FAN_OUT)

    def make_mini_run_node(stage: Stage):
        """Like make_run_node but executes against the current EPIC's Mini workspace."""
        async def node(state: GraphState) -> Command:
            svc = _svc()
            run_ids = dict(state.get("run_ids") or {})
            epic_ids = state.get("epic_workspace_ids") or []
            idx = state.get("current_epic_idx") or 0
            if idx >= len(epic_ids):
                # Falling back to the Global workspace here would register the run
                # under the wrong workspace and produce the "registered under a
                # different workspace" mismatch.  Fail loudly instead so the root
                # cause (open_minis_node found no Mini workspaces, or state was not
                # restored from checkpoint) is surfaced immediately.
                raise IndexError(
                    f"per-EPIC stage '{stage.key}': EPIC index {idx} is out of range "
                    f"(epic_workspace_ids has {len(epic_ids)} entries). "
                    "Check that the epic-set approval fan-out created Mini workspaces "
                    "before open_minis_node ran, and that the checkpoint restored "
                    "epic_workspace_ids correctly."
                )
            mini_ws = epic_ids[idx]
            # Namespace run_id keys by EPIC index to survive multi-iteration loops.
            run_key = f"{stage.key}__{idx}"
            run = store.get_run(run_ids[run_key]) if run_ids.get(run_key) else None
            if run is not None and run.state in (StageState.WAITING_FOR_APPROVAL, StageState.RUNNING,
                                                 StageState.QUEUED, StageState.COMPLETED):
                return Command(update={"current_stage": stage.key}, goto=gate_key(stage.key))
            run = await svc.create_run(stage.key, kb_application=state["project_id"],
                                       workspace_id=mini_ws,
                                       initiated_by=state.get("initiated_by") or "orchestrator",
                                       force=True, execute=False)
            # Anchor the run to the GLOBAL thread so consume_resume_intents can resume it.
            run.graph_thread_id = state["workspace_id"]
            store.save_run(run)
            run_ids[run_key] = run.run_id
            _set_ws_status(store, mini_ws, status="running", current_stage=stage.key,
                           run_id=run.run_id)
            await svc.execute_run(run.run_id)
            run = store.get_run(run.run_id)
            if run.state is StageState.FAILED:
                _set_ws_status(store, mini_ws, status="failed", current_stage=stage.key,
                               run_id=run.run_id, last_error=run.error)
                return Command(update={"run_ids": run_ids, "current_stage": stage.key,
                                       "error": f"{stage.key}[{idx}]: {run.error}",
                                       "finished": True}, goto=END)
            return Command(update={"run_ids": run_ids, "current_stage": stage.key},
                           goto=gate_key(stage.key))
        node.__name__ = f"run_{stage.key}"
        return node

    def make_mini_gate_node(stage: Stage, *, nxt: str):
        """Human gate for a per-EPIC stage. `nxt` is the next node after approval
        (the following per-EPIC stage, or LOOP_HEAD for the last one)."""
        async def node(state: GraphState) -> Command:
            svc = _svc()
            run_ids = dict(state.get("run_ids") or {})
            approved = dict(state.get("approved") or {})
            retries = dict(state.get("retries") or {})
            epic_ids = state.get("epic_workspace_ids") or []
            idx = state.get("current_epic_idx") or 0
            if idx >= len(epic_ids):
                raise IndexError(
                    f"per-EPIC gate '{stage.key}': EPIC index {idx} is out of range "
                    f"(epic_workspace_ids has {len(epic_ids)} entries). "
                    "State was not restored correctly from checkpoint."
                )
            mini_ws = epic_ids[idx]
            run_key = f"{stage.key}__{idx}"
            run = store.get_run(run_ids.get(run_key, ""))
            if run is None:
                return Command(update={"error": f"{stage.key}[{idx}]: run missing",
                                       "finished": True}, goto=END)
            if run.state is StageState.WAITING_FOR_APPROVAL:
                _set_ws_status(store, mini_ws, status="waiting", current_stage=stage.key,
                               run_id=run.run_id, waiting_on_persona=stage.approval_persona)
                decision = interrupt({"kind": "approval", "stage": stage.key,
                                      "run_id": run.run_id, "persona": stage.approval_persona,
                                      "artifact_ids": list(run.artifact_ids)})
                verdict = (decision or {}).get("decision", "approve")
                svc.approve(run.run_id,
                            approver=(decision or {}).get("approver") or "orchestrator",
                            decision=verdict, comment=(decision or {}).get("comment"),
                            approver_persona=(decision or {}).get("persona"))
                run = store.get_run(run.run_id)
            approved_key = f"{stage.key}__{idx}"
            if run.state is StageState.COMPLETED:
                approved[approved_key] = True
                if nxt == LOOP_HEAD:
                    # Last per-EPIC stage: mark mini workspace ready for its thread,
                    # then advance the EPIC index before returning to loop_head.
                    _set_ws_status(store, mini_ws, status="pending_thread",
                                   current_stage=stage.key, waiting_on_persona=None)
                    new_idx = idx + 1
                    return Command(update={"approved": approved, "current_stage": stage.key,
                                           "current_epic_idx": new_idx}, goto=LOOP_HEAD)
                _set_ws_status(store, mini_ws, status="running", current_stage=stage.key,
                               waiting_on_persona=None)
                return Command(update={"approved": approved, "current_stage": stage.key},
                               goto=nxt)
            # Rejected (FAILED) -> re-run the stage, bounded.
            retries[run_key] = retries.get(run_key, 0) + 1
            if retries[run_key] > MAX_REJECT_RETRIES:
                _set_ws_status(store, mini_ws, status="failed", current_stage=stage.key,
                               run_id=run.run_id,
                               last_error=f"rejected {retries[run_key]} times")
                return Command(update={"retries": retries, "current_stage": stage.key,
                                       "error": f"{stage.key}[{idx}]: rejected {retries[run_key]} times",
                                       "finished": True}, goto=END)
            logger.info("per-EPIC stage %s[%d] rejected (%d); re-running",
                        stage.key, idx, retries[run_key])
            run_ids.pop(run_key, None)
            return Command(update={"run_ids": run_ids, "retries": retries,
                                   "current_stage": stage.key}, goto=stage.key)
        node.__name__ = f"gate_{stage.key}"
        return node

    async def fan_out_node(state: GraphState) -> Command:
        """Global tier: JobService.approve already opened the Mini workspaces on
        EPIC-set approval; record them so the service can start their threads,
        then park in `merge` until every Mini is closed."""
        ws = state["workspace_id"]
        minis = [w.id for w in store.list_workspaces(state["project_id"], tier="mini") if w.is_open]
        _set_ws_status(store, ws, status="fanned_out", current_stage=FAN_OUT, epic_workspace_ids=minis)
        return Command(update={"epic_workspace_ids": minis, "current_stage": FAN_OUT}, goto=MERGE)

    async def merge_node(state: GraphState) -> Command:
        """MERGE -> PENDING_SYNC -> CLOSED (Plan v3 C). Wait for all Minis to close,
        build + queue the kb-refresh (STAGING), wait for the human promote decision,
        then close the Global workspace."""
        from app.agentic_platform.fe_core.config import get_settings  # noqa: PLC0415
        from app.agentic_platform.fe_core.kb.merge import build_kb_refresh, kb_refresh_version  # noqa: PLC0415
        ws = state["workspace_id"]
        project = state["project_id"]
        while True:
            open_minis = [w.id for w in store.list_workspaces(project, tier="mini") if w.is_open]
            if not open_minis:
                break
            _set_ws_status(store, ws, status="pending_minis", current_stage=MERGE,
                           waiting_on_persona=None, minis_open=open_minis)
            interrupt({"kind": "merge_wait", "workspace_id": ws, "minis_open": open_minis})
        settings = get_settings()
        wsrow = store.get_workspace(ws)
        base = (wsrow.orchestration or {}).get("pinned_kb_version") if wsrow else None
        art = build_kb_refresh(store, settings, project_id=project, workspace_id=ws, pipeline=pipeline.name,
                               mini_pipeline=mini_pipeline_name, base_kb_version=base,
                               created_by=state.get("initiated_by") or "orchestrator")
        version = kb_refresh_version(base, ws)
        while True:
            _set_ws_status(store, ws, status="pending_sync", current_stage=MERGE,
                           waiting_on_persona="architect", kb_refresh_artifact_id=art.id, kb_version=version,
                           promote={"mode": "manual", "state": "requested"})
            decision = interrupt({"kind": "promote", "workspace_id": ws, "kb_version": version,
                                  "artifact_id": art.id, "how": f"POST /api/v1/workspaces/{ws}/promote"})
            if (decision or {}).get("kind") == "promote":
                break
        by = (decision or {}).get("decided_by") or "unknown"
        wsrow = store.get_workspace(ws)
        cur = dict(wsrow.orchestration or {}) if wsrow else {}
        promo = dict(cur.get("promote") or {})
        promo.update({"state": "promoted", "decided_by": by, "decided_at": (decision or {}).get("at"),
                      "result": (decision or {}).get("result")})
        _set_ws_status(store, ws, status="completed", current_stage=MERGE, waiting_on_persona=None, promote=promo)
        wsrow = store.get_workspace(ws)
        if wsrow is not None and wsrow.is_open:
            store.put_workspace(wsrow.close())
        return Command(update={"finished": True, "current_stage": MERGE}, goto=END)

    g = StateGraph(GraphState)
    for s in before + after:
        g.add_node(s.key, make_run_node(s))
        g.add_node(gate_key(s.key), make_gate_node(s))
    # Register a barrier (join) node for each parallel group.  The barrier waits
    # until all branches have arrived before routing to the next sequential stage.
    for group_name, group_stages in groups.items():
        barrier_key = f"barrier__{group_name}"
        try:
            barrier_nxt = server_keys[server_keys.index(barrier_key) + 1]
        except (ValueError, IndexError):
            barrier_nxt = tail
        g.add_node(barrier_key, make_barrier_node(group_name, group_stages, barrier_nxt))
    if laptop:
        g.add_node(HAND_OFF, hand_off_node)
    if pipeline.tier == "global":
        g.add_node(FAN_OUT, fan_out_node)
        g.add_node(MERGE, merge_node)
        if _arch_after_key:
            g.add_node(ARCH_SYNC, arch_sync_gate_node)
        if per_epic:
            g.add_node(OPEN_MINIS, open_minis_node)
            g.add_node(LOOP_HEAD, loop_head_node)
            for i, s in enumerate(per_epic):
                nxt_stage = per_epic[i + 1].key if i + 1 < len(per_epic) else LOOP_HEAD
                g.add_node(s.key, make_mini_run_node(s))
                g.add_node(gate_key(s.key), make_mini_gate_node(s, nxt=nxt_stage))
    if server_keys:
        g.add_edge(START, server_keys[0])
    else:
        g.add_edge(START, tail if tail != END else END)
    return g.compile(checkpointer=checkpointer)


def _set_ws_status(store, workspace_id: str, **fields) -> None:
    from datetime import datetime, timezone  # noqa: PLC0415
    ws = store.get_workspace(workspace_id)
    if ws is None:
        return
    prev = dict(ws.orchestration or {})
    cur = dict(prev)
    cur.update({k: v for k, v in fields.items()})
    cur["updated_at"] = datetime.now(timezone.utc).isoformat()
    ws.orchestration = cur
    if fields.get("status") == "completed" and ws.tier.value in ("mini", "architecture"):
        ws = ws.close()                      # Mini/Architecture thread finished -> workspace closed
    store.put_workspace(ws)
    if (prev.get("status"), prev.get("current_stage")) != (cur.get("status"), cur.get("current_stage")):
        audit = getattr(store, "audit", None)
        if audit is not None:
            try:
                audit(action="orchestration.transition", actor="orchestrator", workspace_id=workspace_id,
                      run_id=cur.get("run_id"), persona=cur.get("waiting_on_persona"),
                      before={"status": prev.get("status"), "stage": prev.get("current_stage")},
                      after={"status": cur.get("status"), "stage": cur.get("current_stage")},
                      detail={"last_error": cur.get("last_error")} if cur.get("last_error") else None)
            except Exception:  # noqa: BLE001 - audit must never break the graph
                pass
