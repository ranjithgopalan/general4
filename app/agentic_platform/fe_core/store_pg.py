"""PostgresStore — the control-plane store for ECS (FE_STORE=postgres).

Same public surface as `JsonFileStore` (runs, workspaces, artefacts, approvals,
outbox, lineage) plus the queue primitives a separate worker service needs:

    claim_queued_runs(worker_id, limit)   SELECT ... FOR UPDATE SKIP LOCKED
    heartbeat(run_id)
    requeue_stale(older_than_seconds)     RUNNING runs whose heartbeat went silent
    claim_outbox(limit)                   pending outbox events, SKIP LOCKED

Design
------
Document tables. Each row keeps the *query/lock* columns (ids, state, timestamps,
idempotency keys) as real columns and the full pydantic model as `doc jsonb`.
That gives exact parity with the JSON store (whatever the model holds, we store),
transactions and row locks, and it does not break when a model gains a field.
The rich normalised `fe_stage_run` / `fe_sdlc_artifact` tables in schema.sql stay
as reporting/analytics targets that can be projected from these documents later.

Tables (schema = FE_DB_SCHEMA, default `fe`):
    fe_state_run, fe_state_workspace, fe_state_artifact, fe_state_approval,
    fe_state_outbox — created idempotently by `ensure_schema()`.

Connections: psycopg3, one short-lived connection per call (autocommit off,
commit per method) — simple and correct for the API + worker load; a pool can be
slotted in later behind `_connect()` without touching the methods.
"""

from __future__ import annotations

import json
import logging
import socket
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from app.agentic_platform.fe_core.kb.models import (
    Approval,
    ApprovalDecision,
    ArtifactStatus,
    OutboxEvent,
    OutboxStatus,
    SdlcArtifact,
)
from app.agentic_platform.fe_core.pipeline.models import StageRun, StageState
from app.agentic_platform.fe_core.store import ConcurrencyError, _now

logger = logging.getLogger(__name__)

_CREATE_SCHEMA = "CREATE SCHEMA IF NOT EXISTS {schema}"

DDL = """

CREATE TABLE IF NOT EXISTS {schema}.fe_state_run (
    run_id            text PRIMARY KEY,
    pipeline          text NOT NULL,
    stage_key         text NOT NULL,
    workspace_id      text,
    kb_application_id text,
    idempotency_key   text,
    state             text NOT NULL,
    queued_at         timestamptz,
    claimed_by        text,
    claimed_at        timestamptz,
    heartbeat_at      timestamptz,
    doc               jsonb NOT NULL,
    updated_at        timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS fe_state_run_idem ON {schema}.fe_state_run (idempotency_key) WHERE idempotency_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS fe_state_run_queue ON {schema}.fe_state_run (state, queued_at);
CREATE INDEX IF NOT EXISTS fe_state_run_scope ON {schema}.fe_state_run (pipeline, stage_key, workspace_id);

CREATE TABLE IF NOT EXISTS {schema}.fe_state_workspace (
    id                text PRIMARY KEY,
    kb_application_id text NOT NULL,
    tier              text NOT NULL,
    status            text NOT NULL,
    epic_id           text,
    doc               jsonb NOT NULL,
    updated_at        timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS fe_state_workspace_app ON {schema}.fe_state_workspace (kb_application_id, tier);

CREATE TABLE IF NOT EXISTS {schema}.fe_state_artifact (
    id                text PRIMARY KEY,
    pipeline          text NOT NULL,
    stage_key         text NOT NULL,
    artifact_type     text NOT NULL,
    workspace_id      text,
    epic_id           text,
    tier              text,
    version           integer NOT NULL,
    status            text NOT NULL,
    checksum          text,
    parent_id         text,
    doc               jsonb NOT NULL,
    updated_at        timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS fe_state_artifact_scope ON {schema}.fe_state_artifact (pipeline, stage_key, artifact_type, workspace_id);
CREATE INDEX IF NOT EXISTS fe_state_artifact_ws ON {schema}.fe_state_artifact (workspace_id);
CREATE INDEX IF NOT EXISTS fe_state_artifact_parent ON {schema}.fe_state_artifact (parent_id);

CREATE TABLE IF NOT EXISTS {schema}.fe_state_approval (
    approval_id  text PRIMARY KEY,
    artifact_id  text NOT NULL,
    decided_at   timestamptz NOT NULL,
    doc          jsonb NOT NULL
);
CREATE INDEX IF NOT EXISTS fe_state_approval_art ON {schema}.fe_state_approval (artifact_id, decided_at);

CREATE TABLE IF NOT EXISTS {schema}.fe_state_outbox (
    event_id         text PRIMARY KEY,
    idempotency_key  text NOT NULL UNIQUE,
    status           text NOT NULL,
    created_at       timestamptz NOT NULL,
    next_attempt_at  timestamptz,
    claimed_at       timestamptz,
    doc              jsonb NOT NULL
);
CREATE INDEX IF NOT EXISTS fe_state_outbox_pending ON {schema}.fe_state_outbox (status, created_at);
CREATE TABLE IF NOT EXISTS {schema}.fe_state_audit (
    audit_id     text PRIMARY KEY,
    at           timestamptz NOT NULL DEFAULT now(),
    action       text NOT NULL,
    actor        text,
    persona      text,
    workspace_id text,
    artifact_id  text,
    run_id       text,
    doc          jsonb NOT NULL
);
CREATE INDEX IF NOT EXISTS fe_state_audit_ws ON {schema}.fe_state_audit (workspace_id, at);
"""


