"""Developer-stage ReAct agent — enriches deterministic DevDocument sections.

Cloned from architecture/agent/react.py; substitutes the dev prompt and DevContext.
The agent produces: development_notes (per-story/component notes) and dev_gaps (SME gaps).
Returns (None, collected_ids) when model is None (fixture / test mode).

P6 FIX: replaced ``create_react_agent`` (langgraph-prebuilt — files physically missing from disk
despite appearing in pip RECORD) with a manual ChatBedrockConverse.bind_tools() tool-use loop.
No new dependencies; uses langchain-aws 1.7.0 already installed. Same external behaviour:
reason → tool → observe up to _RECURSION_LIMIT iterations, then return final JSON text.

Note: this agent is LIGHTER than the architecture ReAct — its output is a small dict
(development_notes + dev_gaps). Actual code file writing is a SEPARATE codegen route.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.config.settings import settings
from app.lifecycle.stages.analysis.agent.tools import build_tools
from app.lifecycle.stages.analysis.patterns.jsonx import extract_json
from app.lifecycle.stages.developer.match.context import DevContext

_PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "dev.json"
# Max reason→tool→observe iterations (each = 1 LLM call). Config-driven; lowered from a hardcoded 10.
_RECURSION_LIMIT = settings.DEV_REACT_MAX_ITERS


@lru_cache(maxsize=1)
def _system_template() -> str:
    return json.loads(_PROMPT_PATH.read_text(encoding="utf-8"))["system"]


def _system_prompt(persona: str) -> str:
    return _system_template().replace("{persona}", persona or "Developer")


def _text_of(msg: object) -> str:
    """Extract text content from a LangChain/LangGraph message object."""
    if hasattr(msg, "content"):
        c = msg.content
        if isinstance(c, str):
            return c
        if isinstance(c, list):
            parts = [p.get("text", "") if isinstance(p, dict) else str(p) for p in c]
            return " ".join(parts)
    return str(msg)


async def _execute_tool_call(tool_call: dict, tools: list) -> str:
    """Execute one tool call emitted by the model and return the result as a string.

    Supports both async (ainvoke) and sync (invoke / callable) tool interfaces.
    Returns a descriptive error string on failure — never raises (tool errors are
    surfaced to the model as ToolMessages so it can self-correct).
    """
    tool_name = tool_call.get("name", "")
    tool_args = tool_call.get("args", {})

    for tool in tools:
        name = getattr(tool, "name", None) or getattr(tool, "__name__", None)
        if name != tool_name:
            continue
        try:
            if hasattr(tool, "ainvoke"):
                result = await tool.ainvoke(tool_args)
            elif hasattr(tool, "invoke"):
                result = tool.invoke(tool_args)
            elif callable(tool):
                import asyncio
                if asyncio.iscoroutinefunction(tool):
                    result = await tool(**tool_args)
                else:
                    result = tool(**tool_args)
            else:
                result = f"Tool {tool_name!r} is not callable."
            return str(result)
        except Exception as exc:  # noqa: BLE001
            return f"Tool error ({tool_name}): {exc}"

    return f"Unknown tool: {tool_name!r}"


async def run_react(
    *,
    model: object,
    kb: object,
    graph: object,
    persona: str,
    context: DevContext,
) -> tuple[dict[str, Any] | None, set[str]]:
    """Run the Developer ReAct agent and return (enriched_dict, collected_ids).

    ``collected_ids`` starts with allowed_ids from the context; tool calls may extend it
    as the agent explores more KB nodes.
    Returns (None, collected_ids) when model is None (fixture / test mode).

    P6 implementation: manual bind_tools() loop — replaces create_react_agent.
    Each iteration:
      1. LLM responds with either tool_calls OR plain text (final answer).
      2. If tool_calls: execute each tool, append ToolMessages, continue loop.
      3. If no tool_calls: extract JSON from response text, return.
    Falls back to (None, collected) on repeated model errors.

    Expected agent output keys:
      development_notes: list[str]
      dev_gaps: list[{component_id, description, gap_status, sme_required}]
    """
    from app.utils.logging import log

    collected: set[str] = set(context.allowed_ids)

    if model is None:
        return None, collected

    try:
        from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
    except ImportError as exc:
        log.warning(f"[dev-react] langchain_core not available — skipping LLM enrichment: {exc}")
        return None, collected

    tools = build_tools(kb, graph, persona, collected)

    # bind_tools attaches the tool schemas so the model can emit tool_calls.
    # If the model does not support bind_tools (e.g. a fixture stub), fall back to raw model.
    try:
        model_with_tools = model.bind_tools(tools) if tools else model
    except (AttributeError, TypeError):
        model_with_tools = model

    user_content = (
        context.format_for_llm()
        + "\n\nNow return the STRICT JSON only (no prose, no code fences)."
    )

    messages: list[Any] = [
        SystemMessage(content=_system_prompt(persona)),
        HumanMessage(content=user_content),
    ]

    for iteration in range(_RECURSION_LIMIT):
        try:
            response = await model_with_tools.ainvoke(messages)
        except Exception as exc:  # noqa: BLE001
            log.warning(f"[dev-react] model invoke failed at iteration {iteration} (returning None): {exc}")
            return None, collected

        messages.append(response)

        # tool_calls is an empty list (not None) when the model returns a final answer.
        tool_calls: list[dict] = getattr(response, "tool_calls", []) or []

        if not tool_calls:
            # No tool calls → final answer in the text content.
            text = _text_of(response)
            return extract_json(text), collected

        # Execute every tool call this round, then continue the loop.
        for tc in tool_calls:
            tool_call_id = tc.get("id") or f"tc-{iteration}"
            result_text = await _execute_tool_call(tc, tools)
            messages.append(ToolMessage(content=result_text, tool_call_id=tool_call_id))

    # Recursion limit reached — use whatever the last message contains.
    last = messages[-1] if messages else None
    text = _text_of(last) if last else ""
    log.warning(f"[dev-react] recursion limit {_RECURSION_LIMIT} reached; extracting partial JSON")
    return extract_json(text), collected
