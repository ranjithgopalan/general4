"""
Per-request context carried via Python ContextVar.

Set once at the start of each request so deep callees (telemetry, LLM wrappers,
grounding gate) can read correlation/session/user/persona without threading them
through every call. asyncio coroutines inherit the caller's context; child tasks
spawned via create_task/gather receive a *copy* (writes are isolated).

Traceability IDs (agent_run_id, tool_call_id) are set per-agent-run and
per-tool-call respectively, enabling end-to-end traceability without parameter
drilling across the entire lifecycle stack.
"""

from __future__ import annotations

from contextvars import ContextVar

_correlation_id: ContextVar[str] = ContextVar("aidlc_correlation_id", default="")
_session_id: ContextVar[str] = ContextVar("aidlc_session_id", default="")
_userid: ContextVar[str] = ContextVar("aidlc_userid", default="unknown")
_persona: ContextVar[str] = ContextVar("aidlc_persona", default="")
_agent_run_id: ContextVar[str] = ContextVar("aidlc_agent_run_id", default="")
_tool_call_id: ContextVar[str] = ContextVar("aidlc_tool_call_id", default="")
_workflow_run_id: ContextVar[str] = ContextVar("aidlc_workflow_run_id", default="")
# Cross-repository trace ID propagated from the UI via X-Pipeline-ID header
_pipeline_execution_id: ContextVar[str] = ContextVar("aidlc_pipeline_execution_id", default="")


def set_correlation_id(correlation_id: str) -> None:
    """Set only the correlation ID for this async context.

    Called from the raw ASGI middleware (ActorContextMiddleware) so the value
    propagates to route handlers and SSE generators.  Separate from
    set_request_context so the service layer can set session/user/persona later
    without overwriting the ID that was already established at the HTTP boundary.
    """
    _correlation_id.set(str(correlation_id or "").strip())


def set_request_context(correlation_id: str, session_id: str, userid: str, persona: str = "") -> None:
    """Populate context for downstream callees. Call once at request start.

    Does NOT overwrite the correlation ID when it has already been set by the
    ASGI middleware (i.e. when the caller passes an empty string).  This keeps
    the ID that was stamped from the inbound X-Request-ID header intact.
    """
    if correlation_id:
        _correlation_id.set(correlation_id)
    _session_id.set(session_id)
    _userid.set(str(userid or "").strip() or "unknown")
    _persona.set(str(persona or "").strip())


def get_correlation_id() -> str:
    return _correlation_id.get()


def get_session_id() -> str:
    return _session_id.get()


def get_userid() -> str:
    return _userid.get()


def get_persona() -> str:
    return _persona.get()


def set_agent_run_id(agent_run_id: str) -> None:
    """Set the current agent run ID for this async context (call once per stage execution)."""
    _agent_run_id.set(agent_run_id)


def get_agent_run_id() -> str:
    """Return the current agent run ID (empty string when not inside an agent execution)."""
    return _agent_run_id.get()


def set_tool_call_id(tool_call_id: str) -> None:
    """Set the current tool call ID (call once per individual tool invocation)."""
    _tool_call_id.set(tool_call_id)


def get_tool_call_id() -> str:
    """Return the current tool call ID (empty string when not inside a tool call)."""
    return _tool_call_id.get()


def set_workflow_run_id(workflow_run_id: str) -> None:
    """Set the workflow run ID for this async context (one stable ID per BRD→delivery run)."""
    _workflow_run_id.set(str(workflow_run_id or "").strip())


def get_workflow_run_id() -> str:
    """Return the current workflow run ID (empty string when not inside a workflow)."""
    return _workflow_run_id.get()


def set_pipeline_execution_id(pipeline_execution_id: str) -> None:
    """Set the cross-repository pipeline execution ID from the X-Pipeline-ID header."""
    _pipeline_execution_id.set(str(pipeline_execution_id or "").strip())


def get_pipeline_execution_id() -> str:
    """Return the pipeline execution ID shared across UI → Agents → Plugins."""
    return _pipeline_execution_id.get()