def _dump(model) -> str:
    return json.dumps(model.model_dump(mode="json"), ensure_ascii=False)


class PostgresStore:
    """Drop-in for JsonFileStore backed by Postgres document tables."""

    kind = "postgres"

    def __init__(self, url: str, schema: str = "fe", *, ensure: bool = True):
        if not url:
            raise ValueError("FE_DB_URL is required for FE_STORE=postgres")
        try:
            import psycopg  # noqa: F401
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("PostgresStore needs psycopg: pip install 'psycopg[binary]'") from exc
        self.url = url
        self.schema = "".join(c for c in schema if c.isalnum() or c == "_") or "fe"
        self._pool = None
        if ensure:
            self.ensure_schema()

    # -- connection -----------------------------------------------------------
    def _ensure_pool(self):
        """Lazily create a reused connection pool.

        The store previously opened a fresh connection per call; over a remote RDS
        link that made list endpoints (many per-row queries) take tens of seconds.
        A small warm pool keeps each call to a single round-trip.
        """
        if self._pool is None:
            from psycopg.rows import dict_row  # noqa: PLC0415
            from psycopg_pool import ConnectionPool  # noqa: PLC0415

            self._pool = ConnectionPool(
                self.url,
                min_size=1,
                max_size=8,
                timeout=10,
                kwargs={"row_factory": dict_row, "connect_timeout": 8},
                open=True,
            )
        return self._pool

    @contextmanager
    def _connect(self):
        with self._ensure_pool().connection() as conn:
            yield conn
            conn.commit()

    def _t(self, name: str) -> str:
        return f"{self.schema}.{name}"

    def ensure_schema(self) -> None:
        import psycopg  # noqa: PLC0415
        with self._connect() as conn, conn.cursor() as cur:
            # Try to create the schema; skip gracefully if the user lacks CREATE
            # SCHEMA privilege but the schema was already created by a DBA.
            try:
                cur.execute(_CREATE_SCHEMA.format(schema=self.schema))
                conn.commit()
            except psycopg.errors.InsufficientPrivilege:
                conn.rollback()
            # Create all fe_state_* tables and indexes (IF NOT EXISTS — idempotent).
            # If the user lacks CREATE on the schema, the tables were already provisioned
            # by a DBA/migration, so skip gracefully rather than failing every request.
            try:
                cur.execute(DDL.format(schema=self.schema))
                conn.commit()
            except psycopg.errors.InsufficientPrivilege:
                conn.rollback()
                logger.info("PostgresStore: no CREATE on %s; assuming tables pre-provisioned", self.schema)
        logger.info("PostgresStore ready (schema %s)", self.schema)

    # -- runs -----------------------------------------------------------------
    def _run_row(self, run: StageRun) -> tuple:
        return (run.run_id, run.pipeline, run.stage_key, run.workspace_id, run.kb_application_id,
                run.idempotency_key, run.state.value, run.queued_at, _dump(run))

    def create_run(self, run: StageRun) -> StageRun:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(f"SELECT 1 FROM {self._t('fe_state_run')} WHERE run_id=%s", (run.run_id,))
            if cur.fetchone():
                raise ValueError(f"run already exists: {run.run_id}")
            cur.execute(
                f"INSERT INTO {self._t('fe_state_run')} (run_id, pipeline, stage_key, workspace_id, "
                f"kb_application_id, idempotency_key, state, queued_at, doc) "
                f"VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)", self._run_row(run))
        return run

    def save_run(self, run: StageRun) -> StageRun:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                f"INSERT INTO {self._t('fe_state_run')} (run_id, pipeline, stage_key, workspace_id, "
                f"kb_application_id, idempotency_key, state, queued_at, doc) "
                f"VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb) "
                f"ON CONFLICT (run_id) DO UPDATE SET state=EXCLUDED.state, doc=EXCLUDED.doc, "
                f"idempotency_key=EXCLUDED.idempotency_key, workspace_id=EXCLUDED.workspace_id, "
                f"claimed_by=%s, claimed_at=%s, heartbeat_at=%s, updated_at=now()",
                self._run_row(run) + (run.claimed_by, run.claimed_at, run.heartbeat_at))
        return run

    def get_run(self, run_id: str) -> StageRun | None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(f"SELECT doc FROM {self._t('fe_state_run')} WHERE run_id=%s", (run_id,))
            row = cur.fetchone()
        return StageRun.model_validate(row["doc"]) if row else None

    def find_run_by_idempotency_key(self, key: str) -> StageRun | None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(f"SELECT doc FROM {self._t('fe_state_run')} WHERE idempotency_key=%s LIMIT 1", (key,))
            row = cur.fetchone()
        return StageRun.model_validate(row["doc"]) if row else None

    def list_runs(self, pipeline: str | None = None, stage_key: str | None = None, *,
                  workspace_id: str | None = None) -> list[StageRun]:
        sql = f"SELECT doc FROM {self._t('fe_state_run')} WHERE 1=1"
        args: list[Any] = []
        if pipeline:
            sql += " AND pipeline=%s"; args.append(pipeline)
        if stage_key:
            sql += " AND stage_key=%s"; args.append(stage_key)
        if workspace_id is not None:
            sql += " AND workspace_id IS NOT DISTINCT FROM %s"; args.append(workspace_id)
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(sql, args)
            runs = [StageRun.model_validate(r["doc"]) for r in cur.fetchall()]
        return sorted(runs, key=lambda r: r.queued_at or r.started_at
                      or datetime.min.replace(tzinfo=timezone.utc), reverse=True)

    def latest_run(self, pipeline: str, stage_key: str, *, workspace_id: str | None = None) -> StageRun | None:
        runs = self.list_runs(pipeline, stage_key, workspace_id=workspace_id)
        return runs[0] if runs else None

    def next_attempt(self, pipeline: str, stage_key: str, *, workspace_id: str | None = None) -> int:
        return 1 + len(self.list_runs(pipeline, stage_key, workspace_id=workspace_id))

    # -- worker queue primitives ---------------------------------------------
    def claim_queued_runs(self, worker_id: str, limit: int = 1,
                          workspace_filter: str | None = None,
                          runner_affinity: str | None = None) -> list[StageRun]:
        """Atomically hand `limit` QUEUED runs to this worker (SKIP LOCKED).

        workspace_filter:  only claim runs whose workspace_id starts with this prefix.
        runner_affinity:   worker's FE_RUNNER value; skips runs whose required_runner
                           doesn't match (runs with no required_runner are always eligible).
        """
        now = _now()
        claimed: list[StageRun] = []
        params: list = [StageState.QUEUED.value]
        ws_clause = ""
        runner_clause = ""
        if workspace_filter:
            ws_clause = "AND workspace_id LIKE %s "
            params.append(workspace_filter.rstrip("%") + "%")
        if runner_affinity:
            runner_clause = "AND (doc->>'required_runner' IS NULL OR doc->>'required_runner' = %s) "
            params.append(runner_affinity)
        params.append(int(limit))
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                f"SELECT run_id, doc FROM {self._t('fe_state_run')} WHERE state=%s AND claimed_by IS NULL "
                f"{ws_clause}{runner_clause}"
                f"ORDER BY queued_at NULLS LAST, run_id LIMIT %s FOR UPDATE SKIP LOCKED",
                params)
            rows = cur.fetchall()
            for r in rows:
                run = StageRun.model_validate(r["doc"])
                run.claimed_by = worker_id
                run.claimed_at = now
                run.heartbeat_at = now
                cur.execute(
                    f"UPDATE {self._t('fe_state_run')} SET claimed_by=%s, claimed_at=%s, heartbeat_at=%s, "
                    f"doc=%s::jsonb, updated_at=now() WHERE run_id=%s",
                    (worker_id, now, now, _dump(run), run.run_id))
                claimed.append(run)
        return claimed

    def heartbeat(self, run_id: str) -> None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(f"UPDATE {self._t('fe_state_run')} SET heartbeat_at=now() WHERE run_id=%s", (run_id,))

    def requeue_stale(self, older_than_seconds: int = 120) -> list[str]:
        """RUNNING (or claimed-but-stuck QUEUED) runs whose worker stopped heart-beating go back to QUEUED."""
        cutoff = _now() - timedelta(seconds=older_than_seconds)
        requeued: list[str] = []
        with self._connect() as conn, conn.cursor() as cur:
            # Also rescue QUEUED rows whose claimed_by was set but the worker crashed before
            # transitioning state to RUNNING — neither claim_queued_runs nor the original
            # requeue_stale would touch these, leaving them stuck forever.
            cur.execute(
                f"SELECT run_id, doc FROM {self._t('fe_state_run')} "
                f"WHERE (state=%s OR (state=%s AND claimed_by IS NOT NULL)) "
                f"AND (heartbeat_at IS NULL OR heartbeat_at < %s) FOR UPDATE SKIP LOCKED",
                (StageState.RUNNING.value, StageState.QUEUED.value, cutoff))
            for r in cur.fetchall():
                run = StageRun.model_validate(r["doc"])
                run.state = StageState.QUEUED
                run.claimed_by = None
                run.claimed_at = None
                run.log.append(f"requeued: worker heartbeat stale (> {older_than_seconds}s)")
                cur.execute(
                    f"UPDATE {self._t('fe_state_run')} SET state=%s, claimed_by=NULL, claimed_at=NULL, "
                    f"doc=%s::jsonb, updated_at=now() WHERE run_id=%s",
                    (StageState.QUEUED.value, _dump(run), run.run_id))
                requeued.append(run.run_id)
        return requeued

    # -- workspaces -------------------------------------------------------------
    def put_workspace(self, workspace):
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                f"INSERT INTO {self._t('fe_state_workspace')} (id, kb_application_id, tier, status, epic_id, doc) "
                f"VALUES (%s,%s,%s,%s,%s,%s::jsonb) ON CONFLICT (id) DO UPDATE SET status=EXCLUDED.status, "
                f"doc=EXCLUDED.doc, updated_at=now()",
                (workspace.id, workspace.kb_application_id, workspace.tier.value, workspace.status.value,
                 workspace.epic_id, _dump(workspace)))
        return workspace

    def _ws(self, doc):
        from app.agentic_platform.fe_core.workspaces.models import Workspace  # noqa: PLC0415
        return Workspace.model_validate(doc)

    def get_workspace(self, workspace_id: str):
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(f"SELECT doc FROM {self._t('fe_state_workspace')} WHERE id=%s", (workspace_id,))
            row = cur.fetchone()
        return self._ws(row["doc"]) if row else None

    def list_workspaces(self, kb_application_id: str | None = None, *, tier: str | None = None,
                        status: str | None = None):
        sql = f"SELECT doc FROM {self._t('fe_state_workspace')} WHERE 1=1"
        args: list[Any] = []
        if kb_application_id:
            sql += " AND kb_application_id=%s"; args.append(kb_application_id)
        if tier:
            sql += " AND tier=%s"; args.append(tier)
        if status:
            sql += " AND status=%s"; args.append(status)
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(sql, args)
            rows = [self._ws(r["doc"]) for r in cur.fetchall()]
        return sorted(rows, key=lambda w: (w.tier.value != "global", w.id))

    def find_workspace_for_epic(self, kb_application_id: str, epic_id: str):
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(f"SELECT doc FROM {self._t('fe_state_workspace')} WHERE kb_application_id=%s AND epic_id=%s LIMIT 1",
                        (kb_application_id, epic_id))
            row = cur.fetchone()
        return self._ws(row["doc"]) if row else None

    def delete_project(self, kb_application_id: str) -> int:
        """Purge every trace of a project so it can be re-onboarded cleanly.

        Core state (workspaces, runs, artefacts, approvals, audit, outbox) is
        deleted in one transaction. The RAG chunk index and the LangGraph
        checkpoints are purged best-effort in their own transactions afterwards,
        so a missing/optional table can never roll back the core delete.
        """
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(f"SELECT id FROM {self._t('fe_state_workspace')} WHERE kb_application_id=%s",
                        (kb_application_id,))
            ws_ids = [r["id"] for r in cur.fetchall()]
            art_ids: list[str] = []
            if ws_ids:
                cur.execute(f"SELECT id FROM {self._t('fe_state_artifact')} WHERE workspace_id = ANY(%s)",
                            (ws_ids,))
                art_ids = [r["id"] for r in cur.fetchall()]
            if art_ids:
                cur.execute(f"DELETE FROM {self._t('fe_state_approval')} WHERE artifact_id = ANY(%s)", (art_ids,))
                cur.execute(f"DELETE FROM {self._t('fe_state_outbox')} WHERE doc->>'aggregate_id' = ANY(%s)",
                            (art_ids,))
                cur.execute(f"DELETE FROM {self._t('fe_state_artifact')} WHERE id = ANY(%s)", (art_ids,))
            if ws_ids:
                cur.execute(f"DELETE FROM {self._t('fe_state_run')} WHERE workspace_id = ANY(%s)", (ws_ids,))
                cur.execute(f"DELETE FROM {self._t('fe_state_audit')} WHERE workspace_id = ANY(%s)", (ws_ids,))
                cur.execute(f"DELETE FROM {self._t('fe_state_workspace')} WHERE id = ANY(%s)", (ws_ids,))

        # Auxiliary purges — own transactions, never fatal to the core delete.
        self._purge_best_effort("RAG chunks",
                                "DELETE FROM fe_kb_chunk WHERE project_id = %s", (kb_application_id,))
        if ws_ids:
            for tbl in ("checkpoint_writes", "checkpoint_blobs", "checkpoints"):
                self._purge_best_effort("checkpoints",
                                        f"DELETE FROM {tbl} WHERE thread_id = ANY(%s)", (ws_ids,))
        logger.info("Deleted project '%s': %d workspace(s), %d artefact(s)",
                    kb_application_id, len(ws_ids), len(art_ids))
        return len(ws_ids)

    def _purge_best_effort(self, label: str, sql: str, params: tuple) -> None:
        """Run an optional cleanup DELETE in its own transaction; log-and-ignore
        when the table does not exist (RAG not provisioned, MemorySaver, etc.)."""
        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute(sql, params)
        except Exception as exc:  # noqa: BLE001
            logger.debug("delete_project: %s purge skipped: %s", label, exc)

    # -- artefacts --------------------------------------------------------------
    def _art_row(self, a: SdlcArtifact) -> tuple:
        return (a.id, a.pipeline, a.stage_key, a.artifact_type, a.workspace_id, a.epic_id, a.tier,
                a.version, a.status.value, a.checksum or a.content_sha256, a.parent_id, _dump(a))

    def put_artifact(self, artifact: SdlcArtifact) -> SdlcArtifact:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                f"INSERT INTO {self._t('fe_state_artifact')} (id, pipeline, stage_key, artifact_type, workspace_id, "
                f"epic_id, tier, version, status, checksum, parent_id, doc) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb) "
                f"ON CONFLICT (id) DO UPDATE SET status=EXCLUDED.status, checksum=EXCLUDED.checksum, "
                f"doc=EXCLUDED.doc, updated_at=now()", self._art_row(artifact))
        return artifact

    def get_artifact(self, artifact_id: str) -> SdlcArtifact | None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(f"SELECT doc FROM {self._t('fe_state_artifact')} WHERE id=%s", (artifact_id,))
            row = cur.fetchone()
        return SdlcArtifact.model_validate(row["doc"]) if row else None

    def list_artifacts(self, pipeline: str | None = None, stage_key: str | None = None,
                       artifact_type: str | None = None, status: ArtifactStatus | None = None, *,
                       workspace_id: str | None = None, epic_id: str | None = None,
                       tier: str | None = None, _unscoped: bool = False) -> list[SdlcArtifact]:
        sql = f"SELECT doc FROM {self._t('fe_state_artifact')} WHERE 1=1"
        args: list[Any] = []
        if pipeline:
            sql += " AND pipeline=%s"; args.append(pipeline)
        if stage_key:
            sql += " AND stage_key=%s"; args.append(stage_key)
        if artifact_type:
            sql += " AND artifact_type=%s"; args.append(artifact_type)
        if status:
            sql += " AND status=%s"; args.append(status.value)
        if workspace_id is not None:
            sql += " AND workspace_id IS NOT DISTINCT FROM %s"; args.append(workspace_id)
        if epic_id is not None:
            sql += " AND epic_id IS NOT DISTINCT FROM %s"; args.append(epic_id)
        if tier is not None:
            sql += " AND tier IS NOT DISTINCT FROM %s"; args.append(tier)
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(sql, args)
            rows = [SdlcArtifact.model_validate(r["doc"]) for r in cur.fetchall()]
        return sorted(rows, key=lambda a: (a.stage_key, a.artifact_type, a.version))

    def find_artifact_by_checksum(self, pipeline: str, stage_key: str, artifact_type: str, checksum: str,
                                  *, workspace_id: str | None) -> SdlcArtifact | None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                f"SELECT doc FROM {self._t('fe_state_artifact')} WHERE pipeline=%s AND stage_key=%s AND artifact_type=%s "
                f"AND workspace_id IS NOT DISTINCT FROM %s AND checksum=%s LIMIT 1",
                (pipeline, stage_key, artifact_type, workspace_id, checksum))
            row = cur.fetchone()
        return SdlcArtifact.model_validate(row["doc"]) if row else None

    def next_version(self, pipeline: str, stage_key: str, artifact_type: str, *, workspace_id: str | None) -> int:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                f"SELECT COALESCE(MAX(version),0) AS v FROM {self._t('fe_state_artifact')} WHERE pipeline=%s "
                f"AND stage_key=%s AND artifact_type=%s AND workspace_id IS NOT DISTINCT FROM %s",
                (pipeline, stage_key, artifact_type, workspace_id))
            return 1 + int(cur.fetchone()["v"])

    def supersede_previous(self, pipeline: str, stage_key: str, artifact_type: str, *, workspace_id: str | None) -> str | None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                f"SELECT id, doc FROM {self._t('fe_state_artifact')} WHERE pipeline=%s AND stage_key=%s AND artifact_type=%s "
                f"AND workspace_id IS NOT DISTINCT FROM %s AND status<>%s ORDER BY version DESC LIMIT 1 FOR UPDATE",
                (pipeline, stage_key, artifact_type, workspace_id, ArtifactStatus.SUPERSEDED.value))
            row = cur.fetchone()
            if not row:
                return None
            head = SdlcArtifact.model_validate(row["doc"])
            head.status = ArtifactStatus.SUPERSEDED
            cur.execute(f"UPDATE {self._t('fe_state_artifact')} SET status=%s, doc=%s::jsonb, updated_at=now() WHERE id=%s",
                        (head.status.value, _dump(head), head.id))
            return head.id

    def approved_artifacts(self, pipeline: str, artifact_types: Iterable[str], *,
                           workspace_ids: Iterable[str] | None = None) -> list[SdlcArtifact]:
        types = list(set(artifact_types))
        if not types:
            return []
        sql = (f"SELECT doc FROM {self._t('fe_state_artifact')} WHERE pipeline=%s AND artifact_type = ANY(%s) "
               f"AND status=%s")
        args: list[Any] = [pipeline, types, ArtifactStatus.APPROVED.value]
        if workspace_ids is not None:
            sql += " AND workspace_id = ANY(%s)"; args.append(list(set(workspace_ids)))
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(sql, args)
            return [SdlcArtifact.model_validate(r["doc"]) for r in cur.fetchall()]

    # -- approvals --------------------------------------------------------------
    def record_approval(self, artifact: SdlcArtifact, *, decision: ApprovalDecision, decided_by: str,
                        role: str | None = None, comment: str | None = None,
                        expected_version: int | None = None) -> Approval:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(f"SELECT doc FROM {self._t('fe_state_artifact')} WHERE id=%s FOR UPDATE", (artifact.id,))
            row = cur.fetchone()
            current = SdlcArtifact.model_validate(row["doc"]) if row else artifact
            if expected_version is not None and expected_version != current.version:
                raise ConcurrencyError(f"artifact {artifact.id} is at version {current.version}, "
                                       f"not {expected_version}; reload before approving")
            if current.status is ArtifactStatus.SUPERSEDED:
                raise ConcurrencyError(f"artifact {artifact.id} v{current.version} is superseded and cannot be approved")
            approval = Approval(approval_id=uuid.uuid4().hex[:12], artifact_id=artifact.id,
                                artifact_version=current.version, decision=decision, role=role,
                                decided_by=decided_by, decided_at=_now(), comment=comment)
            cur.execute(f"INSERT INTO {self._t('fe_state_approval')} (approval_id, artifact_id, decided_at, doc) "
                        f"VALUES (%s,%s,%s,%s::jsonb)",
                        (approval.approval_id, approval.artifact_id, approval.decided_at, _dump(approval)))
            artifact.status = (ArtifactStatus.APPROVED if decision is ApprovalDecision.APPROVE
                               else ArtifactStatus.REJECTED)
            artifact.approved_by = decided_by
            artifact.approved_at = approval.decided_at
            artifact.approval_feedback = comment
            cur.execute(f"UPDATE {self._t('fe_state_artifact')} SET status=%s, doc=%s::jsonb, updated_at=now() WHERE id=%s",
                        (artifact.status.value, _dump(artifact), artifact.id))
            self._audit_in(cur, action=f"artifact.{decision.value}", actor=decided_by, persona=role,
                           workspace_id=artifact.workspace_id, artifact_id=artifact.id, run_id=artifact.run_id,
                           before={"status": current.status.value}, after={"status": artifact.status.value},
                           detail={"comment": comment})
        return approval

    # -- audit (append-only; mirrors the AIDLC fe_audit_log) ----------------------
    def _audit_in(self, cur, **kw) -> dict:
        entry = {"audit_id": uuid.uuid4().hex[:12], "at": _now().isoformat(), **kw}
        cur.execute(f"INSERT INTO {self._t('fe_state_audit')} (audit_id, at, action, actor, persona, workspace_id, "
                    f"artifact_id, run_id, doc) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)",
                    (entry["audit_id"], entry["at"], entry.get("action"), entry.get("actor"), entry.get("persona"),
                     entry.get("workspace_id"), entry.get("artifact_id"), entry.get("run_id"),
                     json.dumps(entry, default=str)))
        return entry

    def audit(self, *, action: str, actor: str | None = None, persona: str | None = None,
              workspace_id: str | None = None, artifact_id: str | None = None, run_id: str | None = None,
              before: dict | None = None, after: dict | None = None, detail: dict | None = None) -> dict:
        with self._connect() as conn, conn.cursor() as cur:
            return self._audit_in(cur, action=action, actor=actor, persona=persona, workspace_id=workspace_id,
                                  artifact_id=artifact_id, run_id=run_id, before=before, after=after, detail=detail)

    def audit_log(self, *, workspace_id: str | None = None, artifact_id: str | None = None,
                  limit: int = 200) -> list[dict]:
        where, args = [], []
        if workspace_id:
            where.append("workspace_id=%s"); args.append(workspace_id)
        if artifact_id:
            where.append("artifact_id=%s"); args.append(artifact_id)
        clause = (" WHERE " + " AND ".join(where)) if where else ""
        sql = f"SELECT doc FROM {self._t('fe_state_audit')}{clause} ORDER BY at DESC LIMIT %s"
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(sql, (*args, limit))
            return [r["doc"] for r in cur.fetchall()][::-1]

    def approvals_for(self, artifact_id: str) -> list[Approval]:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(f"SELECT doc FROM {self._t('fe_state_approval')} WHERE artifact_id=%s ORDER BY decided_at", (artifact_id,))
            return [Approval.model_validate(r["doc"]) for r in cur.fetchall()]

    # -- outbox -------------------------------------------------------------------
    def enqueue_outbox(self, *, aggregate_id: str, aggregate_version: int, operation: str,
                       idempotency_key: str) -> OutboxEvent:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(f"SELECT doc FROM {self._t('fe_state_outbox')} WHERE idempotency_key=%s", (idempotency_key,))
            row = cur.fetchone()
            if row:
                return OutboxEvent.model_validate(row["doc"])
            event = OutboxEvent(event_id=uuid.uuid4().hex[:12], aggregate_id=aggregate_id,
                                aggregate_version=aggregate_version, operation=operation,
                                idempotency_key=idempotency_key, created_at=_now())
            cur.execute(f"INSERT INTO {self._t('fe_state_outbox')} (event_id, idempotency_key, status, created_at, doc) "
                        f"VALUES (%s,%s,%s,%s,%s::jsonb)",
                        (event.event_id, idempotency_key, event.status.value, event.created_at, _dump(event)))
        return event

    def pending_outbox(self, limit: int = 50) -> list[OutboxEvent]:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(f"SELECT doc FROM {self._t('fe_state_outbox')} WHERE status=%s ORDER BY created_at LIMIT %s",
                        (OutboxStatus.PENDING.value, int(limit)))
            return [OutboxEvent.model_validate(r["doc"]) for r in cur.fetchall()]

    def claim_outbox(self, limit: int = 50) -> list[OutboxEvent]:
        """Pending, due events for this worker only (SKIP LOCKED); marks claimed_at."""
        now = _now()
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                f"SELECT event_id, doc FROM {self._t('fe_state_outbox')} WHERE status=%s "
                f"AND (next_attempt_at IS NULL OR next_attempt_at <= %s) "
                f"AND (claimed_at IS NULL OR claimed_at < %s) "
                f"ORDER BY created_at LIMIT %s FOR UPDATE SKIP LOCKED",
                (OutboxStatus.PENDING.value, now, now - timedelta(minutes=10), int(limit)))
            rows = cur.fetchall()
            ids = [r["event_id"] for r in rows]
            if ids:
                cur.execute(f"UPDATE {self._t('fe_state_outbox')} SET claimed_at=%s WHERE event_id = ANY(%s)", (now, ids))
            return [OutboxEvent.model_validate(r["doc"]) for r in rows]

    def mark_outbox(self, event_id: str, status: OutboxStatus, error: str | None = None) -> None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(f"SELECT doc FROM {self._t('fe_state_outbox')} WHERE event_id=%s FOR UPDATE", (event_id,))
            row = cur.fetchone()
            if not row:
                return
            event = OutboxEvent.model_validate(row["doc"])
            event.status = status
            event.attempts += 1
            event.last_error = error
            cur.execute(f"UPDATE {self._t('fe_state_outbox')} SET status=%s, next_attempt_at=%s, claimed_at=NULL, "
                        f"doc=%s::jsonb WHERE event_id=%s",
                        (status.value, event.next_attempt_at, _dump(event), event_id))

    # -- traceability ---------------------------------------------------------------
    def lineage(self, artifact_id: str) -> dict[str, list[SdlcArtifact]]:
        artifact = self.get_artifact(artifact_id)
        if artifact is None:
            return {"upstream": [], "downstream": []}
        upstream: list[SdlcArtifact] = []
        seen: set[str] = set()
        cursor = artifact
        while cursor and cursor.parent_id and cursor.parent_id not in seen:
            seen.add(cursor.parent_id)
            parent = self.get_artifact(cursor.parent_id)
            if parent is None:
                break
            upstream.append(parent)
            cursor = parent
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(f"SELECT doc FROM {self._t('fe_state_artifact')} WHERE parent_id=%s", (artifact_id,))
            downstream = [SdlcArtifact.model_validate(r["doc"]) for r in cur.fetchall()]
        return {"upstream": upstream, "downstream": downstream}

    # -- ops ------------------------------------------------------------------------
    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        with self._connect() as conn, conn.cursor() as cur:
            for t in ("fe_state_run", "fe_state_workspace", "fe_state_artifact", "fe_state_approval", "fe_state_outbox", "fe_state_audit"):
                cur.execute(f"SELECT count(*) AS n FROM {self._t(t)}")
                out[t] = int(cur.fetchone()["n"])
        return out


def default_worker_id() -> str:
    import os  # noqa: PLC0415
    return f"{socket.gethostname()}:{os.getpid()}"
