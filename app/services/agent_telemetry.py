"""
Agent Telemetry SDK — a common, reusable observability facade for every AIDLC agent.

Design goals
------------
* **Reuse, don't reinvent.** Every event is emitted through the existing
  ``TelemetryService.emit(...)`` seam (``app/services/telemetry.py``), so it fans out
  to the already-registered Postgres sink + GAME hook and inherits the request-context
  correlation IDs. This module adds a *higher-level* vocabulary and lifecycle, not a
  second telemetry pipeline (see architectural principle §56 of the observability spec).
* **One line to instrument an agent.** ``with agent_execution(ctx): ...`` opens an
  execution scope that stamps IDs, emits AGENT_STARTED / AGENT_COMPLETED / AGENT_FAILED
  automatically, and exposes ``record_llm_call`` / ``record_tool_call`` /
  ``record_validation`` / ``record_evaluation`` / ``record_retry`` / ``record_error``.
* **OpenTelemetry-optional.** If ``opentelemetry`` is importable, real workflow→agent→
  llm→tool spans are created and ``trace_id``/``span_id`` are captured. If it is NOT
  installed (current state), a lightweight uuid trace/span id is generated so
  correlation still works and spans degrade to no-ops. No behavior change either way.

No secrets are ever recorded here — callers pass metrics/metadata, not payloads.
"""

from __future__ import annotations

import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator

from app.utils.logging import log
from app.utils import request_context as rc
from app.services.telemetry import get_telemetry_service

# pipeline_execution_id is optional in request_context — guard the import
try:
    from app.utils.request_context import get_pipeline_execution_id as _get_pipeline_id
except ImportError:
    def _get_pipeline_id() -> str:  # type: ignore[misc]
        return ""

# ── OpenTelemetry (optional) ────────────────────────────────────────────────────
try:  # pragma: no cover - depends on optional dependency being installed
    from opentelemetry import trace as _otel_trace  # type: ignore

    _OTEL_TRACER = _otel_trace.get_tracer("aidlc.agent")
    _OTEL_AVAILABLE = True
except Exception:  # ImportError or any init failure → graceful no-op
    _otel_trace = None  # type: ignore
    _OTEL_TRACER = None
    _OTEL_AVAILABLE = False


class AgentEvent:
    """Structured agent-lifecycle event vocabulary (spec §10).

    These are the *granular* per-execution events. They complement — do not replace —
    the coarse EventType.AGENT_RUN / TOOL_CALL / LLM_CALL summaries in telemetry.py.
    """

    AGENT_STARTED = "agent_started"
    CONTEXT_RETRIEVED = "context_retrieved"
    PROMPT_BUILT = "prompt_built"
    LLM_REQUEST_STARTED = "llm_request_started"
    LLM_REQUEST_COMPLETED = "llm_request_completed"
    TOOL_CALL_STARTED = "tool_call_started"
    TOOL_CALL_COMPLETED = "tool_call_completed"
    OUTPUT_GENERATED = "output_generated"
    VALIDATION_STARTED = "validation_started"
    VALIDATION_COMPLETED = "validation_completed"
    EVALUATION_STARTED = "evaluation_started"
    EVALUATION_COMPLETED = "evaluation_completed"
    HUMAN_APPROVAL_REQUESTED = "human_approval_requested"
    HUMAN_APPROVAL_COMPLETED = "human_approval_completed"
    AGENT_RETRY = "agent_retry"
    AGENT_FAILED = "agent_failed"
    AGENT_COMPLETED = "agent_completed"
    AMBIGUITY_DETECTED = "ambiguity_detected"


class ErrorClass:
    """Error taxonomy (spec §22)."""

    LLM_ERROR = "LLM_ERROR"
    TOOL_ERROR = "TOOL_ERROR"
    DATABASE_ERROR = "DATABASE_ERROR"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    AUTHENTICATION_ERROR = "AUTHENTICATION_ERROR"
    AUTHORIZATION_ERROR = "AUTHORIZATION_ERROR"
    TIMEOUT = "TIMEOUT"
    RATE_LIMIT = "RATE_LIMIT"
    CONTEXT_ERROR = "CONTEXT_ERROR"
    PARSING_ERROR = "PARSING_ERROR"
    CONFIGURATION_ERROR = "CONFIGURATION_ERROR"
    UNKNOWN = "UNKNOWN"


