"""Persisted store for runs, artefacts, approvals and the outbox.

Why persisted from the first commit (PRD R-11 / FR-015)
------------------------------------------------------
the knowledge base keeps job state in process-local dicts (e.g. `_analyse_jobs` in
`api/routes/fabric/applications.py`), so a backend restart loses in-flight runs.
FR-015 requires "create, status, event stream, cancel, retry, and failure
endpoints operate across API restarts", so nothing here is memory-only.

Why file-backed for now
-----------------------
The target table (`sdlc_artifacts`) belongs in the knowledge base's schema, added through its
raw-DDL migration list. Until that lands, a JSON store keeps the service runnable
without forking the knowledge base's database. Callers use only the methods below, so swapping
in a Postgres implementation is contained.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

from app.agentic_platform.fe_core.kb.models import (
    Approval,
    ApprovalDecision,
    ArtifactStatus,
    OutboxEvent,
    OutboxStatus,
    SdlcArtifact,
)
from app.agentic_platform.fe_core.pipeline.models import StageRun, StageState
from app.agentic_platform.fe_core.workspaces.models import Workspace

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def make_idempotency_key(*parts: str) -> str:
    """FR-023: idempotent by application, run, step, artefact type and checksum."""
    return sha256("\x1f".join(str(p) for p in parts))


def _atomic_write(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp{os.getpid()}")
    tmp.write_text(payload, encoding="utf-8")
    tmp.replace(path)


class ConcurrencyError(RuntimeError):
    """Expected-version mismatch. Surfaced as HTTP 409 (PRD 9.3)."""


class JsonFileStore:
    kind = "json"
    """Single-file JSON store guarded by a process lock."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.path = self.root / "state.json"
        self._lock = threading.RLock()
        self._runs: dict[str, StageRun] = {}
        self._artifacts: dict[str, SdlcArtifact] = {}
        self._approvals: dict[str, Approval] = {}
        self._outbox: dict[str, OutboxEvent] = {}
        self._audit: list[dict] = []          # append-only (fe_audit_log equivalent)
        self._workspaces: dict[str, Workspace] = {}
        self._load()

    # -- persistence ------------------------------------------------------
    def _load(self) -> None:
        if not self.path.is_file():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.error("Could not read %s (%s); starting empty", self.path, exc)
            return
        for raw in data.get("runs", []):
            try:
                run = StageRun.model_validate(raw)
                self._runs[run.run_id] = run
            except Exception as exc:  # noqa: BLE001
                logger.warning("Skipping unreadable run row: %s", exc)
        for raw in data.get("artifacts", []):
            try:
                art = SdlcArtifact.model_validate(raw)
                self._artifacts[art.id] = art
            except Exception as exc:  # noqa: BLE001
                logger.warning("Skipping unreadable artifact row: %s", exc)
        for raw in data.get("approvals", []):
            try:
                ap = Approval.model_validate(raw)
                self._approvals[ap.approval_id] = ap
            except Exception as exc:  # noqa: BLE001
                logger.warning("Skipping unreadable approval row: %s", exc)
        for raw in data.get("outbox", []):
            try:
                ev = OutboxEvent.model_validate(raw)
                self._outbox[ev.event_id] = ev
            except Exception as exc:  # noqa: BLE001
                logger.warning("Skipping unreadable outbox row: %s", exc)
        self._audit = [e for e in data.get("audit", []) if isinstance(e, dict)]
        for raw in data.get("workspaces", []):
            try:
                ws = Workspace.model_validate(raw)
                self._workspaces[ws.id] = ws
            except Exception as exc:  # noqa: BLE001
                logger.warning("Skipping unreadable workspace row: %s", exc)
        logger.info(
            "Loaded %d run(s), %d artefact(s), %d approval(s), %d workspace(s) from %s",
            len(self._runs), len(self._artifacts), len(self._approvals),
            len(self._workspaces), self.path,
        )

    def _flush(self) -> None:
        payload = {
            # v3 adds `workspaces` and workspace-scoped artefacts. A v2 file
            # still loads: its artefacts have workspace_id=None, which reads as
            # the single-tier pipeline they were written by.
            "version": 3,
            "saved_at": _now().isoformat(),
            "runs": [r.model_dump(mode="json") for r in self._runs.values()],
            "artifacts": [a.model_dump(mode="json") for a in self._artifacts.values()],
            "approvals": [a.model_dump(mode="json") for a in self._approvals.values()],
            "outbox": [e.model_dump(mode="json") for e in self._outbox.values()],
            "audit": list(self._audit[-5000:]),
            "workspaces": [w.model_dump(mode="json") for w in self._workspaces.values()],
        }
        _atomic_write(self.path, json.dumps(payload, indent=2))

    # -- runs -------------------------------------------------------------
    def create_run(self, run: StageRun) -> StageRun:
        with self._lock:
            if run.run_id in self._runs:
                raise ValueError(f"run already exists: {run.run_id}")
            self._runs[run.run_id] = run
            self._flush()
            return run

    def save_run(self, run: StageRun) -> StageRun:
        with self._lock:
            self._runs[run.run_id] = run
            self._flush()
            return run

    def get_run(self, run_id: str) -> StageRun | None:
        return self._runs.get(run_id)

    def find_run_by_idempotency_key(self, key: str) -> StageRun | None:
        """FR-023: a retry with the same key returns the existing run."""
        for run in self._runs.values():
            if run.idempotency_key == key:
                return run
        return None

    def list_runs(self, pipeline: str | None = None,
                  stage_key: str | None = None, *,
                  workspace_id: str | None = None) -> list[StageRun]:
        runs = list(self._runs.values())
        if pipeline:
            runs = [r for r in runs if r.pipeline == pipeline]
        if stage_key:
            runs = [r for r in runs if r.stage_key == stage_key]
        if workspace_id is not None:
            runs = [r for r in runs if r.workspace_id == workspace_id]
        return sorted(
            runs,
            key=lambda r: r.queued_at or r.started_at or datetime.min.replace(
                tzinfo=timezone.utc),
            reverse=True,
        )

    def latest_run(self, pipeline: str, stage_key: str, *,
                   workspace_id: str | None = None) -> StageRun | None:
        candidates = self.list_runs(pipeline, stage_key, workspace_id=workspace_id)
        return candidates[0] if candidates else None

    def next_attempt(self, pipeline: str, stage_key: str, *,
                     workspace_id: str | None = None) -> int:
        """Attempt numbering is per workspace: EPIC 5's first run of `ui-code` is
        attempt 1, not attempt 5 because four other EPICs went first."""
        return 1 + len(self.list_runs(pipeline, stage_key,
                                      workspace_id=workspace_id))

    # -- workspaces (PRD 5.3, FR-P4) --------------------------------------
    def put_workspace(self, workspace: "Workspace") -> "Workspace":
        """Insert or update. Ids are deterministic, so this is idempotent and a
        concurrent create of the same workspace cannot produce two rows."""
        with self._lock:
            self._workspaces[workspace.id] = workspace
            self._flush()
            return workspace

    def get_workspace(self, workspace_id: str) -> "Workspace | None":
        return self._workspaces.get(workspace_id)

    def list_workspaces(
        self, kb_application_id: str | None = None, *,
        tier: str | None = None, status: str | None = None,
    ) -> list["Workspace"]:
        rows = list(self._workspaces.values())
        if kb_application_id:
            rows = [w for w in rows if w.kb_application_id == kb_application_id]
        if tier:
            rows = [w for w in rows if w.tier.value == tier]
        if status:
            rows = [w for w in rows if w.status.value == status]
        return sorted(rows, key=lambda w: (w.tier.value != "global", w.id))

    def find_workspace_for_epic(self, kb_application_id: str,
                                epic_id: str) -> "Workspace | None":
        for workspace in self._workspaces.values():
            if (workspace.kb_application_id == kb_application_id
                    and workspace.epic_id == epic_id):
                return workspace
        return None

    def delete_project(self, kb_application_id: str) -> int:
        """Remove all workspaces (and their runs, artefacts, approvals) for a project.

        Returns the number of workspaces deleted. Does NOT touch the KB catalogue —
        deleting here only removes this service's local state so the project can be
        re-onboarded cleanly.
        """
        with self._lock:
            workspace_ids = {
                ws_id for ws_id, ws in self._workspaces.items()
                if ws.kb_application_id == kb_application_id
            }
            if not workspace_ids:
                return 0

            for ws_id in workspace_ids:
                del self._workspaces[ws_id]

            # Cascade: runs scoped to these workspaces.
            run_ids_to_delete = {
                run_id for run_id, run in self._runs.items()
                if run.workspace_id in workspace_ids
            }
            for run_id in run_ids_to_delete:
                del self._runs[run_id]

            # Cascade: artefacts, then their approvals.
            artifact_ids_to_delete = {
                art_id for art_id, art in self._artifacts.items()
                if art.workspace_id in workspace_ids
            }
            for art_id in artifact_ids_to_delete:
                del self._artifacts[art_id]

            approval_ids_to_delete = {
                ap_id for ap_id, ap in self._approvals.items()
                if ap.artifact_id in artifact_ids_to_delete
            }
            for ap_id in approval_ids_to_delete:
                del self._approvals[ap_id]

            # Cascade: audit rows for these workspaces and outbox events for these artefacts.
            audit = getattr(self, "_audit", None)
            if isinstance(audit, list):
                self._audit = [a for a in audit if a.get("workspace_id") not in workspace_ids]
            outbox = getattr(self, "_outbox", None)
            if isinstance(outbox, dict):
                for eid in [eid for eid, ev in outbox.items()
                            if getattr(ev, "aggregate_id", None) in artifact_ids_to_delete]:
                    del outbox[eid]

            self._flush()
            logger.info(
                "Deleted project '%s': %d workspace(s), %d run(s), "
                "%d artefact(s), %d approval(s)",
                kb_application_id,
                len(workspace_ids),
                len(run_ids_to_delete),
                len(artifact_ids_to_delete),
                len(approval_ids_to_delete),
            )
            return len(workspace_ids)

    # -- artefacts --------------------------------------------------------
    def put_artifact(self, artifact: SdlcArtifact) -> SdlcArtifact:
        with self._lock:
            self._artifacts[artifact.id] = artifact
            self._flush()
            return artifact

    def get_artifact(self, artifact_id: str) -> SdlcArtifact | None:
        return self._artifacts.get(artifact_id)

    def list_artifacts(
        self,
        pipeline: str | None = None,
        stage_key: str | None = None,
        artifact_type: str | None = None,
        status: ArtifactStatus | None = None,
        *,
        workspace_id: str | None = None,
        epic_id: str | None = None,
        tier: str | None = None,
        _unscoped: bool = False,
    ) -> list[SdlcArtifact]:
        """Filtered artefact list.

        `workspace_id=None` means "do not filter", so reporting views still see
        everything. Pass `_unscoped=True` only to be explicit that no workspace
        filter is intended -- it reads as a decision at the call site rather than
        an omission.
        """
        rows = list(self._artifacts.values())
        if pipeline:
            rows = [a for a in rows if a.pipeline == pipeline]
        if stage_key:
            rows = [a for a in rows if a.stage_key == stage_key]
        if artifact_type:
            rows = [a for a in rows if a.artifact_type == artifact_type]
        if status:
            rows = [a for a in rows if a.status is status]
        if workspace_id is not None:
            rows = [a for a in rows if a.workspace_id == workspace_id]
        if epic_id is not None:
            rows = [a for a in rows if a.epic_id == epic_id]
        if tier is not None:
            rows = [a for a in rows if a.tier == tier]
        return sorted(rows, key=lambda a: (a.stage_key, a.artifact_type, a.version))

    def find_artifact_by_checksum(
        self, pipeline: str, stage_key: str, artifact_type: str, checksum: str,
        *, workspace_id: str | None,
    ) -> SdlcArtifact | None:
        """FR-023: identical content must not create a duplicate version.

        `workspace_id` is keyword-only and has no default on purpose: two Mini
        Workspaces generating the same artefact type from the same approved
        upstream produce the *same* checksum, so an unscoped lookup would hand
        EPIC 2 a reference to EPIC 1's artefact and silently merge two EPICs.
        """
        for art in self._artifacts.values():
            if (art.pipeline == pipeline and art.stage_key == stage_key
                    and art.artifact_type == artifact_type
                    and art.workspace_id == workspace_id
                    and (art.checksum or art.content_sha256) == checksum):
                return art
        return None

    def next_version(self, pipeline: str, stage_key: str, artifact_type: str,
                     *, workspace_id: str | None) -> int:
        """Version numbering is per workspace (FR-P5)."""
        existing = self.list_artifacts(pipeline, stage_key, artifact_type,
                                       workspace_id=workspace_id)
        return 1 + max((a.version for a in existing), default=0)

    def supersede_previous(
        self, pipeline: str, stage_key: str, artifact_type: str,
        *, workspace_id: str | None,
    ) -> str | None:
        """Mark this workspace's current head SUPERSEDED and return its id.

        FR-026: regeneration never edits a previous version. Scoped by workspace
        so regenerating EPIC 3's UI cannot supersede EPIC 4's.
        """
        with self._lock:
            heads = [
                a for a in self._artifacts.values()
                if a.pipeline == pipeline and a.stage_key == stage_key
                and a.artifact_type == artifact_type
                and a.workspace_id == workspace_id
                and a.status is not ArtifactStatus.SUPERSEDED
            ]
            if not heads:
                return None
            head = max(heads, key=lambda a: a.version)
            head.status = ArtifactStatus.SUPERSEDED
            self._artifacts[head.id] = head
            self._flush()
            return head.id

    def approved_artifacts(
        self, pipeline: str, artifact_types: Iterable[str],
        *, workspace_ids: Iterable[str] | None = None,
    ) -> list[SdlcArtifact]:
        """FR-025: Draft, Rejected and Superseded are never generation inputs.

        `workspace_ids` is a *set*, not a single id, because a Mini Workspace
        legitimately consumes its parent Global Workspace's approved artefacts --
        M1 Feature needs the approved EPIC. Passing None means "any workspace",
        which is correct only for the single-tier case and for reporting.
        """
        wanted = set(artifact_types)
        allowed = set(workspace_ids) if workspace_ids is not None else None
        return [
            a for a in self._artifacts.values()
            if a.pipeline == pipeline and a.artifact_type in wanted
            and a.status is ArtifactStatus.APPROVED
            and (allowed is None or a.workspace_id in allowed)
        ]

    # -- approvals --------------------------------------------------------
    def record_approval(
        self,
        artifact: SdlcArtifact,
        *,
        decision: ApprovalDecision,
        decided_by: str,
        role: str | None = None,
        comment: str | None = None,
        expected_version: int | None = None,
    ) -> Approval:
        """Append an approval and update artefact status.

        PRD 9.3: the request carries an expected artefact version; a mismatch is
        a conflict and the reviewer must reload. Approval changes status only --
        never content.
        """
        with self._lock:
            if expected_version is not None and expected_version != artifact.version:
                raise ConcurrencyError(
                    f"artifact {artifact.id} is at version {artifact.version}, "
                    f"not {expected_version}; reload before approving"
                )
            if artifact.status is ArtifactStatus.SUPERSEDED:
                raise ConcurrencyError(
                    f"artifact {artifact.id} v{artifact.version} is superseded and "
                    "cannot be approved"
                )

            approval = Approval(
                approval_id=uuid.uuid4().hex[:12],
                artifact_id=artifact.id,
                artifact_version=artifact.version,
                decision=decision,
                role=role,
                decided_by=decided_by,
                decided_at=_now(),
                comment=comment,
            )
            self._approvals[approval.approval_id] = approval
            before_status = artifact.status.value

            artifact.status = (
                ArtifactStatus.APPROVED if decision is ApprovalDecision.APPROVE
                else ArtifactStatus.REJECTED
            )
            artifact.approved_by = decided_by
            self._audit.append({"audit_id": uuid.uuid4().hex[:12], "at": _now().isoformat(),
                                "action": f"artifact.{decision.value}", "actor": decided_by, "persona": role,
                                "workspace_id": artifact.workspace_id, "artifact_id": artifact.id,
                                "run_id": artifact.run_id, "before": {"status": before_status},
                                "after": {"status": artifact.status.value}, "detail": {"comment": comment}})
            artifact.approved_at = approval.decided_at
            artifact.approval_feedback = comment
            self._artifacts[artifact.id] = artifact
            self._flush()
            return approval

    def approvals_for(self, artifact_id: str) -> list[Approval]:
        return sorted(
            (a for a in self._approvals.values() if a.artifact_id == artifact_id),
            key=lambda a: a.decided_at,
        )

    # -- outbox (FR-029) --------------------------------------------------
    def enqueue_outbox(
        self, *, aggregate_id: str, aggregate_version: int, operation: str,
        idempotency_key: str,
    ) -> OutboxEvent:
        with self._lock:
            for existing in self._outbox.values():
                if existing.idempotency_key == idempotency_key:
                    return existing
            event = OutboxEvent(
                event_id=uuid.uuid4().hex[:12],
                aggregate_id=aggregate_id,
                aggregate_version=aggregate_version,
                operation=operation,
                idempotency_key=idempotency_key,
                created_at=_now(),
            )
            self._outbox[event.event_id] = event
            self._flush()
            return event

    def pending_outbox(self, limit: int = 50) -> list[OutboxEvent]:
        rows = [e for e in self._outbox.values() if e.status is OutboxStatus.PENDING]
        return sorted(rows, key=lambda e: e.created_at or _now())[:limit]

    def mark_outbox(
        self, event_id: str, status: OutboxStatus, error: str | None = None
    ) -> None:
        with self._lock:
            event = self._outbox.get(event_id)
            if event is None:
                return
            event.status = status
            event.attempts += 1
            event.last_error = error
            self._outbox[event_id] = event
            self._flush()

    # -- worker queue primitives (FE_EXECUTION=worker) --------------------
    # In-memory equivalents of PostgresStore's SKIP LOCKED claims so callers
    # (worker loop, tests) are store-agnostic. Single-process only, by design.
    def claim_queued_runs(self, worker_id: str, limit: int = 1,
                          workspace_filter: str | None = None,
                          runner_affinity: str | None = None) -> list[StageRun]:
        prefix = workspace_filter.rstrip("%") if workspace_filter else None
        with self._lock:
            queued = sorted(
                (r for r in self._runs.values()
                 if r.state is StageState.QUEUED and not r.claimed_by
                 and (prefix is None or (r.workspace_id or "").startswith(prefix))
                 and (runner_affinity is None or not r.required_runner
                      or r.required_runner == runner_affinity)),
                key=lambda r: (r.queued_at or _now(), r.run_id),
            )[:limit]
            now = _now()
            for run in queued:
                run.claimed_by = worker_id
                run.claimed_at = now
                run.heartbeat_at = now
            if queued:
                self._flush()
            return queued

    def heartbeat(self, run_id: str) -> None:
        with self._lock:
            run = self._runs.get(run_id)
            if run is not None:
                run.heartbeat_at = _now()
                self._flush()

    def requeue_stale(self, older_than_seconds: int = 120) -> list[str]:
        cutoff = _now() - timedelta(seconds=older_than_seconds)
        out: list[str] = []
        with self._lock:
            for run in self._runs.values():
                if run.state is StageState.RUNNING and (run.heartbeat_at is None or run.heartbeat_at < cutoff):
                    run.state = StageState.QUEUED
                    run.claimed_by = None
                    run.claimed_at = None
                    run.log.append(f"requeued: worker heartbeat stale (> {older_than_seconds}s)")
                    out.append(run.run_id)
            if out:
                self._flush()
        return out

    def claim_outbox(self, limit: int = 50) -> list[OutboxEvent]:
        now = _now()
        return [e for e in self.pending_outbox(limit)
                if e.next_attempt_at is None or e.next_attempt_at <= now]

    def counts(self) -> dict[str, int]:
        return {"runs": len(self._runs), "workspaces": len(self._workspaces),
                "artifacts": len(self._artifacts), "approvals": len(self._approvals),
                "outbox": len(self._outbox), "audit": len(self._audit)}

    # -- audit (append-only; mirrors the AIDLC fe_audit_log) --------------------
    def audit(self, *, action: str, actor: str | None = None, persona: str | None = None,
              workspace_id: str | None = None, artifact_id: str | None = None,
              run_id: str | None = None, before: dict | None = None, after: dict | None = None,
              detail: dict | None = None) -> dict:
        entry = {"audit_id": uuid.uuid4().hex[:12], "at": _now().isoformat(), "action": action,
                 "actor": actor, "persona": persona, "workspace_id": workspace_id,
                 "artifact_id": artifact_id, "run_id": run_id, "before": before, "after": after,
                 "detail": detail}
        with self._lock:
            self._audit.append(entry)
            self._flush()
        return entry

    def audit_log(self, *, workspace_id: str | None = None, artifact_id: str | None = None,
                  limit: int = 200) -> list[dict]:
        rows = [e for e in self._audit
                if (workspace_id is None or e.get("workspace_id") == workspace_id)
                and (artifact_id is None or e.get("artifact_id") == artifact_id)]
        return rows[-limit:]

    # -- traceability (FR-027) --------------------------------------------
    def lineage(self, artifact_id: str) -> dict[str, list[SdlcArtifact]]:
        """Resolve upstream and downstream links through parent_id."""
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

        downstream = [
            a for a in self._artifacts.values() if a.parent_id == artifact_id
        ]
        return {"upstream": upstream, "downstream": downstream}


