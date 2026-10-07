"""
Observability REST API — agent execution telemetry, evaluation results,
human approval queue, rule traceability, and performance metrics.

All endpoints require an authenticated request (API key middleware applies).
Workspace-scoped reads accept an optional workspace_id filter so callers can
restrict results to a single workspace.

Route prefix: /observability  (registered in main.py)

Endpoints:
  GET  /observability/agents                  — agent run list
  GET  /observability/agents/{agent_run_id}   — single agent run detail
  GET  /observability/evaluations             — evaluation results
  GET  /observability/approvals               — approval queue
  POST /observability/approvals/{id}/decide   — submit approval decision
  GET  /observability/traceability/rule/{rule_id}       — rule traceability chain
  GET  /observability/traceability/workspace/{ws_id}    — workspace traceability summary
  GET  /observability/metrics/cost            — LLM cost aggregation
  GET  /observability/metrics/performance     — latency/throughput trends
  GET  /observability/health                  — telemetry pipeline health

DB access uses the shared psycopg3 helpers in app.dao.postgres (fetch_all /
fetch_one / execute) — NOT asyncpg-style conn.fetch/fetchrow/fetchval, which
this project's psycopg3 pool does not provide.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.config import get_settings
from app.dao.postgres import fetch_all, fetch_one
from app.utils.logging import log

router = APIRouter(prefix="/observability", tags=["observability"])


# ── Request/Response models ───────────────────────────────────────────────────

class ApprovalDecision(BaseModel):
    decision: str          # APPROVED | REJECTED | REVISION_REQUIRED
    revision_notes: str = ""


class ApprovalDecisionResponse(BaseModel):
    approval_id: int
    decision: str
    accepted: bool


# ── Helpers ───────────────────────────────────────────────────────────────────

def _schema() -> str:
    return get_settings().PG_SCHEMA


# ── Agent runs ────────────────────────────────────────────────────────────────

@router.get("/agents")
async def list_agent_runs(
    workspace_id: str = Query("", description="Filter by workspace"),
    stage: str = Query("", description="Filter by pipeline stage"),
    status: str = Query("", description="Filter by status (success|error)"),
    module_id: str = Query("", description="Filter by business module"),
    limit: int = Query(50, le=200),
    offset: int = Query(0, ge=0),
) -> dict:
    """Return agent run records from fe_telemetry (AGENT_RUN events)."""
    schema = _schema()
    filters = ["event_type = 'agent_run'"]
    params: list[Any] = []

    if workspace_id:
        filters.append("workspace_id = %s")
        params.append(workspace_id)
    if stage:
        filters.append("stage = %s")
        params.append(stage)
    if module_id:
        filters.append("module_id = %s")
        params.append(module_id)
    if status:
        filters.append("(metadata->>'status') = %s")
        params.append(status)

    where = " AND ".join(filters)
    sql = f"""
        SELECT id, agent_run_id, workflow_run_id, correlation_id, stage, workspace_id,
               latency_ms, tokens_in, tokens_out, retry_count, module_id, artifact_id,
               (metadata->>'status') AS status,
               (metadata->>'agent_name') AS agent_name,
               (metadata->>'iterations')::int AS iterations,
               created_at
        FROM {schema}.fe_telemetry
        WHERE {where}
        ORDER BY created_at DESC
        LIMIT %s OFFSET %s
    """
    params.extend([limit, offset])

    try:
        rows = await fetch_all(sql, params)
        return {"items": rows, "limit": limit, "offset": offset}
    except Exception as exc:
        log.warning(f"[observability] list_agent_runs failed: {exc}")
        raise HTTPException(status_code=500, detail="Telemetry query failed")


@router.get("/agents/{agent_run_id}")
async def get_agent_run(agent_run_id: str) -> dict:
    """Return all telemetry events for a single agent run (full lifecycle trace)."""
    schema = _schema()
    sql = f"""
        SELECT id, event_type, agent_run_id, workflow_run_id, correlation_id, stage,
               workspace_id, latency_ms, tokens_in, tokens_out, retry_count,
               module_id, feature_id, rule_id, artifact_id,
               prompt_version, agent_version, validation_status, evaluation_status,
               human_approval_status, metadata, created_at
        FROM {schema}.fe_telemetry
        WHERE agent_run_id = %s
        ORDER BY created_at ASC
    """
    try:
        rows = await fetch_all(sql, (agent_run_id,))
    except Exception as exc:
        log.warning(f"[observability] get_agent_run failed: {exc}")
        raise HTTPException(status_code=500, detail="Telemetry query failed")

    if not rows:
        raise HTTPException(status_code=404, detail=f"agent_run_id {agent_run_id!r} not found")
    return {"agent_run_id": agent_run_id, "events": rows}


# ── Evaluations ───────────────────────────────────────────────────────────────

@router.get("/evaluations")
async def list_evaluations(
    workspace_id: str = Query(""),
    dimension: str = Query(""),
    passed: bool | None = Query(None),
    limit: int = Query(50, le=200),
    offset: int = Query(0, ge=0),
) -> dict:
    """Return evaluation results from fe_evaluations."""
    schema = _schema()
    filters: list[str] = []
    params: list[Any] = []

    if workspace_id:
        filters.append("workspace_id = %s")
        params.append(workspace_id)
    if dimension:
        filters.append("dimension = %s")
        params.append(dimension)
    if passed is not None:
        filters.append("passed_threshold = %s")
        params.append(passed)

    where = ("WHERE " + " AND ".join(filters)) if filters else ""
    sql = f"""
        SELECT id, agent_run_id, artifact_id, rule_id, module_id,
               evaluator_type, dimension, score, passed_threshold, threshold_value,
               rationale, evaluated_at
        FROM {schema}.fe_evaluations
        {where}
        ORDER BY evaluated_at DESC
        LIMIT %s OFFSET %s
    """
    params.extend([limit, offset])

    try:
        rows = await fetch_all(sql, params)
        return {"items": rows, "limit": limit, "offset": offset}
    except Exception as exc:
        log.warning(f"[observability] list_evaluations failed: {exc}")
        raise HTTPException(status_code=500, detail="Evaluation query failed")


# ── Approvals ─────────────────────────────────────────────────────────────────

@router.get("/approvals")
async def list_approvals(
    workspace_id: str = Query(""),
    status: str = Query("PENDING"),
    limit: int = Query(50, le=200),
    offset: int = Query(0, ge=0),
) -> dict:
    """Return the approval queue from the authoritative UWCR store (GAP-008).

    Single source of truth is the UWCR approval flow (fe_state_artifact +
    fe_state_approval), not the retired fe_approvals table. Status mapping:
      PENDING  -> artifacts awaiting review (fe_state_artifact.status = IN_REVIEW)
      APPROVED -> fe_state_approval decision 'approve'
      REJECTED -> fe_state_approval decision 'reject'
      REVISION_REQUIRED / EXPIRED -> no UWCR equivalent -> empty.
    """
    schema = _schema()
    status_up = (status or "PENDING").upper()
    params: list[Any] = []

    if status_up == "PENDING":
        filters = ["status = 'IN_REVIEW'"]
        if workspace_id:
            filters.append("workspace_id = %s")
            params.append(workspace_id)
        sql = f"""
            SELECT id AS artifact_id, artifact_type, workspace_id, version,
                   'PENDING' AS status, updated_at AS submitted_at,
                   NULL::timestamptz AS resolved_at, NULL AS reviewer_id,
                   NULL AS decision
            FROM {schema}.fe_state_artifact
            WHERE {' AND '.join(filters)}
            ORDER BY updated_at DESC
            LIMIT %s OFFSET %s
        """
        params.extend([limit, offset])
    else:
        decision_map = {"APPROVED": "approve", "REJECTED": "reject"}
        uwcr_decision = decision_map.get(status_up)
        if uwcr_decision is None:
            return {"items": [], "limit": limit, "offset": offset, "status": status_up}
        filters = ["(ap.doc->>'decision') = %s"]
        params.append(uwcr_decision)
        if workspace_id:
            filters.append("art.workspace_id = %s")
            params.append(workspace_id)
        # status_up is validated against decision_map keys above — safe to inline.
        sql = f"""
            SELECT ap.approval_id, ap.artifact_id, ap.decided_at AS resolved_at,
                   art.updated_at AS submitted_at, art.artifact_type, art.workspace_id,
                   '{status_up}' AS status,
                   (ap.doc->>'decided_by') AS reviewer_id,
                   (ap.doc->>'decision')   AS decision,
                   (ap.doc->>'role')       AS role,
                   (ap.doc->>'comment')    AS comment
            FROM {schema}.fe_state_approval ap
            LEFT JOIN {schema}.fe_state_artifact art ON art.id = ap.artifact_id
            WHERE {' AND '.join(filters)}
            ORDER BY ap.decided_at DESC
            LIMIT %s OFFSET %s
        """
        params.extend([limit, offset])

    try:
        rows = await fetch_all(sql, params)
        return {"items": rows, "limit": limit, "offset": offset, "source": "uwcr_store"}
    except Exception as exc:
        log.warning(f"[observability] list_approvals failed: {exc}")
        raise HTTPException(status_code=500, detail="Approval query failed")


@router.post("/approvals/{approval_id}/decide", response_model=ApprovalDecisionResponse)
async def decide_approval(
    approval_id: int,
    body: ApprovalDecision,
) -> ApprovalDecisionResponse:
    """Submit an approval decision (APPROVED | REJECTED | REVISION_REQUIRED)."""
    valid = {"APPROVED", "REJECTED", "REVISION_REQUIRED"}
    if body.decision not in valid:
        raise HTTPException(status_code=422, detail=f"decision must be one of {sorted(valid)}")

    try:
        from app.dao.postgres import get_pool
        from app.services.approval import ApprovalService
        svc = ApprovalService(get_pool(), _schema())
        accepted = await svc.submit_decision(
            approval_id=approval_id,
            decision=body.decision,
            revision_notes=body.revision_notes,
        )
    except Exception as exc:
        log.warning(f"[observability] decide_approval failed: {exc}")
        raise HTTPException(status_code=500, detail="Decision submission failed")

    if not accepted:
        raise HTTPException(status_code=409, detail="Approval not found or already resolved")

    return ApprovalDecisionResponse(
        approval_id=approval_id,
        decision=body.decision,
        accepted=True,
    )


# ── Rule traceability ─────────────────────────────────────────────────────────

@router.get("/traceability/rule/{rule_id}")
async def get_rule_traceability(rule_id: str) -> dict:
    """Return the full traceability chain for a business rule: source → requirement → code → test."""
    schema = _schema()
    sql = f"""
        SELECT id, rule_id, module_id, source_file,
               requirement_id, code_artifact_id, test_artifact_id,
               verification_status, agent_run_id, workflow_run_id, workspace_id, traced_at
        FROM {schema}.fe_rule_traceability
        WHERE rule_id = %s
        ORDER BY traced_at DESC
        LIMIT 1
    """
    try:
        row = await fetch_one(sql, (rule_id,))
    except Exception as exc:
        log.warning(f"[observability] get_rule_traceability failed: {exc}")
        raise HTTPException(status_code=500, detail="Traceability query failed")

    if not row:
        raise HTTPException(status_code=404, detail=f"No traceability record for rule_id={rule_id!r}")
    return row


@router.get("/traceability/workspace/{workspace_id}")
async def get_workspace_traceability(
    workspace_id: str,
    module_id: str = Query(""),
) -> dict:
    """Return traceability summary for a workspace (rule counts by verification status)."""
    schema = _schema()
    params: list[Any] = [workspace_id]
    extra = ""
    if module_id:
        extra = " AND module_id = %s"
        params.append(module_id)

    sql = f"""
        SELECT verification_status, COUNT(*) AS rule_count
        FROM {schema}.fe_rule_traceability
        WHERE workspace_id = %s{extra}
        GROUP BY verification_status
    """
    try:
        rows = await fetch_all(sql, params)
        summary = {r["verification_status"]: r["rule_count"] for r in rows}
        total = sum(summary.values())
        return {
            "workspace_id": workspace_id,
            "module_id": module_id or None,
            "total_rules": total,
            "by_status": summary,
        }
    except Exception as exc:
        log.warning(f"[observability] get_workspace_traceability failed: {exc}")
        raise HTTPException(status_code=500, detail="Traceability summary failed")


# ── Metrics ───────────────────────────────────────────────────────────────────

@router.get("/metrics/cost")
async def get_cost_metrics(
    workspace_id: str = Query(""),
    days: int = Query(7, ge=1, le=90),
) -> dict:
    """Return LLM cost aggregation by model tier and workspace."""
    schema = _schema()
    params: list[Any] = [days]
    workspace_filter = ""
    if workspace_id:
        workspace_filter = " AND workspace_id = %s"
        params.append(workspace_id)

    # make_interval(days => %s) binds a real parameter — unlike INTERVAL '%s days',
    # where %s inside the quoted literal is NOT a psycopg placeholder.
    sql = f"""
        SELECT model_tier,
               COUNT(*) AS call_count,
               SUM(tokens_in) AS total_tokens_in,
               SUM(tokens_out) AS total_tokens_out,
               SUM(tokens_in + COALESCE(tokens_out, 0)) AS total_tokens,
               AVG(latency_ms) AS avg_latency_ms
        FROM {schema}.fe_telemetry
        WHERE event_type = 'llm_call'
          AND created_at >= NOW() - make_interval(days => %s)
          {workspace_filter}
        GROUP BY model_tier
        ORDER BY total_tokens DESC
    """
    try:
        rows = await fetch_all(sql, params)
        return {"days": days, "workspace_id": workspace_id or None, "by_model": rows}
    except Exception as exc:
        log.warning(f"[observability] get_cost_metrics failed: {exc}")
        raise HTTPException(status_code=500, detail="Cost metrics query failed")


@router.get("/metrics/performance")
async def get_performance_metrics(
    stage: str = Query(""),
    days: int = Query(7, ge=1, le=90),
) -> dict:
    """Return latency and throughput metrics by agent stage."""
    schema = _schema()
    params: list[Any] = [days]
    stage_filter = ""
    if stage:
        stage_filter = " AND stage = %s"
        params.append(stage)

    sql = f"""
        SELECT stage,
               COUNT(*) AS run_count,
               AVG(latency_ms) AS avg_latency_ms,
               PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY latency_ms) AS p50_latency_ms,
               PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY latency_ms) AS p95_latency_ms,
               MAX(latency_ms) AS max_latency_ms,
               AVG(retry_count) AS avg_retry_count,
               COUNT(CASE WHEN (metadata->>'status') = 'error' THEN 1 END) AS error_count
        FROM {schema}.fe_telemetry
        WHERE event_type = 'agent_run'
          AND created_at >= NOW() - make_interval(days => %s)
          {stage_filter}
        GROUP BY stage
        ORDER BY run_count DESC
    """
    try:
        rows = await fetch_all(sql, params)
        return {"days": days, "stage_filter": stage or None, "by_stage": rows}
    except Exception as exc:
        log.warning(f"[observability] get_performance_metrics failed: {exc}")
        raise HTTPException(status_code=500, detail="Performance metrics query failed")


# ── Executive overview (Screen 1) ──────────────────────────────────────────────

@router.get("/overview")
async def get_overview(
    workspace_id: str = Query(""),
    days: int = Query(30, ge=1, le=365),
) -> dict:
    """Aggregate KPIs for the executive dashboard (agent runs, workflows, eval, approvals)."""
    schema = _schema()
    ws_filter = ""
    run_params: list[Any] = [days]
    if workspace_id:
        ws_filter = " AND workspace_id = %s"
        run_params.append(workspace_id)

    runs_sql = f"""
        SELECT
            COUNT(*) AS total_runs,
            COUNT(*) FILTER (WHERE (metadata->>'status') = 'error') AS failed_runs,
            COUNT(*) FILTER (WHERE (metadata->>'status') = 'ok')    AS ok_runs,
            COUNT(DISTINCT workspace_id) AS workflows,
            COUNT(DISTINCT stage)        AS stages,
            AVG(latency_ms)              AS avg_latency_ms,
            COALESCE(SUM(tokens_in), 0) + COALESCE(SUM(tokens_out), 0) AS total_tokens,
            COUNT(*) FILTER (WHERE retry_count > 0) AS runs_with_retry
        FROM {schema}.fe_telemetry
        WHERE event_type = 'agent_run'
          AND created_at >= NOW() - make_interval(days => %s){ws_filter}
    """
    failed_wf_sql = f"""
        SELECT COUNT(*) AS failed_workflows FROM (
            SELECT workspace_id
            FROM {schema}.fe_telemetry
            WHERE event_type = 'agent_run' AND workspace_id IS NOT NULL
              AND created_at >= NOW() - make_interval(days => %s){ws_filter}
            GROUP BY workspace_id
            HAVING COUNT(*) FILTER (WHERE (metadata->>'status') = 'error') > 0
        ) t
    """
    eval_sql = f"""
        SELECT COUNT(*) AS eval_count, AVG(score) AS avg_score,
               COUNT(*) FILTER (WHERE passed_threshold) AS passed
        FROM {schema}.fe_evaluations
        {("WHERE workspace_id = %s" if workspace_id else "")}
    """
    appr_sql = f"""
        SELECT status, COUNT(*) AS n FROM {schema}.fe_approvals
        {("WHERE workspace_id = %s" if workspace_id else "")}
        GROUP BY status
    """
    try:
        runs = await fetch_one(runs_sql, run_params) or {}
        failed_wf = await fetch_one(failed_wf_sql, run_params) or {}
        eval_params = [workspace_id] if workspace_id else None
        evals = await fetch_one(eval_sql, eval_params) or {}
        appr_rows = await fetch_all(appr_sql, eval_params)
        approvals = {r["status"]: r["n"] for r in appr_rows}
        total_runs = runs.get("total_runs") or 0
        ok_runs = runs.get("ok_runs") or 0
        return {
            "days": days,
            "workspace_id": workspace_id or None,
            "runs": {
                "total": total_runs,
                "ok": ok_runs,
                "failed": runs.get("failed_runs") or 0,
                "success_rate": round(ok_runs / total_runs, 4) if total_runs else None,
                "retry_rate": round((runs.get("runs_with_retry") or 0) / total_runs, 4) if total_runs else None,
                "avg_latency_ms": runs.get("avg_latency_ms"),
                "total_tokens": runs.get("total_tokens") or 0,
            },
            "workflows": {
                "total": runs.get("workflows") or 0,
                "failed": failed_wf.get("failed_workflows") or 0,
                "stages": runs.get("stages") or 0,
            },
            "evaluations": {
                "count": evals.get("eval_count") or 0,
                "avg_score": float(evals["avg_score"]) if evals.get("avg_score") is not None else None,
                "passed": evals.get("passed") or 0,
            },
            "approvals": approvals,
        }
    except Exception as exc:
        log.warning(f"[observability] get_overview failed: {exc}")
        raise HTTPException(status_code=500, detail="Overview query failed")


# ── Workflows (Screen 2) ────────────────────────────────────────────────────────

@router.get("/workflows")
async def list_workflows(
    limit: int = Query(50, le=200),
    offset: int = Query(0, ge=0),
) -> dict:
    """List workflows (one per workspace_id) with roll-up run stats for the workflow monitor."""
    schema = _schema()
    sql = f"""
        SELECT workspace_id,
               COUNT(*)                                   AS run_count,
               COUNT(DISTINCT stage)                      AS stage_count,
               COUNT(*) FILTER (WHERE (metadata->>'status') = 'error') AS error_count,
               COUNT(*) FILTER (WHERE retry_count > 0)    AS retry_count,
               MIN(created_at)                            AS first_activity,
               MAX(created_at)                            AS last_activity,
               COALESCE(SUM(tokens_in), 0) + COALESCE(SUM(tokens_out), 0) AS total_tokens
        FROM {schema}.fe_telemetry
        WHERE event_type = 'agent_run' AND workspace_id IS NOT NULL
        GROUP BY workspace_id
        ORDER BY last_activity DESC
        LIMIT %s OFFSET %s
    """
    try:
        rows = await fetch_all(sql, [limit, offset])
        for r in rows:
            r["status"] = "attention" if (r.get("error_count") or 0) > 0 else "ok"
        return {"items": rows, "limit": limit, "offset": offset}
    except Exception as exc:
        log.warning(f"[observability] list_workflows failed: {exc}")
        raise HTTPException(status_code=500, detail="Workflows query failed")


@router.get("/workflows/{workflow_id}")
async def get_workflow(workflow_id: str) -> dict:
    """Return the ordered stage runs for one workflow (workspace_id) for drill-down."""
    schema = _schema()
    sql = f"""
        SELECT agent_run_id, stage, (metadata->>'status') AS status,
               latency_ms, tokens_in, tokens_out, retry_count,
               module_id, rule_id, artifact_id, workflow_run_id, created_at
        FROM {schema}.fe_telemetry
        WHERE event_type = 'agent_run' AND workspace_id = %s
        ORDER BY created_at ASC
    """
    try:
        rows = await fetch_all(sql, (workflow_id,))
    except Exception as exc:
        log.warning(f"[observability] get_workflow failed: {exc}")
        raise HTTPException(status_code=500, detail="Workflow query failed")

    if not rows:
        raise HTTPException(status_code=404, detail=f"No runs for workflow {workflow_id!r}")
    error_count = sum(1 for r in rows if r.get("status") == "error")
    return {
        "workflow_id": workflow_id,
        "run_count": len(rows),
        "status": "attention" if error_count else "ok",
        "stages": rows,
    }


# ── Trace view (Screen 5) ───────────────────────────────────────────────────────

@router.get("/traces/{trace_id}")
async def get_trace(trace_id: str) -> dict:
    """Return all telemetry events sharing a trace id (correlation_id / workflow_run_id / agent_run_id)."""
    schema = _schema()
    sql = f"""
        SELECT id, event_type, agent_run_id, correlation_id, workflow_run_id, stage,
               workspace_id, latency_ms, tokens_in, tokens_out, retry_count,
               module_id, rule_id, artifact_id, metadata, created_at
        FROM {schema}.fe_telemetry
        WHERE correlation_id = %s OR workflow_run_id = %s OR agent_run_id = %s
        ORDER BY created_at ASC
    """
    try:
        rows = await fetch_all(sql, (trace_id, trace_id, trace_id))
    except Exception as exc:
        log.warning(f"[observability] get_trace failed: {exc}")
        raise HTTPException(status_code=500, detail="Trace query failed")

    if not rows:
        raise HTTPException(status_code=404, detail=f"No trace events for {trace_id!r}")
    return {"trace_id": trace_id, "span_count": len(rows), "spans": rows}


# ── Failures & gaps (Screen 7) ──────────────────────────────────────────────────

@router.get("/failures")
async def list_failures(
    workspace_id: str = Query(""),
    days: int = Query(30, ge=1, le=365),
    limit: int = Query(50, le=200),
    offset: int = Query(0, ge=0),
) -> dict:
    """Return error-status agent runs (failures) for the failures & gaps screen."""
    schema = _schema()
    params: list[Any] = [days]
    ws_filter = ""
    if workspace_id:
        ws_filter = " AND workspace_id = %s"
        params.append(workspace_id)
    sql = f"""
        SELECT id, event_type, agent_run_id, workflow_run_id, correlation_id, stage,
               workspace_id, module_id, rule_id, artifact_id, latency_ms, retry_count,
               (metadata->>'status')     AS status,
               (metadata->>'error')      AS error_message,
               (metadata->>'error_code') AS error_code,
               (metadata->>'termination') AS termination,
               created_at
        FROM {schema}.fe_telemetry
        WHERE (metadata->>'status') = 'error'
          AND created_at >= NOW() - make_interval(days => %s){ws_filter}
        ORDER BY created_at DESC
        LIMIT %s OFFSET %s
    """
    params.extend([limit, offset])
    try:
        rows = await fetch_all(sql, params)
        return {"items": rows, "limit": limit, "offset": offset,
                "days": days, "workspace_id": workspace_id or None}
    except Exception as exc:
        log.warning(f"[observability] list_failures failed: {exc}")
        raise HTTPException(status_code=500, detail="Failures query failed")


# ── Human interventions (Screen 8) ──────────────────────────────────────────────

@router.get("/human-interventions")
async def get_human_interventions(
    workspace_id: str = Query(""),
) -> dict:
    """Aggregate approval metrics from the authoritative UWCR store (GAP-008).

    pending  = artifacts awaiting review (fe_state_artifact.status = IN_REVIEW)
    resolved = fe_state_approval decisions (approve / reject).
    avg_resolution_seconds is not exposed — the UWCR store records the decision
    time but not a stable submitted-for-review timestamp, so it is left null
    rather than reported inaccurately.
    """
    schema = _schema()
    art_params: list[Any] = []
    art_ws = ""
    if workspace_id:
        art_ws = " AND workspace_id = %s"
        art_params.append(workspace_id)
    pending_sql = f"SELECT COUNT(*) AS n FROM {schema}.fe_state_artifact WHERE status = 'IN_REVIEW'{art_ws}"

    appr_params: list[Any] = []
    appr_ws = ""
    if workspace_id:
        appr_ws = " WHERE art.workspace_id = %s"
        appr_params.append(workspace_id)
    decisions_sql = f"""
        SELECT (ap.doc->>'decision') AS decision, COUNT(*) AS n
        FROM {schema}.fe_state_approval ap
        LEFT JOIN {schema}.fe_state_artifact art ON art.id = ap.artifact_id
        {appr_ws}
        GROUP BY (ap.doc->>'decision')
    """
    try:
        pending_row = await fetch_one(pending_sql, art_params or None) or {}
        drows = await fetch_all(decisions_sql, appr_params or None)
    except Exception as exc:
        log.warning(f"[observability] get_human_interventions failed: {exc}")
        raise HTTPException(status_code=500, detail="Human-interventions query failed")

    by_decision = {r["decision"]: r["n"] for r in drows}
    approved = by_decision.get("approve", 0)
    rejected = by_decision.get("reject", 0)
    resolved = approved + rejected
    pending = pending_row.get("n", 0)
    return {
        "workspace_id": workspace_id or None,
        "source": "uwcr_store",
        "pending": pending,
        "approved": approved,
        "rejected": rejected,
        "resolved": resolved,
        "total": pending + resolved,
        "approval_rate": round(approved / resolved, 4) if resolved else None,
        "rejection_rate": round(rejected / resolved, 4) if resolved else None,
        "avg_resolution_seconds": None,
    }


# ── Health ────────────────────────────────────────────────────────────────────

@router.get("/health")
async def observability_health() -> dict:
    """Telemetry pipeline health: verifies DB connectivity and sink registry."""
    from app.services.telemetry import _sinks
    schema = _schema()
    db_status = "unknown"

    try:
        await fetch_one(f"SELECT COUNT(*) AS n FROM {schema}.fe_telemetry")
        db_status = "ok"
    except Exception as exc:
        db_status = f"error: {type(exc).__name__}"

    return {
        "telemetry_db": db_status,
        "sinks_registered": len(_sinks),
        "schema": schema,
    }