@dataclass
class AgentExecutionContext:
    """The common identifier + provenance set carried through one agent execution (spec §8).

    Reuses existing identifiers where present (workflow_run_id, agent_run_id/execution_id).
    Only ``agent_id`` is required; everything else is optional and filled as known.
    """

    agent_id: str
    agent_name: str = ""
    agent_version: str = ""

    # Correlation across the SDLC lifecycle
    workflow_id: str = ""
    module_id: str = ""
    feature_id: str = ""
    rule_id: str = ""
    artifact_id: str = ""

    execution_id: str = ""
    parent_execution_id: str = ""
    pipeline_execution_id: str = ""  # cross-repo trace ID from X-Pipeline-ID header
    trace_id: str = ""
    span_id: str = ""

    # Model / prompt provenance
    model_provider: str = ""
    model_name: str = ""
    model_version: str = ""
    prompt_version: str = ""

    environment: str = ""
    status: str = "started"
    retry_count: int = 0
    human_approval_status: str = ""

    started_at: float = 0.0
    completed_at: float = 0.0

    def identity(self) -> dict[str, Any]:
        """The identifier/provenance snapshot embedded in every event payload."""
        return {
            "workflow_id": self.workflow_id,
            "module_id": self.module_id,
            "feature_id": self.feature_id,
            "rule_id": self.rule_id,
            "artifact_id": self.artifact_id,
            "agent_id": self.agent_id,
            "agent_name": self.agent_name,
            "agent_version": self.agent_version,
            "execution_id": self.execution_id,
            "parent_execution_id": self.parent_execution_id,
            "pipeline_execution_id": self.pipeline_execution_id,
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "model_provider": self.model_provider,
            "model_name": self.model_name,
            "model_version": self.model_version,
            "prompt_version": self.prompt_version,
            "environment": self.environment,
        }