#: Either backend; both expose the same methods. Type hints use this alias.
Store = JsonFileStore  # structural: PostgresStore mirrors every public method

_store = None


def _fe_db_url_from_vault() -> str:
    """Build a Postgres URL from HashiCorp Vault when FE_DB_URL is not set explicitly.

    Keeps DB credentials out of committed config: Vault (``PG_VAULT_NAME`` under
    ``PG_DB_VAULT_APP_CODE``) supplies user/password; host/port/db come from the agent
    settings. Returns "" when Vault/host is not configured (caller falls back).
    """
    import httpx  # noqa: PLC0415
    from psycopg.conninfo import make_conninfo  # noqa: PLC0415

    from app.config import get_settings as _agent_settings  # noqa: PLC0415
    from app.utils.hashicorp_client import VaultClient  # noqa: PLC0415

    a = _agent_settings()
    if a.pg_local:
        # PG_CONNECTION_MODE=local (app/.env): same URL for the control-plane store, no Vault round-trip.
        return a.PG_LOCAL_URL
    if not (a.HASHICORP_API_URL and a.PG_VAULT_NAME and a.PGHOST):
        return ""
    payload = {
        "region": a.HASHICORP_VAULT_REGION or a.ENV,
        "app_code": getattr(a, "PG_DB_VAULT_APP_CODE", "") or a.VAULT_APP_CODE,
        "vault_name": a.PG_VAULT_NAME,
    }
    headers = {"Content-Type": "application/json"}
    token = getattr(a, "HASHICORP_AUTH_TOKEN", "") or ""
    if token:
        headers["Authorization"] = token if token.startswith("Bearer ") else f"Bearer {token}"
    resp = httpx.post(a.HASHICORP_API_URL, json=payload, headers=headers, timeout=a.HASHICORP_API_TIMEOUT, verify=False)
    resp.raise_for_status()
    creds = VaultClient._parse_vault_response(resp.json(), a.PG_VAULT_NAME)
    # Build a libpq keyword conninfo (not a URL). Credentials are set as dict items
    # (mirrors app/dao/postgres) and quoted by make_conninfo.
    parts: dict[str, str] = {"host": a.PGHOST, "port": str(a.PGPORT), "dbname": a.PGDATABASE}
    if creds.username:
        parts["user"] = creds.username
    if creds.password:
        parts["password"] = creds.password
    return make_conninfo(**parts)


