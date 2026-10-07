"""
Readiness Gate Service — two-layer gate for stage/workspace readiness.

Layer 1 (sync, no DB): ReadinessGate.check() — validates workspace state and
artifact acceptance for the pre-DEVELOPMENT gate. Used by the existing test suite
and the approval-flow API.

Layer 2 (async, DB-backed): evaluate_readiness() — queries fe_telemetry,
fe_evaluations, and context_provenance to produce a metric-based scorecard.
Used by the new /evaluation/readiness endpoint.
"""

from __future__ import annotations

import enum
import logging
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from app.services.telemetry import get_telemetry_service

log = logging.getLogger(__name__)

# ── Layer 1: sync gate (preserved API) ───────────────────────────────────────

_REQUIRED_ARTIFACT_KINDS = ("FSD", "SRD", "stories")
_VALID_STATES = ("STORIES", "ARCHITECTURE")
_ASSUMPTION_WARN_THRESHOLD = 0.15


class CheckStatus(str, enum.Enum):
    OK = "ok"
    FAIL = "fail"
    WARN = "warn"


class GateVerdict(str, enum.Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    WARN = "WARN"


@dataclass
class CheckResult:
    name: str
    status: CheckStatus
    detail: str = ""


@dataclass
class GateResult:
    verdict: GateVerdict
    checks: list[CheckResult] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def passed(self) -> bool:
        return self.verdict == GateVerdict.PASS

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict.value,
            "passed": self.passed(),
            "blockers": self.blockers,
            "warnings": self.warnings,
            "checks": [{"name": c.name, "status": c.status.value, "detail": c.detail}
                       for c in self.checks],
        }


class ReadinessGate:
    """Synchronous pre-DEVELOPMENT gate: validates workspace state and artifacts."""

    def check(
        self,
        *,
        workspace_id: str,
        workspace_state: Any,
        artifacts: list[dict],
        assumption_ratios: dict[str, float] | None = None,
    ) -> GateResult:
        checks: list[CheckResult] = []
        blockers: list[str] = []
        warnings: list[str] = []

        state_name = workspace_state.value if hasattr(workspace_state, "value") else str(workspace_state)
        state_ok = state_name in _VALID_STATES
        checks.append(CheckResult(
            name="workspace_state",
            status=CheckStatus.OK if state_ok else CheckStatus.FAIL,
            detail=f"state={state_name}",
        ))
        if not state_ok:
            blockers.append(f"Workspace must be in STORIES or ARCHITECTURE state, got {state_name}")

        accepted_kinds = {
            a["kind"] for a in artifacts if a.get("status") == "ACCEPTED"
        }
        draft_kinds = {
            a["kind"] for a in artifacts if a.get("status") == "DRAFT"
        }

        for kind in _REQUIRED_ARTIFACT_KINDS:
            present = any(a["kind"] == kind for a in artifacts)
            accepted = kind in accepted_kinds
            status = CheckStatus.OK if (present and accepted) else CheckStatus.FAIL
            checks.append(CheckResult(name=f"artifact_{kind}", status=status))
            if not present:
                blockers.append(f"Required artifact {kind} is missing")
            elif not accepted:
                blockers.append(f"Artifact {kind} is not ACCEPTED")

        draft_blocking = draft_kinds & set(_REQUIRED_ARTIFACT_KINDS)
        no_draft_status = CheckStatus.FAIL if draft_blocking else CheckStatus.OK
        checks.append(CheckResult(
            name="no_draft_blocking_artifacts",
            status=no_draft_status,
            detail=f"draft kinds: {sorted(draft_blocking)}" if draft_blocking else "",
        ))
        for kind in sorted(draft_blocking):
            blockers.append(f"Artifact {kind} has a DRAFT version that is not ACCEPTED")

        for kind, ratio in (assumption_ratios or {}).items():
            if ratio > _ASSUMPTION_WARN_THRESHOLD:
                warnings.append(
                    f"High assumption ratio in {kind}: {ratio:.0%} "
                    f"(threshold {_ASSUMPTION_WARN_THRESHOLD:.0%})"
                )

        if blockers:
            verdict = GateVerdict.FAIL
        elif warnings:
            verdict = GateVerdict.WARN
        else:
            verdict = GateVerdict.PASS

        return GateResult(verdict=verdict, checks=checks, blockers=blockers, warnings=warnings)


