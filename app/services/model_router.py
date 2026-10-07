"""
Model router (FE backend) — resolve a chat model for a task, config-driven.

    task ──▶ MODEL_ROUTING ──▶ logical name ──▶ MODEL_REGISTRY ──▶ Bedrock model id

The running app always uses the real Bedrock model (``ChatBedrockConverse`` via langchain-aws).
A ``FakeLLM`` double is returned ONLY when ``settings.LLM_PROVIDER == 'fake'`` (test fixtures);
no shipped config sets that. **No model ids are hardcoded** — all come from Settings.

Every resolved model is wrapped in ``_LLMWithTelemetry`` (lean hook): each ``ainvoke`` emits one
``llm.invoke`` telemetry event (task, model_id, status, duration, token usage) through the shared
telemetry sink. No audit-table write, no debug-log file (deferred).

Usage::

    from app.services.model_router import get_model_router
    llm = get_model_router().get_llm("synthesize")
    resp = await llm.ainvoke(messages)   # resp.content
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any

from app.config.settings import get_settings
from app.services import telemetry as telemetry_mod
from app.utils.logging import log


def _disable_streaming(value: str) -> bool | str:
    """Coerce the BEDROCK_DISABLE_STREAMING config string to ChatBedrockConverse's expected type.

    'tool_calling' → the literal (stream text, buffer when tools bound); 'false'/'0'/'no' → False
    (always stream); anything else → True (never stream, the langchain default)."""
    v = (value or "").strip().lower()
    if v == "tool_calling":
        return "tool_calling"
    if v in ("false", "0", "no", "off"):
        return False
    return True


class _LLMWithTelemetry:
    """Thin wrapper emitting one ``llm.invoke`` telemetry event per async call.

    Forwards ``invoke`` (sync) untouched and chains ``bind_tools`` so ReAct wiring keeps working.
    A telemetry/sink failure can never break the LLM path (the service swallows sink errors).

    Extended fields (Phase 8):
    - prompt_version: stamped from AgentExecutionContext when available
    - agent_version: stamped from AgentExecutionContext when available
    - retry_count: set by @observe_agent / tenacity hook on the parent context
    - time_to_first_token_ms: measured on streaming calls (first chunk latency)
    """

    def __init__(self, inner: Any, model_id: str, task: str) -> None:
        self._inner = inner
        self._model_id = model_id
        self._task = task
        # These are set per-call from AgentExecutionContext when available.
        self.prompt_version: str = ""
        self.agent_version: str = ""
        self.retry_count: int = 0

    def invoke(self, messages: Any, **kwargs: Any) -> Any:
        return self._inner.invoke(messages, **kwargs)

    async def ainvoke(self, messages: Any, **kwargs: Any) -> Any:
        t0 = time.perf_counter()
        request_ts = datetime.now(tz=timezone.utc)
        status = "ok"
        response: Any = None
        try:
            response = await self._inner.ainvoke(messages, **kwargs)
            return response
        except Exception:
            status = "error"
            raise
        finally:
            usage = getattr(response, "usage_metadata", None) or {}
            latency_ms = round((time.perf_counter() - t0) * 1000.0, 2)
            tokens_in = int(usage.get("input_tokens", 0) or 0)
            tokens_out = int(usage.get("output_tokens", 0) or 0)
            svc = telemetry_mod.get_telemetry_service()
            svc.emit_llm_call(
                task=self._task,
                model_id=self._model_id,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                latency_ms=latency_ms,
                stage=status,
            )
            # Extended fields emitted to OUTPUT_GENERATED event
            svc.emit(
                telemetry_mod.EventType.OUTPUT_GENERATED,
                {
                    "task": self._task,
                    "model_id": self._model_id,
                    "latency_ms": latency_ms,
                    "status": status,
                    "prompt_version": self.prompt_version,
                    "agent_version": self.agent_version,
                    "retry_count": self.retry_count,
                },
            )
            from app.services import ai_call_recorder as _rec  # noqa: PLC0415
            asyncio.create_task(_rec.record(_rec.AICallRecord(
                model_provider="bedrock",
                model_name=self._model_id,
                request_ts=request_ts,
                response_ts=datetime.now(tz=timezone.utc),
                latency_ms=int(latency_ms),
                input_tokens=tokens_in,
                output_tokens=tokens_out,
                request_status=status,
            )))

    async def astream(self, messages: Any, **kwargs: Any):
        """Proxy token streaming, measuring time-to-first-token for observability.

        Yields inner model's chunks unchanged. Usage metadata is only present on the
        final chunk for some providers — best-effort, never breaks the stream."""
        t0 = time.perf_counter()
        request_ts = datetime.now(tz=timezone.utc)
        ttft_ms: float | None = None
        status = "ok"
        last: Any = None
        try:
            async for chunk in self._inner.astream(messages, **kwargs):
                if ttft_ms is None:
                    ttft_ms = round((time.perf_counter() - t0) * 1000.0, 2)
                last = chunk
                yield chunk
        except Exception:
            status = "error"
            raise
        finally:
            usage = getattr(last, "usage_metadata", None) or {}
            latency_ms = round((time.perf_counter() - t0) * 1000.0, 2)
            tokens_in = int(usage.get("input_tokens", 0) or 0)
            tokens_out = int(usage.get("output_tokens", 0) or 0)
            svc = telemetry_mod.get_telemetry_service()
            svc.emit_llm_call(
                task=self._task,
                model_id=self._model_id,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                latency_ms=latency_ms,
                stage=status,
            )
            svc.emit(
                telemetry_mod.EventType.OUTPUT_GENERATED,
                {
                    "task": self._task,
                    "model_id": self._model_id,
                    "latency_ms": latency_ms,
                    "time_to_first_token_ms": ttft_ms,
                    "status": status,
                    "prompt_version": self.prompt_version,
                    "agent_version": self.agent_version,
                    "retry_count": self.retry_count,
                },
            )
            from app.services import ai_call_recorder as _rec  # noqa: PLC0415
            asyncio.create_task(_rec.record(_rec.AICallRecord(
                model_provider="bedrock",
                model_name=self._model_id,
                request_ts=request_ts,
                response_ts=datetime.now(tz=timezone.utc),
                latency_ms=int(latency_ms),
                input_tokens=tokens_in,
                output_tokens=tokens_out,
                request_status=status,
            )))

    def bind_tools(self, tools: Any, **kwargs: Any) -> _LLMWithTelemetry:
        self._inner = self._inner.bind_tools(tools, **kwargs)
        return self

    def with_context(
        self,
        *,
        prompt_version: str = "",
        agent_version: str = "",
        retry_count: int = 0,
    ) -> _LLMWithTelemetry:
        """Return self with updated context fields — call from @observe_agent."""
        self.prompt_version = prompt_version
        self.agent_version = agent_version
        self.retry_count = retry_count
        return self


class ModelRouter:
    """Resolve (and cache per task) a telemetry-wrapped chat model."""

    def __init__(self) -> None:
        self._settings = get_settings()
        self._cache: dict[str, Any] = {}
        # Tests set this to a zero-arg callable returning a scripted FakeLLM.
        self._fake_factory: Any = None

    def get_llm(self, task: str) -> Any:
        """Return a telemetry-wrapped chat model for ``task`` (cached)."""
        if task not in self._cache:
            inner, model_id = self._resolve(task)
            self._cache[task] = _LLMWithTelemetry(inner, model_id, task)
        return self._cache[task]

    # kept as an alias — some callers prefer this name
    get_chat_model = get_llm

    def _resolve(self, task: str) -> tuple[Any, str]:
        """(inner_model, model_id_label) for ``task``."""
        if self._settings.LLM_PROVIDER == "fake":
            from app.testing.fake_llm import FakeLLM

            inner = self._fake_factory() if self._fake_factory is not None else FakeLLM()
            return inner, "fake"

        model_id = self._settings.model_id_for(task)
        if not model_id:
            raise ValueError(
                f"[model_router] No model id for task={task!r} "
                f"(routing={self._settings.MODEL_ROUTING}, default={self._settings.MODEL_DEFAULT!r}). "
                "Set MODEL_REGISTRY_JSON + MODEL_ROUTING_JSON/MODEL_DEFAULT."
            )
        return self._make_bedrock_model(model_id, task), model_id

    def _make_bedrock_model(self, model_id: str, task: str) -> Any:
        from langchain_aws import ChatBedrockConverse

        from app.utils.bedrock_clients import get_bedrock_runtime_client

        client = get_bedrock_runtime_client()
        if client is None:
            raise RuntimeError(
                f"[model_router] Bedrock runtime client unavailable for task={task!r}; " "check AWS credentials/region."
            )
        max_tokens = self._settings.TASK_MAX_TOKENS.get(task) or self._settings.BEDROCK_LLM_DEFAULT_MAX_TOKENS
        model = ChatBedrockConverse(
            model=model_id,
            client=client,
            region_name=self._settings.AWS_REGION,
            temperature=self._settings.BEDROCK_LLM_DEFAULT_TEMPERATURE,
            max_tokens=max_tokens,
            disable_streaming=_disable_streaming(self._settings.BEDROCK_DISABLE_STREAMING),
        )
        log.info(f"[model_router] ChatBedrockConverse ready: model_id={model_id} task={task} max_tokens={max_tokens}")
        return model


@lru_cache(maxsize=1)
def get_model_router() -> ModelRouter:
    """Process-wide singleton ModelRouter."""
    return ModelRouter()