def get_store():
    """FE_STORE=json (default) -> JsonFileStore under <workspace_root>/_state
       FE_STORE=postgres      -> PostgresStore on FE_DB_URL, or Vault-built creds when
                                 FE_DB_URL is unset (HashiCorp pulls the DB credentials)."""
    global _store
    if _store is None:
        from app.agentic_platform.fe_core.config import get_settings

        settings = get_settings()
        if getattr(settings, "fe_store", "json") == "postgres":
            from app.agentic_platform.fe_core.store_pg import PostgresStore  # noqa: PLC0415

            url = settings.fe_db_url or _fe_db_url_from_vault()
            # Surface the Vault-built URL on settings. Callers gate on
            # settings.fe_db_url: uses_database() and, crucially, the RAG chunk/embed
            # path in the document-upload endpoint (which also builds ChunkStore from
            # settings.fe_db_url). Without this, an upload stores the file but silently
            # skips chunking/embedding, so the document is never searchable.
            if not settings.fe_db_url:
                try:
                    settings.fe_db_url = url
                except Exception:  # noqa: BLE001 - settings may be frozen; the store still works
                    logger.debug("could not surface Vault-built FE_DB_URL onto settings")
            _store = PostgresStore(url, settings.fe_db_schema)
        else:
            # F8: warn operators that json store is not durable across container restarts.
            logger.warning(
                "FE_STORE=json: state is not durable across container restarts. "
                "Set FE_STORE=postgres with FE_DB_URL for production deployments."
            )
            _store = JsonFileStore(settings.fe_workspace_root / "_state")
    return _store


def reset_store() -> None:
    """Test hook."""
    global _store
    _store = None


def new_run(
    pipeline: str,
    stage_key: str,
    kb_application_id: str,
    *,
    attempt: int = 1,
    idempotency_key: str | None = None,
    initiated_by: str | None = None,
    required_runner: str | None = None,
) -> StageRun:
    run_id = uuid.uuid4().hex[:12]
    return StageRun(
        run_id=run_id,
        pipeline=pipeline,
        stage_key=stage_key,
        kb_application_id=kb_application_id,
        state=StageState.QUEUED,
        attempt=attempt,
        idempotency_key=idempotency_key,
        correlation_id=run_id,
        initiated_by=initiated_by,
        queued_at=_now(),
        required_runner=required_runner,
    )
