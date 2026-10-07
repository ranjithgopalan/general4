"""fe-orchestrator / worker service entrypoint (FE_EXECUTION=worker).

    python -m worker_app.main            # or the container's `worker` entrypoint

Loop:
  1. requeue RUNNING runs whose heartbeat is stale (a crashed peer)
  2. claim up to N QUEUED runs (SKIP LOCKED on Postgres; in-memory on JSON)
  3. execute each with JobService.execute_run() -> StageExecutor (same code the
     API used in-process), heart-beating while it runs
  4. sleep FE_WORKER_POLL_SECONDS

SIGTERM/SIGINT: stop claiming, let in-flight runs finish up to the ECS stop
timeout, then requeue anything still RUNNING so nothing is lost on a deploy.

Works with FE_STORE=json too (single process) so the same binary runs on a
laptop, but the design target is FE_STORE=postgres with >1 replica.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
from datetime import datetime, timezone

logger = logging.getLogger("worker_app.main")


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        stream=sys.stdout,
    )


class Worker:
    def __init__(self, *, pipelines: list[str] | None = None, max_concurrent: int = 2):
        from app.agentic_platform.fe_core.config import get_settings
        from app.agentic_platform.fe_core.pipeline.registry import get_pipeline, registry
        from app.agentic_platform.fe_core.store import get_store

        self.settings = get_settings()
        self.store = get_store()
        self.worker_id = self.settings.fe_worker_id or f"{os.uname().nodename if hasattr(os, 'uname') else os.environ.get('COMPUTERNAME', 'host')}:{os.getpid()}"
        names = pipelines or list(registry().keys())
        self.pipelines = {n: get_pipeline(n) for n in names}
        self.max_concurrent = max_concurrent
        self.stopping = asyncio.Event()
        self._inflight: dict[str, asyncio.Task] = {}
        self._services: dict[str, object] = {}

    # -- helpers ----------------------------------------------------------
    def _service(self, pipeline_name: str):
        if pipeline_name not in self._services:
            from app.agentic_platform.api.services.jobs import JobService  # noqa: PLC0415
            self._services[pipeline_name] = JobService(
                pipeline=self.pipelines[pipeline_name], store=self.store)
        return self._services[pipeline_name]

    async def _heartbeat(self, run_id: str) -> None:
        interval = float(self.settings.fe_worker_heartbeat_seconds or 15)
        try:
            while True:
                await asyncio.sleep(interval)
                # Guard both DB calls with a hard timeout so a Postgres hang cannot
                # freeze heartbeat_at and cause the run to appear stale (FR-HB-01).
                try:
                    await asyncio.wait_for(
                        asyncio.to_thread(self.store.heartbeat, run_id),
                        timeout=10.0,
                    )
                except asyncio.TimeoutError:
                    logger.warning("heartbeat: UPDATE timed out for run %s", run_id)
                try:
                    run = await asyncio.wait_for(
                        asyncio.to_thread(self.store.get_run, run_id),
                        timeout=10.0,
                    )
                except asyncio.TimeoutError:
                    logger.warning("heartbeat: get_run(%s) timed out, skipping cancel check", run_id)
                    run = None
                if run is not None and getattr(run, "cancel_requested", False):
                    task = self._inflight.get(run_id)
                    if task and not task.done():
                        logger.info("cancel requested for %s; cancelling task", run_id)
                        task.cancel()
                        return
        except asyncio.CancelledError:
            return

    async def _run_one(self, run) -> None:
        svc = self._service(run.pipeline)
        hb = asyncio.create_task(self._heartbeat(run.run_id), name=f"hb-{run.run_id}")
        try:
            logger.info("executing %s/%s run=%s ws=%s", run.pipeline, run.stage_key,
                        run.run_id, run.workspace_id)
            await svc.execute_run(run.run_id)
        finally:
            hb.cancel()

    # -- orchestrator (FE_ORCHESTRATOR=langgraph) --------------------------------
    def _orchestrator(self):
        if getattr(self.settings, "fe_orchestrator", "manual") != "langgraph":
            return None
        if getattr(self, "_orch", None) is None:
            from app.agentic_platform.worker.orchestrator.service import OrchestratorService  # noqa: PLC0415
            self._orch = OrchestratorService(
                store=self.store, settings=self.settings, pipelines=self.pipelines,
                service_factory=self._service)
        return self._orch

    async def _orchestrate(self) -> None:
        orch = self._orchestrator()
        if orch is None:
            return
        try:
            started = await orch.consume_intake_events()
            # explicit start requests from POST /workspaces/{id}/orchestrate
            for ws in self.store.list_workspaces():
                if (ws.orchestration or {}).get("status") == "requested":
                    try:
                        await orch.start(ws.id, initiated_by=(ws.orchestration or {}).get("requested_by"))
                        started.append(ws.id)
                    except Exception as exc:  # noqa: BLE001
                        logger.exception("orchestrate %s failed: %s", ws.id, exc)
            resumed = await orch.consume_resume_intents()
            handed = await orch.consume_hand_off_resumes()
            merged = await orch.consume_merge_checks()
            arch = await orch.consume_arch_sync_checks()
            if started or resumed or handed or merged or arch:
                logger.info("orchestrator: started=%s resumed=%s hand-off-checked=%s "
                            "merge-checked=%s arch-checked=%s",
                            started, resumed, handed, merged, arch)
        except Exception as exc:  # noqa: BLE001
            logger.exception("orchestrator tick failed: %s", exc)

    def _spawn_claimed_runs(self, claimed: list) -> None:
        for run in claimed:
            if run.pipeline not in self.pipelines:
                continue
            t = asyncio.create_task(self._run_one(run), name=f"run-{run.run_id}")
            self._inflight[run.run_id] = t
            t.add_done_callback(lambda _t, rid=run.run_id: self._inflight.pop(rid, None))

    # -- main loop --------------------------------------------------------
    async def run_forever(self) -> None:
        logger.info("worker %s started: store=%s pipelines=%s execution=%s runner=%s",
                    self.worker_id, getattr(self.store, "kind", "?"), ",".join(self.pipelines),
                    self.settings.fe_execution, self.settings.fe_runner)
        poll = float(self.settings.fe_worker_poll_seconds or 2)
        stale_after = int(float(self.settings.fe_worker_heartbeat_seconds or 15) * 6)
        while not self.stopping.is_set():
            stop_file = self.settings.fe_workspace_root / "WORKER_STOP"
            if stop_file.exists():
                stop_file.unlink(missing_ok=True)
                logger.info("WORKER_STOP signal — shutting down CLI worker after SRD approval")
                self.stopping.set()
                break
            try:
                stale = await asyncio.to_thread(self.store.requeue_stale, stale_after)
                if stale:
                    logger.warning("requeued %d stale run(s): %s", len(stale), ", ".join(stale))
                free = self.max_concurrent - len([t for t in self._inflight.values() if not t.done()])
                if free > 0:
                    claimed = await asyncio.to_thread(
                        self.store.claim_queued_runs, self.worker_id, free,
                        self.settings.fe_worker_workspace_filter,
                        self.settings.fe_runner)
                    self._spawn_claimed_runs(claimed)
                await self._orchestrate()
            except Exception as exc:  # noqa: BLE001
                logger.exception("worker loop error: %s", exc)
            try:
                await asyncio.wait_for(self.stopping.wait(), timeout=poll)
            except asyncio.TimeoutError:
                pass
        await self._shutdown()

    async def _shutdown(self) -> None:
        inflight = [t for t in self._inflight.values() if not t.done()]
        if inflight:
            grace = float(os.environ.get("FE_WORKER_STOP_GRACE_SECONDS", "25"))
            logger.info("stopping: waiting up to %.0fs for %d in-flight run(s)", grace, len(inflight))
            done, pending = await asyncio.wait(inflight, timeout=grace)
            for t in pending:
                t.cancel()
            # anything still RUNNING for this worker goes back to QUEUED
            for run_id in list(self._inflight):
                run = self.store.get_run(run_id)
                if run is not None and run.state.value == "running":
                    run.state = type(run.state)("queued")
                    run.claimed_by = None
                    run.log.append(f"requeued: worker {self.worker_id} shutdown at "
                                   f"{datetime.now(timezone.utc).isoformat()}")
                    self.store.save_run(run)
        logger.info("worker %s stopped", self.worker_id)

    def request_stop(self, *_):
        logger.info("stop requested")
        self.stopping.set()


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="ADLC forward-engineering worker / orchestrator")
    ap.add_argument("--pipelines", help="comma-separated pipeline names (default: all)")
    ap.add_argument("--concurrency", type=int, default=int(os.environ.get("FE_WORKER_CONCURRENCY", "2")))
    ap.add_argument("--once", action="store_true", help="claim/execute one batch then exit (tests, cron)")
    args = ap.parse_args(argv)

    from app.agentic_platform.fe_core.config import get_settings
    _setup_logging(get_settings().fe_log_level)

    w = Worker(pipelines=args.pipelines.split(",") if args.pipelines else None,
               max_concurrent=args.concurrency)

    async def _amain():
        loop = asyncio.get_running_loop()
        for sig in (getattr(signal, "SIGTERM", None), getattr(signal, "SIGINT", None)):
            if sig is not None:
                try:
                    loop.add_signal_handler(sig, w.request_stop)
                except NotImplementedError:      # Windows
                    signal.signal(sig, w.request_stop)
        if args.once:
            claimed = await asyncio.to_thread(
                w.store.claim_queued_runs, w.worker_id, args.concurrency,
                w.settings.fe_worker_workspace_filter,
                w.settings.fe_runner)
            await asyncio.gather(*(w._run_one(r) for r in claimed))
            await w._orchestrate()
            return
        await w.run_forever()

    if sys.platform.startswith("win"):
        # psycopg async (LangGraph Postgres checkpointer) cannot run on the
        # default ProactorEventLoop; harmless elsewhere.
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(_amain())
    return 0


if __name__ == "__main__":
    sys.exit(main())