class AgentTelemetry:
    """Per-execution telemetry handle. Obtain one via ``agent_execution(...)``.

    Every method emits a structured lifecycle event through the shared telemetry seam;
    sink failures are already swallowed there, so instrumentation never breaks an agent.
    """

    def __init__(self, ctx: AgentExecutionContext, otel_span: Any = None) -> None:
        self.ctx = ctx
        self._otel_span = otel_span

    # ── core emit ────────────────────────────────────────────────────────────────
    def _emit(self, event_type: str, extra: dict[str, Any] | None = None) -> None:
        payload = self.ctx.identity()
        payload["event_type"] = event_type
        payload["status"] = self.ctx.status
        payload["retry_count"] = self.ctx.retry_count
        if extra:
            payload.update(extra)
        get_telemetry_service().emit(event_type, payload)
        if self._otel_span is not None and event_type not in (
            AgentEvent.AGENT_STARTED, AgentEvent.AGENT_COMPLETED, AgentEvent.AGENT_FAILED,
        ):
            try:
                self._otel_span.add_event(event_type, attributes=_otel_attrs(extra or {}))
            except Exception:  # pragma: no cover
                pass

    def record_event(self, event_type: str, **extra: Any) -> None:
        """Emit an arbitrary lifecycle event (use AgentEvent.* constants)."""
        self._emit(event_type, extra)

    # ── lifecycle helpers ─────────────────────────────────────────────────────────
    def context_retrieved(self, *, chunk_count: int = 0, kb_version: str = "", latency_ms: float = 0.0) -> None:
        self._emit(AgentEvent.CONTEXT_RETRIEVED,
                   {"chunk_count": chunk_count, "kb_version": kb_version, "latency_ms": round(latency_ms, 1)})

    def prompt_built(self, *, prompt_version: str = "", prompt_hash: str = "", char_count: int = 0) -> None:
        if prompt_version:
            self.ctx.prompt_version = prompt_version
        self._emit(AgentEvent.PROMPT_BUILT,
                   {"prompt_version": prompt_version or self.ctx.prompt_version,
                    "prompt_hash": prompt_hash, "char_count": char_count})

    def record_llm_call(self, *, model_name: str = "", model_provider: str = "", tokens_in: int = 0,
                        tokens_out: int = 0, latency_ms: float = 0.0, status: str = "success",
                        temperature: float | None = None, prompt_version: str = "",
                        retry_count: int = 0, response_hash: str = "") -> None:
        """Record one LLM invocation (spec §13). Also feeds the coarse LLM_CALL summary."""
        model_name = model_name or self.ctx.model_name
        model_provider = model_provider or self.ctx.model_provider
        extra = {
            "model_name": model_name, "model_provider": model_provider,
            "tokens_in": tokens_in, "tokens_out": tokens_out,
            "total_tokens": tokens_in + tokens_out, "latency_ms": round(latency_ms, 1),
            "llm_status": status, "temperature": temperature,
            "prompt_version": prompt_version or self.ctx.prompt_version,
            "retry_count": retry_count, "response_hash": response_hash,
        }
        self._emit(AgentEvent.LLM_REQUEST_COMPLETED, extra)
        # keep the existing coarse metric populated too
        try:
            get_telemetry_service().emit_llm_call(
                task=self.ctx.agent_id, model_id=model_name,
                tokens_in=tokens_in, tokens_out=tokens_out,
                latency_ms=latency_ms, stage=self.ctx.rule_id or "",
            )
        except Exception:  # pragma: no cover
            pass

    def record_tool_call(self, *, tool_name: str, latency_ms: float = 0.0, status: str = "success",
                         tool_version: str = "", result_count: int = 0, error: str = "",
                         retry_count: int = 0) -> None:
        """Record one tool invocation (spec §14). Never persists tool arguments."""
        tool_call_id = uuid.uuid4().hex[:12]
        prev = rc.get_tool_call_id()
        rc.set_tool_call_id(tool_call_id)
        try:
            self._emit(AgentEvent.TOOL_CALL_COMPLETED, {
                "tool_name": tool_name, "tool_version": tool_version, "tool_call_id": tool_call_id,
                "tool_duration_ms": round(latency_ms, 1), "tool_status": status,
                "result_count": result_count, "error_message": error, "retry_count": retry_count,
            })
            try:
                get_telemetry_service().emit_tool_call(
                    tool_name=tool_name, tool_call_id=tool_call_id,
                    agent_run_id=self.ctx.execution_id, latency_ms=latency_ms,
                    status=status, result_count=result_count,
                )
            except Exception:  # pragma: no cover
                pass
        finally:
            rc.set_tool_call_id(prev)

    def record_validation(self, *, status: str, detail: str = "", checks_passed: int = 0,
                         checks_total: int = 0) -> None:
        self._emit(AgentEvent.VALIDATION_COMPLETED, {
            "validation_status": status, "detail": detail,
            "checks_passed": checks_passed, "checks_total": checks_total})

    def record_evaluation(self, *, status: str, correctness: float | None = None,
                         completeness: float | None = None, traceability: float | None = None,
                         consistency: float | None = None, hallucination: float | None = None,
                         detail: str = "") -> None:
        """Record an evaluation verdict (spec §17). Scores are None when not computed."""
        self._emit(AgentEvent.EVALUATION_COMPLETED, {
            "evaluation_status": status, "correctness": correctness, "completeness": completeness,
            "traceability": traceability, "consistency": consistency, "hallucination": hallucination,
            "detail": detail})

    def record_retry(self, *, reason: str = "", previous_error: str = "", strategy: str = "") -> None:
        self.ctx.retry_count += 1
        self._emit(AgentEvent.AGENT_RETRY, {
            "retry_reason": reason, "previous_error": previous_error, "retry_strategy": strategy})

    def record_error(self, *, error_class: str = ErrorClass.UNKNOWN, message: str = "",
                    code: str = "", recovery_status: str = "") -> None:
        self._emit(AgentEvent.AGENT_FAILED, {
            "error_code": code or error_class, "error_type": error_class,
            "error_message": message, "recovery_status": recovery_status})

    def human_approval_requested(self, *, approver: str = "") -> None:
        self.ctx.human_approval_status = "PENDING_APPROVAL"
        self._emit(AgentEvent.HUMAN_APPROVAL_REQUESTED, {"approver": approver})

    def human_approval_completed(self, *, decision: str, approver: str = "", comments: str = "",
                                revision_number: int = 0) -> None:
        self.ctx.human_approval_status = decision
        self._emit(AgentEvent.HUMAN_APPROVAL_COMPLETED, {
            "human_approval_status": decision, "approver": approver,
            "comments": comments, "revision_number": revision_number})

    def ambiguity_detected(self, *, question: str, source_reference: str = "",
                          interpretations: list[str] | None = None) -> None:
        """First-class STOP signal (spec §21) — the agent must not guess."""
        self._emit(AgentEvent.AMBIGUITY_DETECTED, {
            "question": question, "source_reference": source_reference,
            "possible_interpretations": interpretations or [], "status": "PENDING_BUSINESS_APPROVAL"})

    def output_generated(self, *, artifact_id: str = "", artifact_type: str = "") -> None:
        if artifact_id:
            self.ctx.artifact_id = artifact_id
        self._emit(AgentEvent.OUTPUT_GENERATED,
                   {"artifact_id": artifact_id or self.ctx.artifact_id, "artifact_type": artifact_type})


