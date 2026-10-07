"""
Telemetry — a lightweight event emitter with a pluggable sink registry.

The base framework only defines the seam: an ``emit(event_type, payload)`` that fans
out to registered sinks (which are guarded so a sink failure never breaks a request).
A concrete sink (Postgres ``fe_telemetry`` / an external API) is registered at startup
in a later milestone via ``register_sink(...)``.

EventType constants give every caller a stable vocabulary so dashboards and alerts
can query by event type without string-matching on free-form payloads.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from app.utils.logging import log
from app.utils.request_context import (
    get_agent_run_id,
    get_correlation_id,
    get_persona,
    get_session_id,
    get_tool_call_id,
    get_userid,
    get_workflow_run_id,
)


class EventType:
    """Stable event type vocabulary. Use these constants in all emit() calls."""

    # Retrieval pipeline events
    RETRIEVAL = "retrieval"                # One hybrid retrieval pass (3 lanes → RRF → select)
    RETRIEVAL_LANE = "retrieval_lane"      # Individual lane result (graph | bm25 | dense)

    # LLM call events
    LLM_CALL = "llm_call"                 # One ChatBedrockConverse invocation (tokens + latency)

    # Agent lifecycle events (extended)
    AGENT_STARTED = "agent_started"        # Agent begins execution (before first LLM call)
    AGENT_RUN = "agent_run"               # One complete ReAct agent execution
    AGENT_RETRY = "agent_retry"            # Tenacity retry attempt within an agent run
    TOOL_CALL = "tool_call"               # One tool invocation inside an agent run

    # Prompt / output events
    CONTEXT_RETRIEVED = "context_retrieved"  # KB context assembled and injected into prompt
    PROMPT_BUILT = "prompt_built"            # Final prompt assembled (tokens counted, version stamped)
    OUTPUT_GENERATED = "output_generated"    # First token received (streaming) or full response ready

    # Validation events
    VALIDATION_STARTED = "validation_started"    # Output validation begins
    VALIDATION_COMPLETED = "validation_completed"  # Output validation finished (pass/fail + dimension)

    # Evaluation events
    EVALUATION_STARTED = "evaluation_started"    # Evaluation scoring begins
    EVALUATION_COMPLETED = "evaluation_completed"  # Evaluation scoring finished (score + dimension)

    # Human approval events
    HUMAN_APPROVAL_PENDING = "human_approval_pending"    # Artifact submitted for human review
    HUMAN_APPROVAL_RESOLVED = "human_approval_resolved"  # Reviewer decided (approved/rejected/revision)

    # Anomaly events
    AMBIGUITY_DETECTED = "ambiguity_detected"    # LLM output ambiguous or low-confidence

    # Stage lifecycle events
    STAGE_GENERATE = "stage_generate"     # One fe.generate stage execution (start → end)

    # Grounding events
    GROUNDING = "grounding"               # Grounding gate verdict (PASS | ABSTAIN | BLOCK)

    # Chat / query events
    CHAT_QUERY = "chat_query"             # One /fe/query or /fe/chat turn (end-to-end)

    # Evaluation events (app/services/evaluation.py)
    AGENT_EVALUATION = "agent_evaluation"  # Per-run evaluation record (signals + verdict)

    # Readiness gate events (app/services/readiness_gate.py)
    READINESS_GATE = "readiness_gate"      # Pre-DEVELOPMENT gate verdict (PASS | FAIL | WARN)

    # HITL events
    HITL_INTERRUPT = "hitl_interrupt"      # Human-in-the-loop pause point triggered
    HITL_RESUME = "hitl_resume"            # Human approved or rejected; workflow resumed


@dataclass
class TelemetryEvent:
    """One telemetry event, auto-stamped with request context."""

    event_type: str
    payload: dict[str, Any] = field(default_factory=dict)
    correlation_id: str = ""
    agent_run_id: str = ""
    workflow_run_id: str = ""
    tool_call_id: str = ""
    session_id: str = ""
    userid: str = "unknown"
    persona: str = ""
    ts: float = 0.0


# Module-level sink registry — a sink is any callable taking a TelemetryEvent.
_sinks: list[Callable[[TelemetryEvent], None]] = []


def register_sink(sink: Callable[[TelemetryEvent], None]) -> None:
    """Register a telemetry sink (called for every emitted event). Idempotent per object."""
    if sink not in _sinks:
        _sinks.append(sink)
        log.info(f"[telemetry] Sink registered: {getattr(sink, '__name__', repr(sink))}")


class TelemetryService:
    """Builds events from request context and fans them out to registered sinks."""

    def __init__(self) -> None:
        self._enabled: bool = get_settings_enabled()

    def emit(self, event_type: str, payload: dict[str, Any] | None = None) -> None:
        """Emit a telemetry event. Never raises — sink failures are logged and swallowed."""
        if not self._enabled:
            return
        event = TelemetryEvent(
            event_type=event_type,
            payload=payload or {},
            correlation_id=get_correlation_id(),
            agent_run_id=get_agent_run_id(),
            workflow_run_id=get_workflow_run_id(),
            tool_call_id=get_tool_call_id(),
            session_id=get_session_id(),
            userid=get_userid(),
            persona=get_persona(),
            ts=time.time(),
        )
        for sink in _sinks:
            try:
                sink(event)
            except Exception as exc:  # pragma: no cover - defensive
                log.warning(f"[telemetry] Sink {getattr(sink, '__name__', repr(sink))} failed: {exc}")

    # ── Typed emit helpers ──────────────────────────────────────────────────────

    def emit_retrieval(
        self,
        *,
        kb_version: str,
        lane_counts: dict[str, int],
        fused_count: int,
        final_count: int,
        latency_ms: float,
        top_score: float = 0.0,
    ) -> None:
        """Emit a RETRIEVAL event with per-lane counts and scoring metrics."""
        self.emit(
            EventType.RETRIEVAL,
            {
                "kb_version": kb_version,
                "lane_counts": lane_counts,
                "fused_count": fused_count,
                "final_count": final_count,
                "latency_ms": round(latency_ms, 1),
                "top_score": round(top_score, 6),
            },
        )

    def emit_llm_call(
        self,
        *,
        task: str,
        model_id: str,
        tokens_in: int,
        tokens_out: int,
        latency_ms: float,
        stage: str = "",
    ) -> None:
        """Emit an LLM_CALL event with token usage and latency."""
        self.emit(
            EventType.LLM_CALL,
            {
                "task": task,
                "model_id": model_id,
                "tokens_in": tokens_in,
                "tokens_out": tokens_out,
                "tokens_total": tokens_in + tokens_out,
                "latency_ms": round(latency_ms, 1),
                "stage": stage,
            },
        )

    def emit_tool_call(
        self,
        *,
        tool_name: str,
        tool_call_id: str,
        agent_run_id: str,
        latency_ms: float,
        status: str,
        result_count: int = 0,
    ) -> None:
        """Emit a TOOL_CALL event with execution timing and outcome."""
        self.emit(
            EventType.TOOL_CALL,
            {
                "tool_name": tool_name,
                "tool_call_id": tool_call_id,
                "agent_run_id": agent_run_id,
                "latency_ms": round(latency_ms, 1),
                "status": status,
                "result_count": result_count,
            },
        )

    def emit_agent_run(
        self,
        *,
        agent_run_id: str,
        stage: str,
        persona: str,
        iterations: int,
        status: str,
        latency_ms: float,
        tokens_in: int = 0,
        tokens_out: int = 0,
        retry_count: int = 0,
        workspace_id: str = "",
        module_id: str = "",
        feature_id: str = "",
        rule_ids: list[str] | None = None,
        artifact_id: str = "",
        prompt_version: str = "",
        agent_version: str = "",
    ) -> None:
        """Emit an AGENT_RUN event summarising one complete ReAct execution."""
        self.emit(
            EventType.AGENT_RUN,
            {
                "agent_run_id": agent_run_id,
                "stage": stage,
                "persona": persona,
                "iterations": iterations,
                "status": status,
                "latency_ms": round(latency_ms, 1),
                "tokens_in": tokens_in,
                "tokens_out": tokens_out,
                "retry_count": retry_count,
                # workspace_id is promoted to an indexed column by PostgresTelemetrySink
                # so /observability/agents and /metrics can scope by workspace.
                "workspace_id": workspace_id,
                "module_id": module_id,
                "feature_id": feature_id,
                "rule_ids": rule_ids or [],
                "artifact_id": artifact_id,
                "prompt_version": prompt_version,
                "agent_version": agent_version,
            },
        )

    def emit_agent_started(
        self,
        *,
        agent_name: str,
        stage: str,
        module_id: str = "",
        feature_id: str = "",
        rule_ids: list[str] | None = None,
        artifact_id: str = "",
        prompt_version: str = "",
        agent_version: str = "",
    ) -> None:
        """Emit an AGENT_STARTED event at the beginning of agent execution."""
        self.emit(
            EventType.AGENT_STARTED,
            {
                "agent_name": agent_name,
                "stage": stage,
                "module_id": module_id,
                "feature_id": feature_id,
                "rule_ids": rule_ids or [],
                "artifact_id": artifact_id,
                "prompt_version": prompt_version,
                "agent_version": agent_version,
            },
        )

    def emit_agent_retry(
        self,
        *,
        agent_name: str,
        stage: str,
        attempt: int,
        reason: str,
        module_id: str = "",
        artifact_id: str = "",
    ) -> None:
        """Emit an AGENT_RETRY event from a tenacity retry callback."""
        self.emit(
            EventType.AGENT_RETRY,
            {
                "agent_name": agent_name,
                "stage": stage,
                "attempt": attempt,
                "reason": reason,
                "module_id": module_id,
                "artifact_id": artifact_id,
            },
        )

    def emit_validation(
        self,
        *,
        event_type: str,
        agent_name: str,
        stage: str,
        dimension: str,
        validation_status: str = "",
        score: float | None = None,
        rule_ids: list[str] | None = None,
        artifact_id: str = "",
        details: dict | None = None,
    ) -> None:
        """Emit a VALIDATION_STARTED or VALIDATION_COMPLETED event."""
        self.emit(
            event_type,
            {
                "agent_name": agent_name,
                "stage": stage,
                "dimension": dimension,
                "validation_status": validation_status,
                "score": score,
                "rule_ids": rule_ids or [],
                "artifact_id": artifact_id,
                **(details or {}),
            },
        )

    def emit_evaluation(
        self,
        *,
        event_type: str,
        agent_name: str,
        stage: str,
        evaluator_type: str,
        dimension: str,
        score: float | None = None,
        passed_threshold: bool | None = None,
        threshold_value: float | None = None,
        rationale: str = "",
        rule_ids: list[str] | None = None,
        artifact_id: str = "",
    ) -> None:
        """Emit an EVALUATION_STARTED or EVALUATION_COMPLETED event."""
        self.emit(
            event_type,
            {
                "agent_name": agent_name,
                "stage": stage,
                "evaluator_type": evaluator_type,
                "dimension": dimension,
                "score": score,
                "passed_threshold": passed_threshold,
                "threshold_value": threshold_value,
                "rationale": rationale,
                "rule_ids": rule_ids or [],
                "artifact_id": artifact_id,
            },
        )

    def emit_approval_pending(
        self,
        *,
        artifact_id: str,
        artifact_type: str,
        workspace_id: str = "",
        rule_ids: list[str] | None = None,
        ttl_seconds: int = 86400,
    ) -> None:
        """Emit HUMAN_APPROVAL_PENDING when an artifact is submitted for review."""
        self.emit(
            EventType.HUMAN_APPROVAL_PENDING,
            {
                "artifact_id": artifact_id,
                "artifact_type": artifact_type,
                "workspace_id": workspace_id,
                "rule_ids": rule_ids or [],
                "ttl_seconds": ttl_seconds,
            },
        )

    def emit_approval_resolved(
        self,
        *,
        artifact_id: str,
        artifact_type: str,
        decision: str,
        reviewer_id: str,
        workspace_id: str = "",
        latency_ms: float = 0.0,
    ) -> None:
        """Emit HUMAN_APPROVAL_RESOLVED when a reviewer submits their decision."""
        self.emit(
            EventType.HUMAN_APPROVAL_RESOLVED,
            {
                "artifact_id": artifact_id,
                "artifact_type": artifact_type,
                "decision": decision,
                "reviewer_id": reviewer_id,
                "workspace_id": workspace_id,
                "latency_ms": round(latency_ms, 1),
            },
        )

    def emit_ambiguity(
        self,
        *,
        agent_name: str,
        stage: str,
        confidence_score: float,
        details: str = "",
        artifact_id: str = "",
    ) -> None:
        """Emit AMBIGUITY_DETECTED when LLM output is low-confidence or unclear."""
        self.emit(
            EventType.AMBIGUITY_DETECTED,
            {
                "agent_name": agent_name,
                "stage": stage,
                "confidence_score": round(confidence_score, 4),
                "details": details,
                "artifact_id": artifact_id,
            },
        )

    def emit_retrieval_lane(
        self,
        *,
        lane: str,
        count: int,
        candidate_ids: list[str],
        kb_version: str,
    ) -> None:
        """Emit a RETRIEVAL_LANE event with per-lane candidate IDs (capped at 50)."""
        self.emit(
            EventType.RETRIEVAL_LANE,
            {
                "lane": lane,
                "count": count,
                "candidate_ids": candidate_ids[:50],
                "kb_version": kb_version,
            },
        )

    def emit_prompt_built(
        self,
        *,
        stage: str,
        token_estimate: int,
        chunk_count: int,
        prompt_hash: str,
        workspace_id: str = "",
    ) -> None:
        """Emit a PROMPT_BUILT event after the final prompt is assembled."""
        self.emit(
            EventType.PROMPT_BUILT,
            {
                "stage": stage,
                "token_estimate": token_estimate,
                "chunk_count": chunk_count,
                "prompt_hash": prompt_hash,
                "workspace_id": workspace_id,
            },
        )

    def emit_grounding(
        self,
        *,
        stage: str,
        artifact_id: str,
        verdict: str,
        cited: int,
        known: int,
        unknown: int,
        coverage: float | None,
        workspace_id: str = "",
    ) -> None:
        """Emit a GROUNDING event with per-artifact grounding gate verdict."""
        self.emit(
            EventType.GROUNDING,
            {
                "stage": stage,
                "artifact_id": artifact_id,
                "verdict": verdict,
                "cited": cited,
                "known": known,
                "unknown": unknown,
                "coverage": round(coverage, 4) if coverage is not None else None,
                "workspace_id": workspace_id,
            },
        )

    def emit_readiness_gate(
        self,
        *,
        stage: str,
        workspace_id: str,
        verdict: str,
        reasons: list[str],
        metrics: dict,
    ) -> None:
        """Emit a READINESS_GATE event with the pre-stage gate decision."""
        self.emit(
            EventType.READINESS_GATE,
            {
                "stage": stage,
                "workspace_id": workspace_id,
                "verdict": verdict,
                "reasons": reasons,
                "metrics": metrics,
            },
        )


def get_settings_enabled() -> bool:
    from app.config.settings import get_settings

    return get_settings().TELEMETRY_ENABLED


@lru_cache(maxsize=1)
def get_telemetry_service() -> TelemetryService:
    """Return the process-wide singleton TelemetryService."""
    return TelemetryService()
