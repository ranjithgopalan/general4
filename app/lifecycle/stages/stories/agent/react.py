"""Stories-stage ReAct agent — generates user story prose grounded in KB cards.

Cloned from brd/agent/react.py; substitutes the stories prompt and StoriesContext type.
The agent produces N story rows (title, as_a, i_want, so_that, AC fields, change_class)
and a list of open items.

P6 FIX (2026-08-09): replaced ``create_react_agent`` (langgraph-prebuilt — files missing from
disk) with a manual ChatBedrockConverse.bind_tools() tool-use loop. Same pattern as
developer/agent/react.py. No new pip dependencies. No change to external behaviour.

IMAD retry-with-merge pattern: on JSON parse failure, retries once with a reinforced
instruction prefix. On second failure, returns {} (all prose fields stay as stubs).

When model is None (fixture/test mode) returns ({}, collected_ids) immediately — callers
use the deterministic skeleton as-is without crashing.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.lifecycle.stages.analysis.agent.react import _tool_step
from app.lifecycle.stages.analysis.agent.tools import build_tools
from app.lifecycle.stages.analysis.patterns.jsonx import extract_json
from app.lifecycle.stages.stories.match.context import StoriesContext

_PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "stories.json"
# NOTE: _RECURSION_LIMIT is now read from config (FE_REACT_RECURSION_LIMIT) at runtime in run_react().
# Default fallback if config is not available: 4 (aggressive, forces quick decisions)
_RECURSION_LIMIT_DEFAULT = 4
_DECISIVE = (
    "\n\nBe efficient: make at most 3-4 targeted knowledge-base lookups, then output the final JSON. "
    "Do not keep exploring — the deterministic context already gives you the primary cards. "
    "Output the JSON immediately after your 3-4 lookups — do not attempt more iterations."
)
_RETRY_SUFFIX = (
    "\n\nYou MUST return ONLY valid JSON matching the schema above. "
    "No markdown fences. No code blocks. No explanation. "
    "Your ENTIRE response must be valid JSON starting with '{' and ending with '}'. "
    "Write complete narratives for EVERY story field — not stubs, not one-word values. "
    "Minimum 5 words per field: title, as_a, i_want, so_that, ac_given, ac_when, ac_then."
)


def _needs_retry(enriched: Any) -> bool:
    """True when an attempt produced empty/invalid JSON (no usable stories)."""
    return not (isinstance(enriched, dict) and enriched.get("stories"))


@lru_cache(maxsize=1)
def _prompt_spec() -> dict:
    return json.loads(_PROMPT_PATH.read_text(encoding="utf-8"))


def _system_prompt() -> str:
    return _prompt_spec()["system"]


def _user_message(ctx: StoriesContext) -> str:
    template: str = _prompt_spec()["user_template"]
    return template.replace("{context}", ctx.format_for_llm())


def _text_of(msg: object) -> str:
    """Extract text content from a LangChain message object."""
    if hasattr(msg, "content"):
        c = msg.content
        if isinstance(c, str):
            return c
        if isinstance(c, list):
            parts = [p.get("text", "") if isinstance(p, dict) else str(p) for p in c]
            return " ".join(parts)
    return str(msg)


async def _execute_tool_call(tool_call: dict, tools: list) -> str:
    """Execute one tool call emitted by the model. Never raises."""
    import asyncio

    tool_name = tool_call.get("name", "")
    tool_args = tool_call.get("args", {})

    for tool in tools:
        name = getattr(tool, "name", None) or getattr(tool, "__name__", None)
        if name != tool_name:
            continue
        try:
            if hasattr(tool, "ainvoke"):
                return str(await tool.ainvoke(tool_args))
            if hasattr(tool, "invoke"):
                return str(tool.invoke(tool_args))
            if callable(tool):
                if asyncio.iscoroutinefunction(tool):
                    return str(await tool(**tool_args))
                return str(tool(**tool_args))
            return f"Tool {tool_name!r} is not callable."
        except Exception as exc:  # noqa: BLE001
            return f"Tool error ({tool_name}): {exc}"

    return f"Unknown tool: {tool_name!r}"


async def _invoke_once(model_with_tools: object, messages: list[Any], tools: list, recursion_limit: int = _RECURSION_LIMIT_DEFAULT) -> AsyncIterator[tuple[str, Any]]:
    """Async generator for one full ReAct loop (reason → tool → observe): yields ('step', {tool,label})
    per tool call, then exactly ONE ('result', (parsed_dict | None, messages)). The loop stops when the
    model emits no tool_calls (final answer). Yields ('result', (None, messages)) if the model call fails."""
    from app.utils.logging import log

    try:
        from langchain_core.messages import ToolMessage
    except ImportError:
        yield ("result", (None, messages))
        return

    _FORCE_JSON_MSG = (
        "\n\n⚠ STOP TOOL CALLS NOW. You have used most of your allowed iterations. "
        "Output the final JSON immediately — no more kb_query or kb_read calls. "
        "Use ONLY the KB cards already shown in the context and tool results above. "
        "Return ONLY valid JSON starting with '{' and ending with '}'. No markdown."
    )

    for iteration in range(recursion_limit):
        # Two iterations before the hard limit, append a stop instruction so the LLM has
        # one full iteration to produce JSON instead of dying mid-tool-call.
        if iteration == recursion_limit - 2:
            try:
                from langchain_core.messages import HumanMessage as _HM
                messages.append(_HM(content=_FORCE_JSON_MSG))
                log.info(f"[stories-react] iteration={iteration}: injecting forced-stop message")
            except ImportError:
                pass

        try:
            response = await model_with_tools.ainvoke(messages)
        except Exception as exc:  # noqa: BLE001
            log.warning(f"[stories-react] model invoke failed at iteration {iteration}: {exc}")
            yield ("result", (None, messages))
            return

        messages.append(response)
        tool_calls: list[dict] = getattr(response, "tool_calls", []) or []

        if not tool_calls:  # final answer
            raw_text = _text_of(response)
            parsed = extract_json(raw_text)
            story_count = len((parsed or {}).get("stories", []))
            log.info(
                f"[stories-react] iteration={iteration} final answer: "
                f"parse={'ok stories={}'.format(story_count) if parsed else 'FAILED (None)'} "
                f"raw_preview={raw_text[:500]!r}"
            )
            yield ("result", (parsed, messages))
            return

        for tc in tool_calls:
            tool_call_id = tc.get("id") or f"tc-{iteration}"
            result_text = await _execute_tool_call(tc, tools)
            # emit AFTER execution so the step's detail can name the business items found (parity with
            # the astream_events stages, which emit on on_tool_end).
            yield ("step", _tool_step(tc.get("name", ""), tc.get("args"), result_text))
            messages.append(ToolMessage(content=result_text, tool_call_id=tool_call_id))

    # Recursion limit hit — return whatever is in the last message
    last = messages[-1] if messages else None
    raw_text = _text_of(last) if last else ""
    parsed = extract_json(raw_text)
    log.warning(
        f"[stories-react] RECURSION LIMIT HIT (limit={recursion_limit}): "
        f"parse={'ok stories={}'.format(len((parsed or {}).get('stories', []))) if parsed else 'FAILED'} "
        f"raw_preview={raw_text[:300]!r}"
    )
    yield ("result", (parsed, messages))


async def run_react(
    *,
    model: object,
    kb: object,
    graph: object,
    persona: str,
    context: StoriesContext,
    recursion_limit: int | None = None,
) -> AsyncIterator[tuple[str, Any]]:
    """Async generator: yields ('step', {tool,label}) live as the agent works, then ONE
    ('result', (enriched_dict, collected_ids)). ``enriched_dict`` keys: stories[], open_items[].

    Manual bind_tools() loop (no langgraph.prebuilt dependency). IMAD retry-with-merge: on empty/
    invalid JSON, retries once with a reinforced instruction. Always yields a well-shaped dict.

    recursion_limit: override the default step budget (read from config if not provided).
    """
    from app.utils.logging import log

    # Read recursion limit from config if not explicitly provided
    if recursion_limit is None:
        try:
            from app.config.settings import get_settings
            settings = get_settings()
            recursion_limit = settings.STORIES_REACT_RECURSION_LIMIT
        except Exception:  # noqa: BLE001
            recursion_limit = _RECURSION_LIMIT_DEFAULT

    log.info(f"[stories-react] recursion_limit={recursion_limit} (from config STORIES_REACT_RECURSION_LIMIT or default={_RECURSION_LIMIT_DEFAULT})")

    collected: set[str] = set(context.allowed_ids)
    if model is None:
        yield ("result", ({}, collected))
        return

    try:
        from langchain_core.messages import HumanMessage, SystemMessage
    except ImportError as exc:
        log.warning(f"[stories-react] langchain_core not available: {exc}")
        yield ("result", ({}, collected))
        return

    # For SCR-only contexts all cards are already injected into the prompt — disable tools
    # so the model outputs JSON immediately without making kb_query calls. Each kb_query
    # internally runs its own LLM synthesis (~40s each), causing Bedrock read timeouts when
    # the model makes 4+ tool calls before producing the final JSON.
    scr_only = bool(context.scr_matched and not context.fr_matched and not context.br_matched)
    if scr_only:
        log.info("[stories-react] SCR-only context — tools disabled (all screens in prompt)")
        model_with_tools = model
        tools = []
    else:
        tools = build_tools(kb, graph, persona, collected)
        try:
            model_with_tools = model.bind_tools(tools) if tools else model
        except (AttributeError, TypeError):
            model_with_tools = model

    user_msg = _user_message(context) + _DECISIVE
    captured: dict[str, Any] = {"enriched": None}

    async def _attempt(msgs: list[Any]) -> AsyncIterator[dict]:
        """Run one _invoke_once attempt: re-yield step payloads, stash the parsed result."""
        async for kind, payload in _invoke_once(model_with_tools, msgs, tools, recursion_limit=recursion_limit):
            if kind == "step":
                yield payload
            else:
                captured["enriched"] = payload[0]

    def _msgs(extra: str = "") -> list[Any]:
        return [SystemMessage(content=_system_prompt()), HumanMessage(content=user_msg + extra)]

    async for step in _attempt(_msgs()):
        yield ("step", step)

    enriched = captured["enriched"]
    # IMAD retry-with-merge: if the first attempt produced invalid/empty JSON, retry once.
    if _needs_retry(enriched):
        log.info("[stories-react] first attempt returned empty/invalid JSON — retrying with reinforced instruction")
        async for step in _attempt(_msgs(_RETRY_SUFFIX)):
            yield ("step", step)
        enriched = captured["enriched"]

    if not isinstance(enriched, dict):
        enriched = {}
    enriched.setdefault("stories", [])
    enriched.setdefault("open_items", [])
    yield ("step", {"tool": "synthesize", "label": "Composing the stories"})
    yield ("result", (enriched, collected))
