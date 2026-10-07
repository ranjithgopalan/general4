"""In-memory ClaudeRunner for tests.

PRD FR-016 requires this explicitly: "a mock runner supports tests". Without it,
every pipeline test would need a live model, and the approval/state-machine
rules -- which is where the governance value is -- could not be tested at all.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import AsyncIterator

from app.agentic_platform.fe_core.ports.claude_runner import (
    AgentEvent,
    EventKind,
    RunRequest,
    RunResult,
    TerminationReason,
    verify_plugins,
)
from app.agentic_platform.worker.runners._common import fold_event


class MockRunner:
    """Scriptable runner. Defaults to a successful run that loads every
    requested plugin and writes one file per declared output."""

    name = "mock"

    def __init__(
        self,
        *,
        text: str = "mock run complete",
        files: list[str] | None = None,
        load_plugins: bool = True,
        deny_tools: list[str] | None = None,
        fail_with: str | None = None,
        terminated: TerminationReason = TerminationReason.COMPLETED,
        input_tokens: int = 100,
        output_tokens: int = 50,
        verify_plugins_strict: bool = True,
    ):
        self._text = text
        self._files = files or []
        self._load_plugins = load_plugins
        self._deny_tools = deny_tools or []
        self._fail_with = fail_with
        self._terminated = terminated
        self._in = input_tokens
        self._out = output_tokens
        self._strict = verify_plugins_strict
        self.calls: list[RunRequest] = []

    async def preflight(self) -> tuple[bool, str]:
        return True, "mock runner (no external dependency)"

    async def stream(self, request: RunRequest) -> AsyncIterator[AgentEvent]:
        self.calls.append(request)
        yield AgentEvent(
            kind=EventKind.SESSION_START,
            detail={"session_id": f"mock-{request.correlation_id}"},
        )
        # Honour the plugin contract so verification can be tested both ways.
        yield AgentEvent(
            kind=EventKind.PLUGINS_LOADED,
            detail={
                "plugins": request.required_plugin_names() if self._load_plugins else [],
                "skills": request.required_skill_names() if self._load_plugins else [],
                "slash_commands": [],
            },
        )
        if self._text:
            yield AgentEvent(kind=EventKind.TEXT, text=self._text)
        for tool in self._deny_tools:
            yield AgentEvent(
                kind=EventKind.TOOL_DENIED,
                tool_name=tool,
                text=f"permission denied: {tool} is not in the stage allowlist",
            )
        # An obedient mock: when no files were configured, honour the output
        # contract in the prompt so the deliverable check behaves as with a real
        # agent -- "named exactly: `spec.md`" for docs stages, SUMMARY.md + a stub
        # source file for code stages.
        files = list(self._files)
        if not files:
            import re  # noqa: PLC0415
            m = re.search(r"named\s+exactly:\s*(.+?)\.\s", request.prompt or "", flags=re.S)
            if m:
                files = re.findall(r"`([\w.\-]+\.md)`", m.group(1))
            elif "SUMMARY.md" in (request.prompt or ""):
                files = ["SUMMARY.md", "src/generated_stub.txt"]
        for path in files:
            target = request.workspace / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(f"# mock output for {request.correlation_id}\n", encoding="utf-8")
            yield AgentEvent(
                kind=EventKind.FILE_WRITTEN, tool_name="Write", path=str(target)
            )
        yield AgentEvent(
            kind=EventKind.USAGE,
            detail={"input_tokens": self._in, "output_tokens": self._out},
        )
        if self._fail_with:
            yield AgentEvent(kind=EventKind.ERROR, text=self._fail_with)
        yield AgentEvent(
            kind=EventKind.SESSION_END,
            detail={
                "session_id": f"mock-{request.correlation_id}",
                "is_error": bool(self._fail_with),
            },
            text=self._fail_with,
        )

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

        if self._terminated is not TerminationReason.COMPLETED:
            result.terminated = self._terminated
        elif result.error:
            result.terminated = TerminationReason.ERROR

        try:
            verify_plugins(result, request, strict=self._strict)
        except Exception:
            result.terminated = TerminationReason.PLUGIN_LOAD_FAILED
            raise
        return result
