"""Shared event-folding logic for every ClaudeRunner adapter.

Both adapters emit the same normalised `AgentEvent` stream, so accumulation into
a `RunResult` lives here once. If this were duplicated per adapter, the SDK and
CLI paths would drift and only one would be covered by tests.
"""

from __future__ import annotations

from app.agentic_platform.fe_core.ports.claude_runner import (
    AgentEvent,
    EventKind,
    RunResult,
    TerminationReason,
)

WRITE_TOOLS = frozenset({"Write", "Edit", "MultiEdit", "NotebookEdit"})

_DENIAL_MARKERS = ("permission", "not allowed", "denied", "requires approval")


def looks_like_denial(text: str) -> bool:
    lowered = (text or "").lower()
    return any(marker in lowered for marker in _DENIAL_MARKERS)


def fold_event(event: AgentEvent, result: RunResult) -> None:
    """Accumulate one normalised event into the run result."""
    if event.kind is EventKind.TEXT and event.text:
        result.text += event.text
    elif event.kind is EventKind.PLUGINS_LOADED:
        result.manifest_seen = True
        result.plugins_loaded = list(event.detail.get("plugins") or [])
        skills = set(event.detail.get("skills") or [])
        skills.update(event.detail.get("slash_commands") or [])
        result.skills_loaded = sorted(skills)
    elif event.kind is EventKind.TOOL_CALL and event.tool_name:
        result.tools_invoked.append(event.tool_name)
    elif event.kind is EventKind.TOOL_DENIED:
        result.tools_denied.append(event.tool_name or event.text or "unknown")
    elif event.kind is EventKind.FILE_WRITTEN and event.path:
        result.files_written.append(event.path)
    elif event.kind is EventKind.USAGE:
        result.input_tokens += int(event.detail.get("input_tokens") or 0)
        result.output_tokens += int(event.detail.get("output_tokens") or 0)
        result.cache_read_input_tokens += int(
            event.detail.get("cache_read_input_tokens") or 0)
        result.cache_creation_input_tokens += int(
            event.detail.get("cache_creation_input_tokens") or 0)
        cost = event.detail.get("cost_usd")
        if cost is not None:
            result.cost_usd = (result.cost_usd or 0.0) + float(cost)
        turns = event.detail.get("num_turns")
        if turns is not None:
            # The harness reports the running total, not a delta.
            result.num_turns = max(int(turns), result.num_turns or 0)
    elif event.kind is EventKind.SESSION_START:
        result.session_id = event.detail.get("session_id") or result.session_id
    elif event.kind is EventKind.SESSION_END:
        result.session_id = event.detail.get("session_id") or result.session_id
        subtype = str(event.detail.get("subtype") or "")
        if subtype == "error_max_turns":
            # The turn cap fired. Without this mapping the run looks like a
            # normal completion and a half-finished artefact gets persisted.
            result.terminated = TerminationReason.MAX_TURNS
            result.error = result.error or (
                f"stopped by max_turns cap"
                + (f" after {result.num_turns} turns" if result.num_turns else "")
            )
        elif event.detail.get("is_error"):
            result.error = event.text or "agent reported an error"
    elif event.kind is EventKind.ERROR:
        result.error = event.text
        if event.detail.get("termination") == TerminationReason.TIMEOUT.value:
            result.terminated = TerminationReason.TIMEOUT
    result.events.append(event)