def _otel_attrs(d: dict[str, Any]) -> dict[str, Any]:
    """Flatten a payload to OTEL-safe scalar attributes."""
    out: dict[str, Any] = {}
    for k, v in d.items():
        if isinstance(v, (str, bool, int, float)):
            out[k] = v
        elif v is not None:
            out[k] = str(v)
    return out


@contextmanager
def agent_execution(ctx: AgentExecutionContext) -> Iterator[AgentTelemetry]:
    """Scope one agent execution: stamp IDs, open a span, emit start/complete/fail.

    Usage::

        ctx = AgentExecutionContext(agent_id="frd-generator", agent_version="1.4.2",
                                    workflow_id=wf, module_id="AUTH", rule_id="AA-15")
        with agent_execution(ctx) as tel:
            tel.context_retrieved(chunk_count=12)
            tel.prompt_built(prompt_version="v8")
            tel.record_llm_call(model_name=..., tokens_in=..., tokens_out=...)
            tel.output_generated(artifact_id=..., artifact_type="frd")
            tel.record_validation(status="PASS")
    """
    if not ctx.execution_id:
        ctx.execution_id = f"EXEC-{uuid.uuid4().hex[:12]}"
    if not ctx.workflow_id:
        ctx.workflow_id = rc.get_workflow_run_id()
    if not ctx.pipeline_execution_id:
        ctx.pipeline_execution_id = _get_pipeline_id()
    ctx.started_at = time.time()

    # scope correlation IDs so every downstream emit inherits them
    prev_agent = rc.get_agent_run_id()
    prev_wf = rc.get_workflow_run_id()
    rc.set_agent_run_id(ctx.execution_id)
    if ctx.workflow_id:
        rc.set_workflow_run_id(ctx.workflow_id)

    otel_cm = None
    span = None
    if _OTEL_AVAILABLE:  # pragma: no cover - only when dependency present
        try:
            otel_cm = _OTEL_TRACER.start_as_current_span(f"agent.{ctx.agent_id}")
            span = otel_cm.__enter__()
            sctx = span.get_span_context()
            ctx.trace_id = format(sctx.trace_id, "032x")
            ctx.span_id = format(sctx.span_id, "016x")
            for k, v in _otel_attrs(ctx.identity()).items():
                span.set_attribute(f"aidlc.{k}", v)
        except Exception:
            otel_cm = span = None
    if not ctx.trace_id:
        ctx.trace_id = uuid.uuid4().hex
    if not ctx.span_id:
        ctx.span_id = uuid.uuid4().hex[:16]

    tel = AgentTelemetry(ctx, otel_span=span)
    ctx.status = "running"
    tel._emit(AgentEvent.AGENT_STARTED, {"started_at": ctx.started_at})
    try:
        yield tel
        ctx.status = "completed"
        ctx.completed_at = time.time()
        tel._emit(AgentEvent.AGENT_COMPLETED,
                  {"completed_at": ctx.completed_at,
                   "duration_ms": round((ctx.completed_at - ctx.started_at) * 1000, 1)})
    except Exception as exc:
        ctx.status = "failed"
        ctx.completed_at = time.time()
        tel._emit(AgentEvent.AGENT_FAILED, {
            "error_type": type(exc).__name__, "error_message": str(exc)[:500],
            "duration_ms": round((ctx.completed_at - ctx.started_at) * 1000, 1)})
        if span is not None:  # pragma: no cover
            try:
                span.record_exception(exc)
            except Exception:
                pass
        raise
    finally:
        if otel_cm is not None:  # pragma: no cover
            try:
                otel_cm.__exit__(None, None, None)
            except Exception:
                pass
        rc.set_agent_run_id(prev_agent)
        rc.set_workflow_run_id(prev_wf)


def observability_available() -> dict[str, bool]:
    """Quick self-report of what the SDK can currently do (spec §45/§46)."""
    return {
        "telemetry_emit": True,
        "correlation_ids": True,
        "lifecycle_events": True,
        "opentelemetry_tracing": _OTEL_AVAILABLE,
    }
