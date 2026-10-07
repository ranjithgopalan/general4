"""
ApprovalService — human approval state machine for HITL artifact review.

State transitions:
    PENDING → APPROVED | REJECTED | REVISION_REQUIRED | EXPIRED (via TTL job)

Artifacts move to PENDING when an agent emits HUMAN_APPROVAL_PENDING via TelemetryService.
Reviewers submit decisions via the /observability/approvals/{id}/decide API.
Each transition emits a HUMAN_APPROVAL_RESOLVED telemetry event.

The service interacts with form_rationalization_anh.fe_approvals (created in migration 0013).
All DB operations are async and fire-and-forget-safe — errors are logged, never re-raised.

Security:
  - reviewer_id is the authenticated userid (from ContextVar / JWT claim), never email or full name
  - revision_notes are stored but never echoed in log output (may contain business content)
  - No DML writes use raw string concatenation — all values are parameterized
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.services.telemetry import EventType, get_telemetry_service
from app.utils.logging import log
from app.utils.request_context import get_correlation_id, get_userid, get_workflow_run_id


# Valid status values — enforced in submit_decision
_VALID_DECISIONS = {"APPROVED", "REJECTED", "REVISION_REQUIRED"}


@dataclass
class ApprovalRecord:
    id: int
    agent_run_id: str
    artifact_id: str
    artifact_type: str
    status: str
    workspace_id: str
    submitted_at: datetime
    resolved_at: datetime | None
    ttl_expires_at: datetime | None
    reviewer_id: str | None
    decision: str | None
    revision_notes: str | None


class ApprovalService:
    """Manage the human approval lifecycle for HITL artifact review."""

    def __init__(self, pool: Any, schema: str) -> None:
        self._pool = pool
        self._schema = schema

    # ── Submit artifact for review ────────────────────────────────────────────

    async def submit_for_review(
        self,
        *,
        agent_run_id: str,
        artifact_id: str,
        artifact_type: str,
        workspace_id: str = "",
        rule_ids: list[str] | None = None,
        ttl_seconds: int = 86400,
    ) -> int | None:
        """Insert a PENDING approval record. Returns the new row id, or None on DB error."""
        ttl_ts = datetime.now(timezone.utc).timestamp() + ttl_seconds
        ttl_dt = datetime.fromtimestamp(ttl_ts, tz=timezone.utc)

        sql = f"""
            INSERT INTO {self._schema}.fe_approvals
                (agent_run_id, workflow_run_id, correlation_id, workspace_id,
                 artifact_id, artifact_type, status, ttl_expires_at)
            VALUES (%s, %s, %s, %s, %s, %s, 'PENDING', %s)
            RETURNING id
        """
        try:
            async with self._pool.connection() as conn:
                row = await conn.fetchrow(sql, (
                    agent_run_id,
                    get_workflow_run_id() or None,
                    get_correlation_id() or None,
                    workspace_id or None,
                    artifact_id,
                    artifact_type,
                    ttl_dt,
                ))
                approval_id = row["id"] if row else None
        except Exception as exc:
            log.warning(f"[approval] submit_for_review failed (non-fatal): {exc}")
            approval_id = None

        # Emit telemetry regardless of DB success
        get_telemetry_service().emit_approval_pending(
            artifact_id=artifact_id,
            artifact_type=artifact_type,
            workspace_id=workspace_id,
            rule_ids=rule_ids or [],
            ttl_seconds=ttl_seconds,
        )
        return approval_id

    # ── Submit reviewer decision ──────────────────────────────────────────────

    async def submit_decision(
        self,
        *,
        approval_id: int,
        decision: str,
        revision_notes: str = "",
    ) -> bool:
        """Transition an approval record from PENDING to the given decision.

        Returns True on success, False if the record is not found or already resolved.
        """
        if decision not in _VALID_DECISIONS:
            log.warning(f"[approval] invalid decision={decision!r}; must be one of {_VALID_DECISIONS}")
            return False

        reviewer_id = get_userid() or "unknown"
        t0 = time.perf_counter()

        sql_fetch = f"""
            SELECT id, artifact_id, artifact_type, workspace_id, agent_run_id, status
            FROM {self._schema}.fe_approvals
            WHERE id = %s
        """
        sql_update = f"""
            UPDATE {self._schema}.fe_approvals
            SET status = %s, reviewer_id = %s, decision = %s, revision_notes = %s, resolved_at = NOW()
            WHERE id = %s AND status = 'PENDING'
        """
        artifact_id = ""
        artifact_type = ""
        workspace_id = ""
        agent_run_id = ""
        updated = False

        try:
            async with self._pool.connection() as conn:
                row = await conn.fetchrow(sql_fetch, (approval_id,))
                if not row:
                    log.warning(f"[approval] submit_decision: approval_id={approval_id} not found")
                    return False
                if row["status"] != "PENDING":
                    log.warning(f"[approval] submit_decision: approval_id={approval_id} already {row['status']}")
                    return False

                artifact_id = row["artifact_id"]
                artifact_type = row["artifact_type"]
                workspace_id = row["workspace_id"] or ""
                agent_run_id = row["agent_run_id"]

                result = await conn.execute(sql_update, (
                    decision, reviewer_id, decision,
                    revision_notes if revision_notes else None,
                    approval_id,
                ))
                updated = result and "UPDATE 1" in str(result)
        except Exception as exc:
            log.warning(f"[approval] submit_decision failed (non-fatal): {exc}")
            return False

        if updated:
            latency_ms = round((time.perf_counter() - t0) * 1000.0, 1)
            get_telemetry_service().emit_approval_resolved(
                artifact_id=artifact_id,
                artifact_type=artifact_type,
                decision=decision,
                reviewer_id=reviewer_id,
                workspace_id=workspace_id,
                latency_ms=latency_ms,
            )

        return updated

    # ── Queries ───────────────────────────────────────────────────────────────

    async def list_pending(
        self,
        *,
        workspace_id: str = "",
        reviewer_id: str = "",
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict]:
        """Return pending approval records, optionally scoped to a workspace or reviewer."""
        filters = ["status = 'PENDING'"]
        params: list[Any] = []
        if workspace_id:
            filters.append(f"workspace_id = %s")
            params.append(workspace_id)
        if reviewer_id:
            filters.append(f"reviewer_id = %s")
            params.append(reviewer_id)

        where = " AND ".join(filters)
        sql = f"""
            SELECT id, agent_run_id, artifact_id, artifact_type, workspace_id,
                   status, submitted_at, ttl_expires_at, reviewer_id, decision
            FROM {self._schema}.fe_approvals
            WHERE {where}
            ORDER BY submitted_at DESC
            LIMIT %s OFFSET %s
        """
        params.extend([limit, offset])
        try:
            async with self._pool.connection() as conn:
                rows = await conn.fetch(sql, params)
                return [dict(r) for r in rows]
        except Exception as exc:
            log.warning(f"[approval] list_pending failed (non-fatal): {exc}")
            return []

    async def expire_overdue(self) -> int:
        """Mark PENDING approvals past their TTL as EXPIRED. Returns count updated."""
        sql = f"""
            UPDATE {self._schema}.fe_approvals
            SET status = 'EXPIRED', resolved_at = NOW()
            WHERE status = 'PENDING' AND ttl_expires_at < NOW()
        """
        try:
            async with self._pool.connection() as conn:
                result = await conn.execute(sql, ())
                count = int(result.split()[-1]) if result else 0
                if count:
                    log.info(f"[approval] expired {count} overdue approval(s)")
                return count
        except Exception as exc:
            log.warning(f"[approval] expire_overdue failed (non-fatal): {exc}")
            return 0