@lru_cache(maxsize=1)
def get_readiness_gate() -> ReadinessGate:
    return ReadinessGate()


# ── Layer 2: async DB-backed gate (new) ───────────────────────────────────────

_LLM_SCORE_PASS = 0.7
_GROUNDEDNESS_PASS = 0.6


@dataclass
class MetricGateResult:
    verdict: str
    reasons: list[str] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)


async def evaluate_readiness(
    *,
    workspace_id: str,
    stage: str,
    pool: Any,
    schema: str,
) -> MetricGateResult:
    """Query stage metrics from Postgres and return a structured readiness verdict."""
    metrics = await _fetch_metrics(workspace_id, stage, pool, schema)
    result = _apply_rules(metrics)
    _emit_gate(workspace_id, stage, result)
    return result


async def _fetch_metrics(
    workspace_id: str, stage: str, pool: Any, schema: str,
) -> dict:
    m: dict = {}
    try:
        async with pool.connection() as conn:
            row = await conn.fetchrow(
                f"""
                SELECT AVG((metadata->>'latency_ms')::float) AS avg_latency,
                       SUM(COALESCE(tokens_in,0) + COALESCE(tokens_out,0)) AS total_tokens
                FROM {schema}.fe_telemetry
                WHERE workspace_id = %s AND stage = %s AND event_type = 'stage_generate'
                """,
                workspace_id, stage,
            )
            if row:
                m["latency_ms"] = row["avg_latency"]
                m["tokens_total"] = row["total_tokens"]

            eval_rows = await conn.fetch(
                f"""
                SELECT dimension, AVG(score) AS avg_score
                FROM {schema}.fe_evaluations
                WHERE evaluator_type = 'llm' AND workspace_id = %s
                GROUP BY dimension
                """,
                workspace_id,
            )
            for r in eval_rows:
                m[r["dimension"]] = r["avg_score"]

            prov_row = await conn.fetchrow(
                f"""
                SELECT COUNT(*) FILTER (WHERE was_sent) AS sent
                FROM {schema}.fe_context_provenance
                WHERE workspace_id = %s AND stage = %s
                """,
                workspace_id, stage,
            )
            if prov_row:
                m["context_chunk_count"] = prov_row["sent"]
    except Exception as exc:
        log.warning("[readiness_gate] metrics fetch failed (non-fatal): %s", exc)
    return m


def _apply_rules(metrics: dict) -> MetricGateResult:
    reasons: list[str] = []
    verdict = "PASS"

    hallucination = metrics.get("hallucination_risk")
    if hallucination is not None and hallucination > (1 - _GROUNDEDNESS_PASS):
        reasons.append(f"Hallucination risk {hallucination:.2f} exceeds threshold")
        verdict = "BLOCK"

    correctness = metrics.get("correctness")
    if correctness is not None and correctness < _LLM_SCORE_PASS:
        reasons.append(f"Correctness score {correctness:.2f} below {_LLM_SCORE_PASS}")
        verdict = "BLOCK" if correctness < 0.5 else ("WARN" if verdict != "BLOCK" else verdict)

    completeness = metrics.get("completeness")
    if completeness is not None and completeness < _LLM_SCORE_PASS:
        reasons.append(f"Completeness score {completeness:.2f} below {_LLM_SCORE_PASS}")
        if verdict not in ("BLOCK",):
            verdict = "WARN"

    if metrics.get("context_chunk_count") == 0:
        reasons.append("No KB context was retrieved for this stage")
        if verdict not in ("BLOCK",):
            verdict = "WARN"

    if not reasons:
        reasons.append("All measured thresholds met")

    return MetricGateResult(verdict=verdict, reasons=reasons, metrics=metrics)


def _emit_gate(workspace_id: str, stage: str, result: MetricGateResult) -> None:
    try:
        get_telemetry_service().emit_readiness_gate(
            stage=stage,
            workspace_id=workspace_id,
            verdict=result.verdict,
            reasons=result.reasons,
            metrics=result.metrics,
        )
    except Exception as exc:
        log.debug("[readiness_gate] emit failed (non-fatal): %s", exc)
