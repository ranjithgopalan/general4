"""ClaudeRunner port — the boundary that keeps SDK details out of product code.

PRD FR-016: "SDK-specific objects do not appear in route handlers or domain
models; a mock runner supports tests."

Everything in this module is plain dataclasses and a Protocol. No import of
`claude_agent_sdk`, no subprocess, no HTTP. Adapters live in
`apps/worker/worker_app/runners/` and normalise their transport into these
types before anything else sees them.

Two adapters are provided, selected by configuration (PRD 1.1: "Claude SDK —
use the current Claude Agent SDK through a server-side adapter"):

  * ``sdk``  -> claude_agent_sdk in-process
  * ``cli``  -> the Claude Code CLI as a subprocess

Both honour a per-stage tool allowlist and a permission policy. Neither passes
``--dangerously-skip-permissions``; PRD 5.4 prohibits it explicitly, and
FR-018 requires that "a denied tool call is recorded and cannot execute".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, AsyncIterator, Protocol, runtime_checkable


class PermissionPolicy(str, Enum):
    """How un-allowlisted tool calls are handled.

    ``BYPASS`` is deliberately absent. There is no value of this enum that
    disables permission checking, so no stage can be configured into a bypass.
    """

    # Allowlisted tools run; anything else is denied without prompting.
    # Correct default for non-interactive server execution: prompting would
    # hang forever, and bypassing is prohibited.
    DENY_UNLISTED = "deny_unlisted"
    # Allowlisted file edits are auto-accepted; other tools still denied.
    ACCEPT_EDITS = "accept_edits"
    # Read-only exploration; no writes at all.
    PLAN_ONLY = "plan_only"


class EventKind(str, Enum):
    """Normalised event stream. Maps to PRD FR-021 status events."""

    SESSION_START = "session_start"
    PLUGINS_LOADED = "plugins_loaded"
    TEXT = "text"
    THINKING = "thinking"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    TOOL_DENIED = "tool_denied"
    FILE_WRITTEN = "file_written"
    USAGE = "usage"
    ERROR = "error"
    SESSION_END = "session_end"


class TerminationReason(str, Enum):
    COMPLETED = "completed"
    MAX_TURNS = "max_turns"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    ERROR = "error"
    PLUGIN_LOAD_FAILED = "plugin_load_failed"


@dataclass(frozen=True)
class ToolPermission:
    """A stage's tool allowlist. Empty ``allow`` means no tools at all."""

    allow: tuple[str, ...] = ()
    deny: tuple[str, ...] = ()
    policy: PermissionPolicy = PermissionPolicy.DENY_UNLISTED

    def as_allow_list(self) -> list[str]:
        return list(self.allow)

    def as_deny_list(self) -> list[str]:
        return list(self.deny)


@dataclass(frozen=True)
class McpServerSpec:
    """An MCP server the agent may call. Transport-neutral."""

    name: str
    command: str
    args: tuple[str, ...] = ()
    cwd: str | None = None
    env: dict[str, str] = field(default_factory=dict)

    def to_mcp_config(self) -> dict[str, Any]:
        """The `mcpServers` JSON shape shared by the SDK and the CLI."""
        if not self.command and self.env.get("FE_TOOLS_HTTP_URL"):
            # remote streamable-http server (fe-tools on ECS)
            return {"type": "http", "url": self.env["FE_TOOLS_HTTP_URL"]}
        spec: dict[str, Any] = {
            "type": "stdio",
            "command": self.command,
            "args": list(self.args),
        }
        if self.cwd:
            spec["cwd"] = self.cwd
        if self.env:
            spec["env"] = dict(self.env)
        return spec


@dataclass(frozen=True)
class PluginSpec:
    """A GATHER plugin to load, by local directory path."""

    name: str
    path: Path
    required_skills: tuple[str, ...] = ()


@dataclass(frozen=True)
class RunLimits:
    """PRD FR-020: configurable turn, duration and retry limits."""

    max_turns: int = 120
    timeout_seconds: int = 3600
    max_retries: int = 0


@dataclass(frozen=True)
class RunRequest:
    """Everything an adapter needs. Carries no SDK or transport types."""

    prompt: str
    workspace: Path
    correlation_id: str

    model: str = "claude-opus-5"
    effort: str | None = "high"
    system_prompt_append: str | None = None

    plugins: tuple[PluginSpec, ...] = ()
    mcp_servers: tuple[McpServerSpec, ...] = ()
    permissions: ToolPermission = field(default_factory=ToolPermission)
    limits: RunLimits = field(default_factory=RunLimits)

    additional_dirs: tuple[Path, ...] = ()
    resume_session_id: str | None = None
    fork_session: bool = False
    workspace_id: str | None = None

    def required_plugin_names(self) -> list[str]:
        return [p.name for p in self.plugins]

    def required_skill_names(self) -> list[str]:
        return [s for p in self.plugins for s in p.required_skills]


