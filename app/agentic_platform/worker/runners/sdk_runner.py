"""Claude Agent SDK adapter for the ClaudeRunner port.

PRD 1.1: "Use the current Claude Agent SDK through a server-side adapter... the
adapter prevents SDK details from leaking into product code." Every SDK type is
converted to a normalised `AgentEvent` here and nowhere else.

Environment caveat this adapter must guard against
--------------------------------------------------
`import claude_agent_sdk` can SUCCEED while resolving to a stub. This estate
ships one at C:\\CTS_Git\\kb\\claude_agent_sdk\\ whose own docstring says the
stubs are "NOT sufficient for: Actually calling the SDK's query() function (use
CLI mode instead)". Because the import succeeds, a naive readiness check reports
a healthy SDK and the failure only appears mid-run after tokens are spent.
`preflight()` detects the stub so the factory can fall back to the CLI.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator

from app.agentic_platform.fe_core.ports.claude_runner import (
    AgentEvent,
    EventKind,
    PermissionPolicy,
    RunRequest,
    RunResult,
    RunnerUnavailableError,
    TerminationReason,
    verify_plugins,
)
from app.agentic_platform.worker.runners._common import WRITE_TOOLS, fold_event, looks_like_denial

logger = logging.getLogger(__name__)

# PermissionPolicy -> SDK permission_mode. `bypassPermissions` is unreachable.
_PERMISSION_MODE = {
    PermissionPolicy.DENY_UNLISTED: "dontAsk",
    PermissionPolicy.ACCEPT_EDITS: "acceptEdits",
    PermissionPolicy.PLAN_ONLY: "plan",
}


def sdk_is_stub() -> bool:
    """True when `claude_agent_sdk` is a stub rather than the real package."""
    try:
        import claude_agent_sdk as sdk  # type: ignore
    except ImportError:
        return False
    if getattr(sdk, "_real", "MISSING") is None:
        return True
    return "stub" in ((getattr(sdk, "__doc__", "") or "")[:400].lower())


def sdk_location() -> str:
    try:
        import claude_agent_sdk as sdk  # type: ignore

        return str(getattr(sdk, "__file__", "<unknown>"))
    except ImportError:
        return "<not importable>"


class SdkRunner:
    """Runs a stage in-process through claude_agent_sdk."""

    name = "sdk"

    def __init__(self, verify_plugins_strict: bool = True):
        self._strict = verify_plugins_strict

    async def preflight(self) -> tuple[bool, str]:
        try:
            import claude_agent_sdk  # noqa: F401
        except ImportError:
            return False, "claude_agent_sdk is not installed"
        if sdk_is_stub():
            return False, (
                f"claude_agent_sdk at {sdk_location()} is a STUB and cannot execute "
                "query(); use the CLI runner instead"
            )
        return True, f"claude_agent_sdk at {sdk_location()}"

    def _options(self, request: RunRequest) -> Any:
        from claude_agent_sdk import ClaudeAgentOptions  # type: ignore

        kwargs: dict[str, Any] = {
            "cwd": str(request.workspace),
            "model": request.model,
            "max_turns": request.limits.max_turns,
            "permission_mode": _PERMISSION_MODE[request.permissions.policy],
            # FR-018: stage-specific allowlist. Passed even when empty.
            "allowed_tools": request.permissions.as_allow_list(),
            "plugins": [
                {"type": "local", "path": str(p.path)} for p in request.plugins
            ],
            # Never inherit the developer machine's settings into a server run.
            "setting_sources": [],
        }
        if request.permissions.as_deny_list():
            kwargs["disallowed_tools"] = request.permissions.as_deny_list()
        if request.effort:
            kwargs["effort"] = request.effort
        if request.mcp_servers:
            kwargs["mcp_servers"] = {
                s.name: s.to_mcp_config() for s in request.mcp_servers
            }
            kwargs["strict_mcp_config"] = True
        if request.additional_dirs:
            kwargs["add_dirs"] = [str(d) for d in request.additional_dirs]
        if request.system_prompt_append:
            kwargs["system_prompt"] = {
                "type": "preset",
                "preset": "claude_code",
                "append": request.system_prompt_append,
            }
        if request.resume_session_id:
            kwargs["resume"] = request.resume_session_id
            kwargs["fork_session"] = request.fork_session
        return ClaudeAgentOptions(**kwargs)

    async def stream(self, request: RunRequest) -> AsyncIterator[AgentEvent]:
        ok, detail = await self.preflight()
        if not ok:
            raise RunnerUnavailableError(detail)

        from claude_agent_sdk import query  # type: ignore

        request.workspace.mkdir(parents=True, exist_ok=True)
        options = self._options(request)

        logger.info(
            "SDK run start: correlation=%s plugins=%s allow=%s mode=%s",
            request.correlation_id,
            [p.name for p in request.plugins],
            request.permissions.as_allow_list(),
            _PERMISSION_MODE[request.permissions.policy],
        )

        async for message in query(prompt=request.prompt, options=options):
            for event in _to_events(message):
                yield event

    async def run(self, request: RunRequest) -> RunResult:
        result = RunResult(
            correlation_id=request.correlation_id,
            runner=self.name,
            model=request.model,
            started_at=datetime.now(timezone.utc),
        )
        async for event in self.stream(request):
            fold_event(event, result)
        result.finished_at = datetime.now(timezone.utc)

        if result.error and result.terminated is TerminationReason.COMPLETED:
            result.terminated = TerminationReason.ERROR
        try:
            verify_plugins(result, request, strict=self._strict)
        except Exception:
            result.terminated = TerminationReason.PLUGIN_LOAD_FAILED
            raise

        result.files_written = list(dict.fromkeys(result.files_written))
        return result


def _to_events(message: Any) -> list[AgentEvent]:
    """Convert one SDK message into normalised events.

    Duck-typed rather than isinstance-checked so the same code survives SDK
    version differences and shim types.
    """
    events: list[AgentEvent] = []

    # SystemMessage(subtype="init") carries the plugin/skill manifest.
    if getattr(message, "subtype", None) == "init":
        data = getattr(message, "data", None) or {}
        if isinstance(data, dict):
            plugins = [
                p.get("name") if isinstance(p, dict) else str(p)
                for p in (data.get("plugins") or [])
            ]
            events.append(AgentEvent(
                kind=EventKind.SESSION_START,
                detail={"session_id": data.get("session_id")},
            ))
            events.append(AgentEvent(
                kind=EventKind.PLUGINS_LOADED,
                detail={
                    "plugins": [p for p in plugins if p],
                    "skills": list(data.get("skills") or []),
                    "slash_commands": list(data.get("slash_commands") or []),
                },
            ))
        return events

    content = getattr(message, "content", None)
    if isinstance(content, str):
        events.append(AgentEvent(kind=EventKind.TEXT, text=content))
    elif content is not None and not isinstance(content, (bytes,)):
        try:
            blocks = list(content)
        except TypeError:
            blocks = []
        for block in blocks:
            text = getattr(block, "text", None)
            if text is None and isinstance(block, dict):
                text = block.get("text")
            name = getattr(block, "name", None)
            if name is None and isinstance(block, dict):
                name = block.get("name")
            tool_input = getattr(block, "input", None)
            if tool_input is None and isinstance(block, dict):
                tool_input = block.get("input")
            is_error = getattr(block, "is_error", None)
            if is_error is None and isinstance(block, dict):
                is_error = block.get("is_error")

            if name:
                events.append(AgentEvent(kind=EventKind.TOOL_CALL, tool_name=str(name)))
                if str(name) in WRITE_TOOLS and isinstance(tool_input, dict):
                    path = tool_input.get("file_path") or tool_input.get("path")
                    if path:
                        events.append(AgentEvent(
                            kind=EventKind.FILE_WRITTEN,
                            tool_name=str(name), path=str(path),
                        ))
            elif isinstance(text, str) and text:
                if is_error and looks_like_denial(text):
                    events.append(AgentEvent(kind=EventKind.TOOL_DENIED, text=text[:300]))
                else:
                    events.append(AgentEvent(kind=EventKind.TEXT, text=text))

    usage = getattr(message, "usage", None)
    if usage is not None:
        def _u(name: str):
            if isinstance(usage, dict):
                return usage.get(name)
            return getattr(usage, name, None)
        detail: dict[str, Any] = {
            "input_tokens": _u("input_tokens") or 0,
            "output_tokens": _u("output_tokens") or 0,
            "cache_read_input_tokens": _u("cache_read_input_tokens") or 0,
            "cache_creation_input_tokens": _u("cache_creation_input_tokens") or 0,
        }
        # ResultMessage carries the run totals; per-message usage does not.
        cost = getattr(message, "total_cost_usd", None)
        if cost is not None:
            detail["cost_usd"] = cost
        turns = getattr(message, "num_turns", None)
        if turns is not None:
            detail["num_turns"] = turns
        events.append(AgentEvent(kind=EventKind.USAGE, detail=detail))

    session_id = getattr(message, "session_id", None)
    if session_id is not None:
        events.append(AgentEvent(
            kind=EventKind.SESSION_END,
            detail={
                "session_id": str(session_id),
                "is_error": bool(getattr(message, "is_error", False)),
                # `error_max_turns` is how the SDK reports the turn cap firing.
                "subtype": getattr(message, "subtype", None),
            },
            text=(getattr(message, "result", None)
                  if getattr(message, "is_error", False) else None),
        ))
    return events
