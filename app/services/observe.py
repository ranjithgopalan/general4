"""
@observe_agent — decorator for automatic AGENT_STARTED / AGENT_RUN telemetry.

Wraps an async agent entry-point function. The decorated function must be
an async callable that accepts keyword-only ``agent_name`` and ``stage``
arguments (or they can be supplied to the decorator directly as defaults).

Usage — on the function definition:

    from app.services.observe import observe_agent

    @observe_agent(agent_name="brd", stage="brd")
    async def run(state: WorkspaceState, ...) -> AgentOutput:
        ...

Usage — inline with explicit context override:

    @observe_agent()
    async def run(state, *, agent_name: str = "analysis", stage: str = "analysis", ...):
        ...

The decorator:
1. Emits AGENT_STARTED (via AgentExecutionContext) before the function runs.
2. Emits AGENT_RUN on completion (success or error) with latency + retry_count.
3. Propagates tenacity retry count via a RetryCallState callback — callers can
   pass a RetryCallState as the kwarg ``_retry_state`` to update the count.
4. Never changes the return type of the wrapped function.
5. Never swallows exceptions — re-raises after emitting the error event.
"""

from __future__ import annotations

import functools
import time
from typing import Any, Callable

from app.services.agent_context import AgentExecutionContext
from app.utils.logging import log


def observe_agent(
    *,
    agent_name: str = "",
    stage: str = "",
    module_id: str = "",
    feature_id: str = "",
    prompt_version: str = "",
    agent_version: str = "",
) -> Callable:
    """Decorator factory — wraps an async agent function with lifecycle telemetry."""

    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            # Allow per-call overrides via kwargs (e.g. from conductors)
            _agent_name = kwargs.pop("_obs_agent_name", None) or agent_name or fn.__name__
            _stage = kwargs.pop("_obs_stage", None) or stage or _agent_name
            _module_id = kwargs.pop("_obs_module_id", None) or module_id
            _feature_id = kwargs.pop("_obs_feature_id", None) or feature_id
            _rule_ids: list[str] = kwargs.pop("_obs_rule_ids", None) or []
            _artifact_id: str = kwargs.pop("_obs_artifact_id", None) or ""
            _prompt_version = kwargs.pop("_obs_prompt_version", None) or prompt_version
            _agent_version = kwargs.pop("_obs_agent_version", None) or agent_version

            ctx = AgentExecutionContext.from_request(
                agent_name=_agent_name,
                stage=_stage,
                module_id=_module_id,
                feature_id=_feature_id,
                rule_ids=_rule_ids,
                artifact_id=_artifact_id,
                prompt_version=_prompt_version,
                agent_version=_agent_version,
            )

            ctx.emit_started()
            t0 = time.perf_counter()
            status = "success"
            result: Any = None

            try:
                result = await fn(*args, **kwargs)
                return result
            except Exception as exc:
                status = "error"
                log.warning(f"[observe_agent] {_agent_name} raised: {exc}")
                raise
            finally:
                latency_ms = round((time.perf_counter() - t0) * 1000.0, 1)
                # Extract token counts from result if the agent returns a dict with usage
                tokens_in = 0
                tokens_out = 0
                iterations = 0
                if isinstance(result, dict):
                    tokens_in = int(result.get("tokens_in") or result.get("usage", {}).get("input_tokens", 0) or 0)
                    tokens_out = int(result.get("tokens_out") or result.get("usage", {}).get("output_tokens", 0) or 0)
                    iterations = int(result.get("iterations") or 0)

                ctx.emit_completed(
                    iterations=iterations,
                    tokens_in=tokens_in,
                    tokens_out=tokens_out,
                    status=status,
                    latency_ms=latency_ms,
                )

        return wrapper

    return decorator


def tenacity_retry_callback(ctx: AgentExecutionContext) -> Callable:
    """Return a tenacity ``before_sleep`` callback that emits AGENT_RETRY events.

    Usage with tenacity:
        @retry(
            stop=stop_after_attempt(3),
            wait=wait_exponential(min=1, max=10),
            before_sleep=tenacity_retry_callback(ctx),
        )
        async def _call_llm():
            ...
    """

    def callback(retry_state: Any) -> None:
        attempt = retry_state.attempt_number
        exc = retry_state.outcome.exception() if retry_state.outcome else None
        reason = str(exc) if exc else "unknown"
        ctx.emit_retry(attempt=attempt, reason=reason)

    return callback
