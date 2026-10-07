"""Claude Code CLI adapter for the ClaudeRunner port.

PRD 5.4 prohibits reusing the existing helper that "shells out to a hard-coded
Claude CLI path with --dangerously-skip-permissions". This adapter is a clean
implementation of that transport:

  * the binary comes from configuration, never a hard-coded path
  * ``--dangerously-skip-permissions`` is NEVER passed; the permission mode is
    derived from the stage's PermissionPolicy, and there is no enum value that
    maps to ``bypassPermissions``
  * ``--allowedTools`` / ``--disallowedTools`` carry the per-stage allowlist,
    satisfying FR-018
  * ``--plugin-dir`` loads the GATHER plugins for the session only
  * ``--mcp-config`` + ``--strict-mcp-config`` expose exactly the KB MCP server
    and nothing inherited from the developer's machine

Verified against `claude --help` (Claude Code CLI): --plugin-dir, --mcp-config,
--strict-mcp-config, --allowedTools, --disallowedTools, --permission-mode,
--system-prompt, --append-system-prompt, --output-format stream-json,
--add-dir, --resume, --fork-session, --session-id, --model, --max-turns.

Windows notes (learned the hard way elsewhere in this estate):
  * a ``.cmd``/``.bat`` shim must be invoked through ``cmd.exe /c``
  * ``CLAUDECODE`` must be removed from the child environment, or the CLI
    refuses to start with a nested-session error
  * a long system prompt must go via a file, not argv, to stay under the 32K
    ``CreateProcess`` limit -- here we use ``--settings`` free argv instead by
    keeping the appended prompt short and passing longer text in the prompt body
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import platform
import shutil
import subprocess
import threading
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
from ._common import fold_event as _fold_event  # shared accumulator — no per-adapter copy

logger = logging.getLogger(__name__)

# PermissionPolicy -> CLI --permission-mode. `bypassPermissions` is intentionally
# unreachable: no PermissionPolicy member maps to it.
_PERMISSION_MODE = {
    PermissionPolicy.DENY_UNLISTED: "dontAsk",
    PermissionPolicy.ACCEPT_EDITS: "acceptEdits",
    PermissionPolicy.PLAN_ONLY: "plan",
}

_SENTINEL = object()


class CliRunner:
    """Runs a stage by driving the Claude Code CLI as a subprocess."""

    name = "cli"

    # Per-binary cache of "does this CLI accept flag X". Shared across
    # instances so a worker probes each flag once per process, not per run.
    _flag_support: dict[tuple[str, str], bool] = {}

    def __init__(self, binary: str | None = None, verify_plugins_strict: bool = True,
                 flag_support: dict[str, bool] | None = None):
        self._binary = binary or os.getenv("CLAUDE_CLI_PATH") or shutil.which("claude")
        self._strict = verify_plugins_strict
        # Explicit overrides (tests, or an operator who knows the CLI version)
        # win over probing.
        for flag, ok in (flag_support or {}).items():
            self._flag_support[(str(self._binary), flag)] = ok

    def supports_flag(self, flag: str) -> bool:
        """True when the CLI's argument parser accepts ``flag``.

        Probed by passing the flag together with a deliberately unknown option:
        commander reports the *first* unknown option, so an error naming our
        sentinel means ``flag`` was accepted, an error naming ``flag`` means it
        was not. No model call is made. Result cached per (binary, flag).
        """
        key = (str(self._binary), flag)
        if key in self._flag_support:
            return self._flag_support[key]
        supported = False
        binary = self._binary
        resolved = binary if binary and os.path.exists(binary) else (shutil.which(binary) if binary else None)
        if resolved:
            sentinel = "--fe-probe-unknown-option"
            sample = ["low"] if flag in ("--effort",) else []
            try:
                proc = subprocess.run(
                    self._wrap([resolved, "--print", flag, *sample, sentinel, "probe"]),
                    capture_output=True, text=True, timeout=30, env=self._child_env(),
                )
                err = (proc.stderr or "") + (proc.stdout or "")
                supported = sentinel in err and f"'{flag}'" not in err
            except Exception as exc:  # noqa: BLE001
                logger.debug("flag probe for %s failed: %s", flag, exc)
        self._flag_support[key] = supported
        return supported

    # -- discovery --------------------------------------------------------
    @property
    def binary(self) -> str:
        if not self._binary:
            raise RunnerUnavailableError(
                "Claude Code CLI not found. Set CLAUDE_CLI_PATH or put `claude` "
                "on PATH. (`npm install -g @anthropic-ai/claude-code`)"
            )
        return self._binary

    async def preflight(self) -> tuple[bool, str]:
        try:
            binary = self.binary
        except RunnerUnavailableError as exc:
            return False, str(exc)
        try:
            proc = await asyncio.to_thread(
                subprocess.run,
                self._wrap([binary, "--version"]),
                capture_output=True, text=True, timeout=30,
                env=self._child_env(),   # strip CLAUDECODE so nested-session guard doesn't fire
            )
        except Exception as exc:  # noqa: BLE001
            return False, f"CLI probe failed: {exc}"
        if proc.returncode != 0:
            return False, f"`claude --version` exited {proc.returncode}: {proc.stderr[:200]}"
        effort = "effort flag supported" if self.supports_flag("--effort") else \
            "no --effort flag (effort settings ignored on this CLI)"
        return True, f"{binary} ({proc.stdout.strip()[:60]}; {effort})"

    # -- argv -------------------------------------------------------------
    def _wrap(self, cmd: list[str]) -> list[str]:
        """Launch the CLI while avoiding cmd.exe's 8191-char command-line cap.

        On Windows an npm-installed ``claude`` is a ``.cmd`` shim; subprocess
        cannot exec it without a shell, so historically we wrapped it in
        ``cmd.exe /c``. But cmd.exe caps the command line at 8191 chars -- a
        stage with a large system prompt overflowed it and the child never
        launched ("The command line is too long"). Resolve the shim to the real
        ``claude.exe`` (higher 32K Win32 cap) when we can; fall back to cmd.exe.
        """
        if platform.system() == "Windows" and cmd[0].lower().endswith((".cmd", ".bat")):
            exe = self._resolve_windows_exe(cmd[0])
            if exe is not None:
                return [exe, *cmd[1:]]
            return ["cmd.exe", "/c", *cmd]
        return cmd

    @staticmethod
    def _resolve_windows_exe(shim_path: str) -> str | None:
        """Resolve an npm ``claude.cmd`` shim to the ``claude.exe`` it wraps.

        The shim sits next to ``node_modules/@anthropic-ai/claude-code/bin/claude.exe``.
        Returns None when the .exe is absent so the caller falls back to cmd.exe.
        """
        exe = (Path(shim_path).parent / "node_modules" / "@anthropic-ai"
               / "claude-code" / "bin" / "claude.exe")
        return str(exe) if exe.is_file() else None

    def build_argv(self, request: RunRequest, mcp_config_path: Path | None) -> list[str]:
        argv: list[str] = [
            self.binary,
            "--print",
            "--output-format", "stream-json",
            "--verbose",
            "--model", request.model,
            "--max-turns", str(request.limits.max_turns),
            "--permission-mode", _PERMISSION_MODE[request.permissions.policy],
        ]

        # FR-018: stage-specific allowlist. An empty allowlist is meaningful --
        # it means "no tools" -- so pass it explicitly rather than omitting.
        argv += ["--allowedTools", ",".join(request.permissions.as_allow_list())]
        if request.permissions.as_deny_list():
            argv += ["--disallowedTools", ",".join(request.permissions.as_deny_list())]

        for plugin in request.plugins:
            argv += ["--plugin-dir", str(plugin.path)]

        if mcp_config_path is not None:
            # --strict-mcp-config ignores every other MCP source, so the agent
            # sees exactly the servers we granted and nothing from the
            # developer's machine.
            argv += ["--mcp-config", str(mcp_config_path), "--strict-mcp-config"]

        # Effort is a first-class cost/quality dial, but the flag only exists on
        # newer CLIs (2.0.55 rejects it with "unknown option"). Probe once per
        # binary; on an old CLI log and continue rather than fail every run.
        if request.effort:
            if self.supports_flag("--effort"):
                argv += ["--effort", request.effort]
            else:
                logger.warning(
                    "CLI %s does not support --effort; effort=%s not applied "
                    "(upgrade @anthropic-ai/claude-code to control it)",
                    self._binary, request.effort,
                )

        if request.system_prompt_append:
            # A large system prompt passed inline overflows the Windows command-line
            # cap (32K, or 8K under cmd.exe) so the child never launches ("The command
            # line is too long"). Write it to a file in the worktree and pass the short
            # path via --append-system-prompt-file instead -- unbounded by prompt size.
            prompt_file = request.workspace / ".append-system-prompt.txt"
            prompt_file.write_text(request.system_prompt_append, encoding="utf-8")
            argv += ["--append-system-prompt-file", str(prompt_file)]
        for extra in request.additional_dirs:
            argv += ["--add-dir", str(extra)]
        if request.resume_session_id:
            argv += ["--resume", request.resume_session_id]
            if request.fork_session:
                argv.append("--fork-session")

        return self._wrap(argv)

    def _child_env(self) -> dict[str, str]:
        env = dict(os.environ)
        # Without this the CLI aborts with a nested-session error when the
        # parent process was itself started by Claude Code.
        env.pop("CLAUDECODE", None)
        env.pop("CLAUDE_CODE_ENTRYPOINT", None)
        # CLAUDE_CLI_USE=true: strip the Anthropic API key so the CLI uses
        # the subscription session from `claude auth login` instead.
        # Required when the API-key workspace rate-limit is 0 (e.g. an
        # enterprise console policy) or when billing is via Claude.ai plan.
        if os.getenv("CLAUDE_CLI_USE", "").lower() in ("1", "true", "yes"):
            env.pop("ANTHROPIC_API_KEY", None)
            env.pop("ANTHROPIC_AUTH_TOKEN", None)
            logger.debug(
                "CLAUDE_CLI_USE=true: ANTHROPIC_API_KEY stripped from CLI "
                "subprocess env; CLI will use the `claude auth login` session."
            )
        return env

    # -- execution --------------------------------------------------------
    async def stream(self, request: RunRequest) -> AsyncIterator[AgentEvent]:
        request.workspace.mkdir(parents=True, exist_ok=True)
        mcp_path = _write_mcp_config(request)
        argv = self.build_argv(request, mcp_path)

        logger.info(
            "CLI run start: correlation=%s plugins=%s allow=%s mode=%s",
            request.correlation_id,
            [p.name for p in request.plugins],
            request.permissions.as_allow_list(),
            _PERMISSION_MODE[request.permissions.policy],
        )

        queue: asyncio.Queue[Any] = asyncio.Queue()
        loop = asyncio.get_running_loop()

        def pump() -> None:
            """Read NDJSON in a thread; Windows asyncio subprocess is fragile."""
            proc = None
            try:
                proc = subprocess.Popen(
                    argv,
                    cwd=str(request.workspace),
                    env=self._child_env(),
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                )
                assert proc.stdin is not None and proc.stdout is not None
                proc.stdin.write(request.prompt)
                proc.stdin.close()
                for line in proc.stdout:
                    line = line.strip()
                    if line:
                        loop.call_soon_threadsafe(queue.put_nowait, line)
                proc.wait(timeout=request.limits.timeout_seconds)
                if proc.returncode != 0:
                    err = (proc.stderr.read() if proc.stderr else "")[:600]
                    loop.call_soon_threadsafe(
                        queue.put_nowait,
                        {"__error__": f"CLI exited {proc.returncode}: {err}"},
                    )
            except subprocess.TimeoutExpired:
                if proc:
                    proc.kill()
                loop.call_soon_threadsafe(
                    queue.put_nowait, {"__timeout__": request.limits.timeout_seconds}
                )
            except Exception as exc:  # noqa: BLE001
                loop.call_soon_threadsafe(queue.put_nowait, {"__error__": str(exc)})
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, _SENTINEL)

        threading.Thread(target=pump, name="claude-cli-pump", daemon=True).start()

        while True:
            item = await queue.get()
            if item is _SENTINEL:
                break
            if isinstance(item, dict) and "__error__" in item:
                yield AgentEvent(kind=EventKind.ERROR, text=item["__error__"])
                continue
            if isinstance(item, dict) and "__timeout__" in item:
                yield AgentEvent(
                    kind=EventKind.ERROR,
                    text=f"timed out after {item['__timeout__']}s",
                    detail={"termination": TerminationReason.TIMEOUT.value},
                )
                continue
            for event in _parse_ndjson_line(item):
                yield event

    async def run(self, request: RunRequest) -> RunResult:
        result = RunResult(
            correlation_id=request.correlation_id,
            runner=self.name,
            model=request.model,
            started_at=datetime.now(timezone.utc),
        )
        async for event in self.stream(request):
            _fold_event(event, result)
        result.finished_at = datetime.now(timezone.utc)

        if result.error and result.terminated is TerminationReason.COMPLETED:
            result.terminated = TerminationReason.ERROR

        try:
            verify_plugins(result, request, strict=self._strict)
        except Exception:
            result.terminated = TerminationReason.PLUGIN_LOAD_FAILED
            raise

        result.files_written = list(dict.fromkeys(result.files_written))
        logger.info(
            "CLI run end: correlation=%s terminated=%s tools=%d denied=%d files=%d tokens=%d",
            request.correlation_id, result.terminated.value, len(result.tools_invoked),
            len(result.tools_denied), len(result.files_written), result.total_tokens,
        )
        return result


# ---------------------------------------------------------------------------
# NDJSON -> normalised events
# ---------------------------------------------------------------------------

_WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}


def _parse_ndjson_line(line: str) -> list[AgentEvent]:
    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        # The CLI occasionally emits non-JSON diagnostics on stdout.
        return [AgentEvent(kind=EventKind.TEXT, text=line, detail={"unparsed": True})]
    if not isinstance(payload, dict):
        return []

    kind = payload.get("type")
    events: list[AgentEvent] = []

    if kind == "system" and payload.get("subtype") == "init":
        plugins = [
            p.get("name") if isinstance(p, dict) else str(p)
            for p in (payload.get("plugins") or [])
        ]
        events.append(AgentEvent(
            kind=EventKind.SESSION_START,
            detail={"session_id": payload.get("session_id")},
        ))
        events.append(AgentEvent(
            kind=EventKind.PLUGINS_LOADED,
            detail={
                "plugins": [p for p in plugins if p],
                "skills": list(payload.get("skills") or []),
                "slash_commands": list(payload.get("slash_commands") or []),
            },
        ))
        return events

    if kind == "assistant":
        for block in (payload.get("message") or {}).get("content") or []:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "text" and block.get("text"):
                events.append(AgentEvent(kind=EventKind.TEXT, text=block["text"]))
            elif btype == "thinking":
                events.append(AgentEvent(kind=EventKind.THINKING))
            elif btype == "tool_use":
                tool = block.get("name") or "?"
                tool_input = block.get("input") or {}
                events.append(AgentEvent(
                    kind=EventKind.TOOL_CALL, tool_name=str(tool)
                ))
                if tool in _WRITE_TOOLS and isinstance(tool_input, dict):
                    path = tool_input.get("file_path") or tool_input.get("path")
                    if path:
                        events.append(AgentEvent(
                            kind=EventKind.FILE_WRITTEN,
                            tool_name=str(tool), path=str(path),
                        ))
        return events

    if kind == "user":
        # Tool results come back as user-role messages; surface denials.
        for block in (payload.get("message") or {}).get("content") or []:
            if not isinstance(block, dict) or block.get("type") != "tool_result":
                continue
            content = block.get("content")
            text = content if isinstance(content, str) else json.dumps(content)[:400]
            if block.get("is_error") and _looks_like_denial(text):
                events.append(AgentEvent(
                    kind=EventKind.TOOL_DENIED, text=text[:300]
                ))
            else:
                events.append(AgentEvent(kind=EventKind.TOOL_RESULT))
        return events

    if kind == "result":
        usage = payload.get("usage") or {}
        events.append(AgentEvent(
            kind=EventKind.USAGE,
            detail={
                "input_tokens": usage.get("input_tokens") or 0,
                "output_tokens": usage.get("output_tokens") or 0,
                "cache_read_input_tokens": usage.get("cache_read_input_tokens") or 0,
                "cache_creation_input_tokens": usage.get("cache_creation_input_tokens") or 0,
                # Only the CLI reports these; keep None (not 0) when absent so
                # "not reported" stays distinguishable from "free".
                "cost_usd": payload.get("total_cost_usd"),
                "num_turns": payload.get("num_turns"),
            },
        ))
        events.append(AgentEvent(
            kind=EventKind.SESSION_END,
            detail={
                "session_id": payload.get("session_id"),
                "is_error": bool(payload.get("is_error")),
                "subtype": payload.get("subtype"),
            },
            text=payload.get("result") if payload.get("is_error") else None,
        ))
        return events

    return events


def _looks_like_denial(text: str) -> bool:
    lowered = text.lower()
    return any(
        marker in lowered
        for marker in ("permission", "not allowed", "denied", "requires approval")
    )


def _write_mcp_config(request: RunRequest) -> Path | None:
    """Materialise `--mcp-config` JSON inside the run workspace."""
    if not request.mcp_servers:
        return None
    config = {"mcpServers": {s.name: s.to_mcp_config() for s in request.mcp_servers}}
    path = request.workspace / ".mcp-config.json"
    path.write_text(json.dumps(config, indent=2), encoding="utf-8")
    return path
