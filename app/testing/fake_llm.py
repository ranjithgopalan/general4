"""
FakeLLM — a deterministic, offline chat-model **test double**.

Purpose: keep the unit suite hermetic (fast, deterministic, zero AWS calls / cost) while the
running app always uses the real Bedrock model. This is selected by ``ModelRouter`` ONLY when
``settings.LLM_PROVIDER == 'fake'`` — which no shipped config sets; test fixtures set it.

It mimics the minimal slice of the LangChain ``ChatBedrockConverse`` surface the forward stack
uses: ``ainvoke`` / ``invoke`` returning an object with ``.content`` + ``.usage_metadata``, and
``bind_tools`` (returns self, so ReAct wiring is exercisable without a real tool-calling model).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any


@dataclass
class FakeLLMResponse:
    """Minimal stand-in for a LangChain ``AIMessage`` (only the fields callers read)."""

    content: str
    usage_metadata: dict[str, int] = field(default_factory=lambda: {"input_tokens": 0, "output_tokens": 0})
    response_metadata: dict[str, Any] = field(default_factory=dict)
    type: str = "ai"


class FakeLLM:
    """Deterministic chat-model double.

    Args:
        responses: canned reply(s). A single string replays for every call; an iterable is
            consumed one reply per call (then the last reply repeats). Defaults to a marker
            string so an un-scripted call is still obvious in assertions.
    """

    def __init__(self, responses: str | Iterable[str] | None = None) -> None:
        if responses is None:
            self._queue = ["[FAKE-LLM] no scripted response"]
        elif isinstance(responses, str):
            self._queue = [responses]
        else:
            self._queue = list(responses) or ["[FAKE-LLM] empty script"]
        self.calls: list[Any] = []  # every messages payload seen (for assertions)
        self._bound_tools: list[Any] = []

    def _next(self) -> str:
        return self._queue[0] if len(self._queue) == 1 else self._queue.pop(0)

    def _respond(self, messages: Any) -> FakeLLMResponse:
        self.calls.append(messages)
        return FakeLLMResponse(content=self._next())

    def invoke(self, messages: Any, **_kwargs: Any) -> FakeLLMResponse:
        return self._respond(messages)

    async def ainvoke(self, messages: Any, **_kwargs: Any) -> FakeLLMResponse:
        return self._respond(messages)

    def bind_tools(self, tools: Any, **_kwargs: Any) -> FakeLLM:
        """Record bound tools and return self (mirrors ChatBedrockConverse chaining)."""
        self._bound_tools = list(tools) if isinstance(tools, list | tuple) else [tools]
        return self