@dataclass
class AgentEvent:
    """One normalised event. Safe to stream to authorized clients.

    PRD FR-021: "Clients receive ordered queued/running/waiting/failed/completed
    events without raw secrets or prompts." Adapters must not put prompt text or
    credentials in ``detail``.
    """

    kind: EventKind
    at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    text: str | None = None
    tool_name: str | None = None
    path: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class RunResult:
    """Outcome of one agent run, in domain terms only."""

    correlation_id: str
    terminated: TerminationReason = TerminationReason.COMPLETED

    text: str = ""
    files_written: list[str] = field(default_factory=list)
    tools_invoked: list[str] = field(default_factory=list)
    tools_denied: list[str] = field(default_factory=list)

    plugins_loaded: list[str] = field(default_factory=list)
    skills_loaded: list[str] = field(default_factory=list)
    # Whether a session-start manifest was seen at all. Distinct from an empty
    # `plugins_loaded`: "no manifest" means we could not observe loading, while
    # an empty manifest means loading was observed and found nothing. Conflating
    # them sends an operator debugging the wrong thing.
    manifest_seen: bool = False

    input_tokens: int = 0
    output_tokens: int = 0
    # Prompt-cache accounting. Cache reads are billed at ~10% of the input
    # rate, so `input_tokens` alone under-reports what was sent and
    # over-reports what was paid. Both are needed to prove caching works and
    # to reconcile `cost_usd`.
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    # Reported by the harness when available (CLI `result.total_cost_usd`);
    # None means "not reported", not "free".
    cost_usd: float | None = None
    # Agent turns actually consumed. Compared against RunLimits.max_turns it
    # tells whether the cap was the reason the run stopped.
    num_turns: int | None = None
    session_id: str | None = None

    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: str | None = None
    events: list[AgentEvent] = field(default_factory=list)

    # Provenance for PRD FR-019.
    runner: str = ""
    model: str = ""

    @property
    def total_tokens(self) -> int:
        """Billable-ish volume: uncached input + cached input + output."""
        return (self.input_tokens + self.cache_read_input_tokens
                + self.cache_creation_input_tokens + self.output_tokens)

    @property
    def cache_hit_ratio(self) -> float | None:
        """Share of input served from cache; None when no input was seen."""
        total_in = (self.input_tokens + self.cache_read_input_tokens
                    + self.cache_creation_input_tokens)
        if total_in <= 0:
            return None
        return self.cache_read_input_tokens / total_in

    @property
    def ok(self) -> bool:
        return self.terminated is TerminationReason.COMPLETED and not self.error

    @property
    def duration_seconds(self) -> float | None:
        if self.started_at and self.finished_at:
            return (self.finished_at - self.started_at).total_seconds()
        return None


class PluginVerificationError(RuntimeError):
    """A required plugin or skill did not load, so the run is untrustworthy.

    The SDK and the CLI both skip a missing plugin path silently. A stage that
    "succeeded" without its plugin produced output nobody should trust, so this
    is an error rather than a warning.
    """


class RunnerUnavailableError(RuntimeError):
    """The configured runner cannot execute in this environment."""


@runtime_checkable
class ClaudeRunner(Protocol):
    """The port. Implemented by SdkRunner, CliRunner and MockRunner."""

    name: str

    async def run(self, request: RunRequest) -> RunResult:
        """Execute to completion and return a normalised result."""
        ...

    def stream(self, request: RunRequest) -> AsyncIterator[AgentEvent]:
        """Execute, yielding normalised events as they occur."""
        ...

    async def preflight(self) -> tuple[bool, str]:
        """Cheap usability probe for /health. Never raises."""
        ...


def verify_plugins(
    result: RunResult, request: RunRequest, *, strict: bool = True
) -> None:
    """Assert every required plugin and skill actually loaded.

    Shared by all adapters so the check cannot be forgotten in one of them.
    """
    if not strict:
        return

    if request.plugins and not result.manifest_seen:
        raise PluginVerificationError(
            "No session-start plugin manifest was observed, so plugin loading "
            "could not be verified. Refusing to trust this run: both the SDK "
            "and the CLI skip missing plugin paths silently."
        )

    missing = [n for n in request.required_plugin_names() if n not in result.plugins_loaded]
    if missing:
        raise PluginVerificationError(
            f"Plugin(s) did not load: {', '.join(sorted(missing))}. "
            f"Loaded: {', '.join(sorted(result.plugins_loaded)) or '(none)'}. "
            "The most common cause is a path that does not exist."
        )

    known = set(result.skills_loaded)
    missing_skills = [s for s in request.required_skill_names() if s not in known]
    if missing_skills:
        raise PluginVerificationError(
            f"Skill(s) did not load: {', '.join(sorted(missing_skills))}. "
            f"Loaded: {', '.join(sorted(known)) or '(none)'}. Check that each is "
            "skills/<name>/SKILL.md and declared in plugin.json."
        )
