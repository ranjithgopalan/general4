"""Configuration-driven ClaudeRunner selection.

`FE_RUNNER` accepts:

    sdk      Claude Agent SDK in-process. Refused if the SDK is a stub.
    cli      Claude Code CLI subprocess. No permission bypass (PRD 5.4).
    mock     In-memory, for tests and dry runs.
    auto     Prefer sdk, fall back to cli. The default.
    bedrock  LangGraph create_react_agent + ChatBedrockConverse (IAM, no API key).
             Required for ECS deployment where CLI / SDK are unavailable.

`auto` exists because this estate ships a stub `claude_agent_sdk` whose import
succeeds: choosing statically would either fail at call time (sdk) or give up
the in-process path on machines where the real SDK IS installed (cli).
"""

from __future__ import annotations

import logging
from typing import Literal

from app.agentic_platform.fe_core.ports.claude_runner import ClaudeRunner, RunnerUnavailableError

logger = logging.getLogger(__name__)

RunnerName = Literal["sdk", "cli", "mock", "auto", "bedrock"]


def build_runner(
    name: RunnerName = "auto",
    *,
    cli_binary: str | None = None,
    verify_plugins_strict: bool = True,
) -> ClaudeRunner:
    from app.agentic_platform.worker.runners.cli_runner import CliRunner
    from app.agentic_platform.worker.runners.mock_runner import MockRunner
    from app.agentic_platform.worker.runners.sdk_runner import SdkRunner

    if name == "mock":
        return MockRunner(verify_plugins_strict=verify_plugins_strict)
    if name == "sdk":
        return SdkRunner(verify_plugins_strict=verify_plugins_strict)
    if name == "cli":
        return CliRunner(binary=cli_binary, verify_plugins_strict=verify_plugins_strict)
    if name == "bedrock":
        from app.agentic_platform.worker.runners.bedrock_runner import BedrockReActRunner
        return BedrockReActRunner()
    if name != "auto":
        raise ValueError(f"unknown runner: {name!r}. Valid: sdk, cli, mock, auto, bedrock")
    # auto: resolved lazily by resolve_runner() so preflight can be awaited.
    return CliRunner(binary=cli_binary, verify_plugins_strict=verify_plugins_strict)


async def resolve_runner(
    name: RunnerName = "auto",
    *,
    cli_binary: str | None = None,
    verify_plugins_strict: bool = True,
) -> tuple[ClaudeRunner, str]:
    """Return a usable runner plus a human-readable reason.

    Raises RunnerUnavailableError when nothing can execute, rather than handing
    back a runner that will fail on first use.
    """
    from app.agentic_platform.worker.runners.cli_runner import CliRunner
    from app.agentic_platform.worker.runners.mock_runner import MockRunner
    from app.agentic_platform.worker.runners.sdk_runner import SdkRunner

    if name == "mock":
        runner = MockRunner(verify_plugins_strict=verify_plugins_strict)
        return runner, "mock runner selected explicitly"

    if name == "bedrock":
        from app.agentic_platform.worker.runners.bedrock_runner import BedrockReActRunner
        runner = BedrockReActRunner()
        ok, detail = await runner.preflight()
        if ok:
            return runner, detail
        raise RunnerUnavailableError(
            f"Bedrock runner unavailable: {detail}. "
            "Ensure the ECS task role (or ~/.aws/credentials on laptop) has "
            "bedrock:InvokeModel permission."
        )

    candidates: list[ClaudeRunner]
    if name == "sdk":
        candidates = [SdkRunner(verify_plugins_strict=verify_plugins_strict)]
    elif name == "cli":
        candidates = [CliRunner(binary=cli_binary,
                                verify_plugins_strict=verify_plugins_strict)]
    elif name == "auto":
        candidates = [
            SdkRunner(verify_plugins_strict=verify_plugins_strict),
            CliRunner(binary=cli_binary, verify_plugins_strict=verify_plugins_strict),
        ]
    else:
        raise ValueError(f"unknown runner: {name!r}. Valid: sdk, cli, mock, auto, bedrock")

    failures: list[str] = []
    for candidate in candidates:
        ok, detail = await candidate.preflight()
        if ok:
            if name == "auto" and candidate.name != "sdk":
                logger.warning(
                    "Runner 'auto' fell back to %s. SDK unusable: %s",
                    candidate.name, failures[0] if failures else "unknown",
                )
            return candidate, detail
        failures.append(f"{candidate.name}: {detail}")

    raise RunnerUnavailableError(
        "No usable Claude runner. " + "; ".join(failures)
        + ". Install the real claude-agent-sdk, or install the Claude Code CLI "
          "and set CLAUDE_CLI_PATH, or set FE_RUNNER=bedrock for ECS."
    )
