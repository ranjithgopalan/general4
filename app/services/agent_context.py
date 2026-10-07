"""
AgentExecutionContext — shared execution context for agent observability.

Each agent invocation creates one AgentExecutionContext at entry and passes it
through the call chain. The context binds together all identifiers needed to
emit correlated telemetry events across the full agent lifecycle:

    AGENT_STARTED → (LLM_CALL*) → (TOOL_CALL*) → AGENT_RUN

Usage in a ReAct agent:
    ctx = AgentExecutionContext.from_request(agent_name="brd", stage="brd")
    ctx.emit_started()
    ...
    ctx.emit_completed(iterations=N, tokens_in=T, tokens_out=T, status="success")
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from app.services.telemetry import EventType, TelemetryEvent, _sinks, get_settings_enabled
from app.utils.logging import log
from app.utils.request_context import (
    get_agent_run_id,
    get_correlation_id,
    get_persona,
    get_session_id,
    get_userid,
    get_workflow_run_id,
)


@dataclass
class AgentExecutionContext:
    """Immutable snapshot of all identifiers needed to emit lifecycle telemetry for one agent run."""

    agent_name: str
    stage: str
    agent_run_id: str
    correlation_id: str
    workflow_run_id: str
    session_id: str
    userid: str
    persona: str

    # Traceability fields — populated by caller when the rule/module context is known
    module_id: str = ""
    feature_id: str = ""
    rule_ids: list[str] = field(default_factory=list)
    artifact_id: str = ""

    # Version fields — populated from config/env at agent startup
    prompt_version: str = ""
    agent_version: str = ""

    # Retry tracking — incremented by @observe_agent or tenacity hook
    retry_count: int = 0

    # Internal — set by emit_started()
    _started_at: float = field(default=0.0, repr=False)

    # ── Factory ──────────────────────────────────────────────────────────────

    @classmethod
    def from_request(
        cls,
        *,
        agent_name: str,
        stage: str,
        module_id: str = "",
        feature_id: str = "",
        rule_ids: list[str] | None = None,
        artifact_id: str = "",
        prompt_version: str = "",
        agent_version: str = "",
    ) -> "AgentExecutionContext":
        """Build a context from the current ContextVar state (must be called inside a request)."""
        return cls(
            agent_name=agent_name,
            stage=stage,
            agent_run_id=get_agent_run_id() or str(uuid.uuid4()),
            correlation_id=get_correlation_id(),
            workflow_run_id=get_workflow_run_id(),
            session_id=get_session_id(),
            userid=get_userid(),
            persona=get_persona(),
            module_id=module_id,
            feature_id=feature_id,
            rule_ids=rule_ids or [],
            artifact_id=artifact_id,
            prompt_version=prompt_version,
            agent_version=agent_version,
        )

    # ── Emit helpers ─────────────────────────────────────────────────────────

    def emit_started(self) -> None:
        """Emit AGENT_STARTED. Call at the very beginning of agent execution."""
        self._started_at = time.time()
        self._emit(
            EventType.AGENT_STARTED,
            {
                "agent_name": self.agent_name,
                "stage": self.stage,
                "module_id": self.module_id,
                "feature_id": self.feature_id,
                "rule_ids": self.rule_ids,
                "artifact_id": self.artifact_id,
                "prompt_version": self.prompt_version,
                "agent_version": self.agent_version,
            },
        )

    def emit_completed(
        self,
        *,
        iterations: int,
        tokens_in: int,
        tokens_out: int,
        status: str,
        latency_ms: float | None = None,
    ) -> None:
        """Emit AGENT_RUN. Call when the agent finishes (success or failure)."""
        elapsed = latency_ms if latency_ms is not None else (
            (time.time() - self._started_at) * 1000 if self._started_at else 0.0
        )
        self._emit(
            EventType.AGENT_RUN,
            {
                "agent_name": self.agent_name,
                "agent_run_id": self.agent_run_id,
                "stage": self.stage,
                "persona": self.persona,
                "iterations": iterations,
                "status": status,
                "latency_ms": round(elapsed, 1),
                "tokens_in": tokens_in,
                "tokens_out": tokens_out,
                "retry_count": self.retry_count,
                "module_id": self.module_id,
                "feature_id": self.feature_id,
                "rule_ids": self.rule_ids,
                "artifact_id": self.artifact_id,
                "prompt_version": self.prompt_version,
                "agent_version": self.agent_version,
            },
        )

    def emit_retry(self, *, attempt: int, reason: str) -> None:
        """Emit AGENT_RETRY. Call from tenacity retry callback."""
        self.retry_count = attempt
        self._emit(
            EventType.AGENT_RETRY,
            {
                "agent_name": self.agent_name,
                "stage": self.stage,
                "attempt": attempt,
                "reason": reason,
                "module_id": self.module_id,
                "artifact_id": self.artifact_id,
            },
        )

    def emit_validation(self, *, status: str, dimension: str, details: dict[str, Any] | None = None) -> None:
        """Emit VALIDATION_COMPLETED. Call after agent output is validated."""
        self._emit(
            EventType.VALIDATION_COMPLETED,
            {
                "agent_name": self.agent_name,
                "stage": self.stage,
                "validation_status": status,
                "dimension": dimension,
                "module_id": self.module_id,
                "rule_ids": self.rule_ids,
                "artifact_id": self.artifact_id,
                **(details or {}),
            },
        )

    # ── Internal ─────────────────────────────────────────────────────────────

    def _emit(self, event_type: str, payload: dict[str, Any]) -> None:
        if not get_settings_enabled():
            return
        event = TelemetryEvent(
            event_type=event_type,
            payload=payload,
            correlation_id=self.correlation_id,
            agent_run_id=self.agent_run_id,
            workflow_run_id=self.workflow_run_id,
            session_id=self.session_id,
            userid=self.userid,
            persona=self.persona,
            ts=time.time(),
        )
        for sink in _sinks:
            try:
                sink(event)
            except Exception as exc:
                log.warning(f"[agent_context] sink failed for {event_type}: {exc}")
