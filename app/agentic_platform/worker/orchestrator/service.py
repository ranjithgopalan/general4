"""OrchestratorService — start / resume / status for workspace threads.

Runs inside the worker process (never the API): the graph and the stage
execution must live together so an `interrupt()` and its `Command(resume=…)`
share one checkpointer.

Checkpointer: `langgraph-checkpoint-postgres` on FE_DB_URL (schema-less tables
`checkpoints*` in the DB's default schema; `setup()` is idempotent) when
FE_STORE=postgres; MemorySaver otherwise (single process, tests).

The API never calls this directly. It records a human decision on the run
(`StageRun.resume_intent`); the worker loop finds runs with an intent whose
`graph_thread_id` is set and calls `resume()`. Uploads queue an `intake_ready`
outbox event; the worker loop calls `consume_intake_events()` which starts the
Global thread for that project.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from langgraph.types import Command

from app.agentic_platform.fe_core.kb.library import library_workspace_id as _library_workspace_id
from app.agentic_platform.fe_core.kb.models import OutboxStatus
from app.agentic_platform.fe_core.pipeline.models import StageState
from app.agentic_platform.worker.orchestrator.graph import build_graph

logger = logging.getLogger(__name__)


class OrchestratorService:
    def __init__(self, *, store, settings, pipelines: dict[str, Any], service_factory, checkpointer=None):
        self.store = store
        self.settings = settings
        self.pipelines = pipelines                    # name -> Pipeline
        self._service_factory = service_factory       # (pipeline_name) -> JobService
        self._checkpointer = checkpointer
        self._graphs: dict[str, Any] = {}

    # -- infrastructure -----------------------------------------------------
    async def checkpointer(self):
        if self._checkpointer is not None:
            return self._checkpointer
        if getattr(self.settings, "fe_store", "json") == "postgres" and self.settings.fe_db_url:
            from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver  # noqa: PLC0415
            from psycopg import AsyncConnection  # noqa: PLC0415
            from psycopg.rows import dict_row  # noqa: PLC0415
            conn = await AsyncConnection.connect(self.settings.fe_db_url, autocommit=True,
                                                 row_factory=dict_row, prepare_threshold=0)
            saver = AsyncPostgresSaver(conn)
            await saver.setup()
            self._checkpointer = saver
        else:
            from langgraph.checkpoint.memory import MemorySaver  # noqa: PLC0415
            self._checkpointer = MemorySaver()
        return self._checkpointer

    async def graph(self, pipeline_name: str):
        if pipeline_name not in self._graphs:
            pipeline = self.pipelines[pipeline_name]
            self._graphs[pipeline_name] = build_graph(
                pipeline, store=self.store, checkpointer=await self.checkpointer(),
                service_factory=lambda n=pipeline_name: self._service_factory(n),
                mini_pipeline_name=getattr(self.settings, "fe_pipeline_mini", None),
                architecture_pipeline_name=getattr(self.settings, "fe_pipeline_architecture", None),
                pipelines=self.pipelines)
        return self._graphs[pipeline_name]

    @staticmethod
    def _cfg(workspace_id: str) -> dict:
        return {"configurable": {"thread_id": workspace_id}}

    # -- lifecycle ------------------------------------------------------------
    async def start(self, workspace_id: str, *, initiated_by: str | None = None) -> dict:
        ws = self.store.get_workspace(workspace_id)
        if ws is None:
            raise ValueError(f"unknown workspace {workspace_id}")
        cur = (ws.orchestration or {}).get("status")
        if cur in ("running", "waiting", "handed_off", "fanned_out",
                   "pending_minis", "pending_sync", "pending_architecture"):
            return {"workspace_id": workspace_id, "status": cur, "detail": "already running"}
        graph = await self.graph(ws.pipeline)
        state = {"project_id": ws.kb_application_id, "workspace_id": workspace_id, "tier": ws.tier.value,
                 "pipeline": ws.pipeline, "epic_id": ws.epic_id, "initiated_by": initiated_by,
                 "run_ids": {}, "approved": {}, "retries": {}, "epic_workspace_ids": [],
                 "current_epic_idx": 0, "handed_off": False, "error": None, "finished": False}
        logger.info("orchestrator: start thread %s (%s)", workspace_id, ws.pipeline)
        result = await graph.ainvoke(state, config=self._cfg(workspace_id))
        return await self._after_step(workspace_id, result)

    async def resume(self, workspace_id: str, decision: dict) -> dict:
        ws = self.store.get_workspace(workspace_id)
        if ws is None:
            raise ValueError(f"unknown workspace {workspace_id}")
        graph = await self.graph(ws.pipeline)
        logger.info("orchestrator: resume thread %s with %s", workspace_id, decision.get("decision") or decision.get("kind"))
        result = await graph.ainvoke(Command(resume=decision), config=self._cfg(workspace_id))
        return await self._after_step(workspace_id, result)

    async def _after_step(self, workspace_id: str, result: dict) -> dict:
        # Global thread parked at arch_sync_gate -> start the Architecture thread.
        arch_started = await self._maybe_start_architecture(workspace_id)
        # Global thread reached fan_out (status="fanned_out") -> start Mini threads.
        # During the per-EPIC loop (feature/user-story/coverage) the global status
        # is "running" or "waiting", so we must NOT start Mini threads until the
        # loop is complete and fan_out_node has fired.
        started = []
        gws = self.store.get_workspace(workspace_id)
        gws_status = (gws.orchestration or {}).get("status") if gws else None
        if gws_status == "fanned_out":
            for mini_id in (result or {}).get("epic_workspace_ids") or []:
                mini = self.store.get_workspace(mini_id)
                if mini is None or (mini.orchestration or {}).get("status") in (
                        "running", "waiting", "handed_off", "completed"):
                    continue
                try:
                    await self.start(mini_id, initiated_by="orchestrator")
                    started.append(mini_id)
                except Exception as exc:  # noqa: BLE001
                    logger.exception("could not start Mini thread %s: %s", mini_id, exc)
        return (await self.status(workspace_id)) | {"mini_started": started,
                                                    "architecture_started": arch_started}

    async def _maybe_start_architecture(self, workspace_id: str) -> str | None:
        """A Global thread parked at arch_sync_gate needs its Architecture Workspace
        thread started. Idempotent: skips one already running/finished."""
        from app.agentic_platform.fe_core.workspaces.models import architecture_workspace_id  # noqa: PLC0415
        ws = self.store.get_workspace(workspace_id)
        if ws is None or (ws.orchestration or {}).get("status") != "pending_architecture":
            return None
        arch_id = ((ws.orchestration or {}).get("architecture_workspace_id")
                   or architecture_workspace_id(ws.kb_application_id))
        arch_ws = self.store.get_workspace(arch_id)
        if arch_ws is None:
            return None
        arch_status = (arch_ws.orchestration or {}).get("status")
        if arch_status in ("running", "waiting", "handed_off", "fanned_out",
                           "pending_minis", "pending_sync", "pending_architecture", "completed"):
            return None
        try:
            await self.start(arch_id, initiated_by="orchestrator")
            logger.info("orchestrator: started Architecture thread %s", arch_id)
            return arch_id
        except Exception as exc:  # noqa: BLE001
            logger.exception("could not start Architecture thread %s: %s", arch_id, exc)
            return None

    async def status(self, workspace_id: str) -> dict:
        ws = self.store.get_workspace(workspace_id)
        info = dict((ws.orchestration if ws else None) or {})
        info.setdefault("status", "idle")
        info["workspace_id"] = workspace_id
        try:
            if ws is not None:
                graph = await self.graph(ws.pipeline)
                snap = await graph.aget_state(self._cfg(workspace_id))
                if snap is not None:
                    info["next"] = list(snap.next or [])
                    intr = [getattr(t, "interrupts", ()) for t in (snap.tasks or [])]
                    info["interrupt"] = [i.value for ii in intr for i in ii] if intr else []
        except Exception:  # noqa: BLE001
            pass
        return info

    # -- worker hooks -----------------------------------------------------------
    async def consume_intake_events(self, limit: int = 20) -> list[str]:
        """Start the Global thread for each `intake_ready` outbox event."""
        started: list[str] = []
        for ev in self.store.pending_outbox(limit=200):
            if ev.operation != "intake_ready":
                continue
            art = self.store.get_artifact(ev.aggregate_id)
            if art is None or not art.workspace_id:
                self.store.mark_outbox(ev.event_id, OutboxStatus.FAILED, error="input artefact missing")
                continue
            if art.workspace_id == _library_workspace_id():
                # The global card library is built on demand (POST /library/build).
                # A thread would run its one stage and then close the workspace,
                # which would refuse every later rebuild.
                self.store.mark_outbox(ev.event_id, OutboxStatus.SENT)
                continue
            try:
                await self.start(art.workspace_id, initiated_by=art.created_by)
                started.append(art.workspace_id)
                self.store.mark_outbox(ev.event_id, OutboxStatus.SENT)
            except Exception as exc:  # noqa: BLE001
                logger.exception("intake_ready for %s failed: %s", art.workspace_id, exc)
                self.store.mark_outbox(ev.event_id, OutboxStatus.PENDING, error=str(exc)[:300])
            if len(started) >= limit:
                break
        return started

    async def consume_resume_intents(self, limit: int = 20) -> list[str]:
        """Feed recorded human decisions to their threads."""
        resumed: list[str] = []
        for run in self.store.list_runs():
            if not run.graph_thread_id or not run.resume_intent:
                continue
            if run.state is not StageState.WAITING_FOR_APPROVAL:
                run.resume_intent = None
                self.store.save_run(run)
                continue
            intent = dict(run.resume_intent)
            try:
                await self.resume(run.graph_thread_id, intent)
                # Only clear intent after a successful resume so a transient
                # failure (network blip, LangGraph hiccup, SUPERSEDED artifact)
                # is retried on the next worker tick instead of being lost.
                run.resume_intent = None
                self.store.save_run(run)
                resumed.append(run.run_id)
            except Exception as exc:  # noqa: BLE001
                logger.exception("resume for %s failed: %s", run.run_id, exc)
            if len(resumed) >= limit:
                break
        return resumed

    async def consume_merge_checks(self, limit: int = 20) -> list[str]:
        """Global threads parked in `merge`: nudge `pending_minis` when a Mini closed;
        resume `pending_sync` when a promote decision was recorded by the API."""
        resumed: list[str] = []
        for ws in self.store.list_workspaces(tier="global", status="open"):
            info = ws.orchestration or {}
            st = info.get("status")
            try:
                if st == "pending_minis":
                    if any(w.is_open for w in self.store.list_workspaces(ws.kb_application_id, tier="mini")):
                        continue
                    await self.resume(ws.id, {"kind": "merge_check", "at": datetime.now(timezone.utc).isoformat()})
                    resumed.append(ws.id)
                elif st == "pending_sync" and (info.get("promote") or {}).get("state") == "approved":
                    promo = info.get("promote") or {}
                    await self.resume(ws.id, {"kind": "promote", "decided_by": promo.get("decided_by"),
                                              "at": promo.get("decided_at"), "result": promo.get("result")})
                    resumed.append(ws.id)
            except Exception as exc:  # noqa: BLE001
                logger.exception("merge check for %s failed: %s", ws.id, exc)
            if len(resumed) >= limit:
                break
        return resumed

    async def consume_arch_sync_checks(self, limit: int = 20) -> list[str]:
        """Global threads parked at arch_sync_gate: resume once the paired
        Architecture Workspace has closed (SRD approved)."""
        from app.agentic_platform.fe_core.workspaces.models import architecture_workspace_id  # noqa: PLC0415
        resumed: list[str] = []
        for ws in self.store.list_workspaces(tier="global", status="open"):
            info = ws.orchestration or {}
            if info.get("status") != "pending_architecture":
                continue
            arch_id = (info.get("architecture_workspace_id")
                       or architecture_workspace_id(ws.kb_application_id))
            arch_ws = self.store.get_workspace(arch_id)
            if arch_ws is None or arch_ws.is_open:
                continue  # architecture still in progress
            # Detect whether a LangGraph checkpoint exists for this workspace.
            # In langgraph mode arch_sync_gate_node parked the thread — resume it.
            # In manual mode (no LangGraph graph state) start epic-set directly.
            snap = None
            try:
                g = await self.graph(ws.pipeline)
                snap = await g.aget_state(self._cfg(ws.id))
            except Exception:  # noqa: BLE001
                pass
            try:
                if snap is not None and snap.next:
                    await self.resume(ws.id, {"kind": "arch_sync_check",
                                              "at": datetime.now(timezone.utc).isoformat()})
                    logger.info("orchestrator: resumed Global %s after arch workspace %s closed",
                                ws.id, arch_id)
                else:
                    await self._start_epic_set_direct(ws.id)
                    logger.info(
                        "orchestrator: directly started epic-set on Global %s "
                        "(no LangGraph checkpoint)", ws.id,
                    )
                resumed.append(ws.id)
            except Exception as exc:  # noqa: BLE001
                logger.exception("arch sync check for %s failed: %s", ws.id, exc)
            if len(resumed) >= limit:
                break
        return resumed

    async def _start_epic_set_direct(self, workspace_id: str) -> None:
        """Start the epic-set stage directly on a Global workspace that was
        manually orchestrated (no LangGraph checkpoint exists).

        Called from consume_arch_sync_checks() when the Architecture Workspace
        closes and there is no graph state to resume into.  The resulting
        StageRun appears in the console for the Product Owner to approve; on
        approval _fan_out_if_epic_set() opens the Mini Workspaces exactly as in
        the langgraph path.
        """
        ws = self.store.get_workspace(workspace_id)
        if ws is None:
            return
        global_pipeline_name = getattr(self.settings, "fe_pipeline_global", "uw-cr-global")
        svc = self._service_factory(global_pipeline_name)
        # execute=False so we can persist the run before starting execution,
        # matching the pattern used by the API's POST /runs endpoint.
        run = await svc.create_run(
            "epic-set",
            kb_application=ws.kb_application_id,
            workspace_id=workspace_id,
            initiated_by="orchestrator",
            force=True,
            execute=False,
        )
        self.store.save_run(run)
        logger.info(
            "orchestrator: directly starting epic-set run %s on Global %s",
            run.run_id, workspace_id,
        )
        await svc.execute_run(run.run_id)

    async def consume_hand_off_resumes(self, limit: int = 20) -> list[str]:
        """Mini threads parked at hand_off: nudge them when laptop artefacts arrived."""
        resumed: list[str] = []
        for ws in self.store.list_workspaces(tier="mini", status="open"):
            info = ws.orchestration or {}
            if info.get("status") != "handed_off":
                continue
            try:
                await self.resume(ws.id, {"kind": "hand_off_check", "at": datetime.now(timezone.utc).isoformat()})
                resumed.append(ws.id)
            except Exception as exc:  # noqa: BLE001
                logger.exception("hand-off check for %s failed: %s", ws.id, exc)
            if len(resumed) >= limit:
                break
        return resumed
