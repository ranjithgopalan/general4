"""QA-stage ReAct agent — enriches test_scope and regression_scope.

Cloned from developer/agent/react.py; substitutes the QA prompt and QAContext.
The agent produces: test_scope (prose), additional_gaps (list), regression_items (list).
Returns (None, collected_ids) when model is None (fixture / test mode).

Manual bind_tools() loop (P6 pattern) — no create_react_agent dependency.

Expected agent output keys (strict JSON):
  test_scope:        str  — 3-5 sentences about what is under test
  additional_gaps:   list[{description, gap_type, priority, action}]
  regression_items:  list[str]
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.lifecycle.stages.analysis.agent.tools import build_tools
from app.lifecycle.stages.analysis.patterns.jsonx import extract_json
from app.lifecycle.stages.qa.match.context import QAContext

_PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "qa.json"
_RECURSION_LIMIT = 8   # QA agent is lighter than architecture — fewer iterations needed


@lru_cache(maxsize=1)
def _load_prompt() -> dict:
    return json.loads(_PROMPT_PATH.read_text(encoding="utf-8"))


def _system_prompt() -> str:
    return _load_prompt()["system"]


def _user_message(ctx: QAContext) -> str:
    template = _load_prompt().get("user_template", "{context}")
    allowed_str = ", ".join(sorted(ctx.allowed_ids)[:30])
    gap_summary = f"{len([]) } deterministic gaps detected"  # filled by assemble step; brief here
    return (
        template
        .replace("{context}", ctx.format_for_llm())
        .replace("{allowed_ids}", allowed_str or "(none)")
        .replace("{gap_summary}", gap_summary)
    )


def _text_of(msg: object) -> str:
    if hasattr(msg, "content"):
        c = msg.content
        if isinstance(c, str):
            return c
        if isinstance(c, list):
            return " ".join(p.get("text", "") if isinstance(p, dict) else str(p) for p in c)
    return str(msg)


async def _execute_tool_call(tool_call: dict, tools: list) -> str:
    """Execute one tool call and return the result as a string (never raises)."""
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
                import asyncio
                if asyncio.iscoroutinefunction(tool):
                    return str(await tool(**tool_args))
                return str(tool(**tool_args))
            return f"Tool {tool_name!r} is not callable."
        except Exception as exc:  # noqa: BLE001
            return f"Tool error ({tool_name}): {exc}"
    return f"Unknown tool: {tool_name!r}"


async def run_react(
    *,
    model: object,
    kb: object,
    graph: object,
    persona: str,
    context: QAContext,
) -> tuple[dict[str, Any] | None, set[str]]:
    """Run the QA ReAct agent and return (enriched_dict, collected_ids).

    collected_ids = context.allowed_ids; may grow as agent explores KB.
    Returns (None, collected) when model is None (fixture / test mode).
    """
    from app.utils.logging import log

    collected: set[str] = set(context.allowed_ids)

    if model is None:
        return None, collected

    try:
        from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
    except ImportError as exc:
        log.warning(f"[qa-react] langchain_core not available — skipping LLM enrichment: {exc}")
        return None, collected

    tools = build_tools(kb, graph, persona, collected)

    try:
        model_with_tools = model.bind_tools(tools) if tools else model
    except (AttributeError, TypeError):
        model_with_tools = model

    messages: list[Any] = [
        SystemMessage(content=_system_prompt()),
        HumanMessage(content=_user_message(context)),
    ]

    for iteration in range(_RECURSION_LIMIT):
        try:
            response = await model_with_tools.ainvoke(messages)
        except Exception as exc:  # noqa: BLE001
            log.warning(f"[qa-react] model invoke failed at iteration {iteration} (returning None): {exc}")
            return None, collected

        messages.append(response)
        tool_calls: list[dict] = getattr(response, "tool_calls", []) or []

        if not tool_calls:
            text = _text_of(response)
            return extract_json(text), collected

        for tc in tool_calls:
            tool_call_id = tc.get("id") or f"tc-{iteration}"
            result_text = await _execute_tool_call(tc, tools)
            messages.append(ToolMessage(content=result_text, tool_call_id=tool_call_id))

    last = messages[-1] if messages else None
    text = _text_of(last) if last else ""
    log.warning(f"[qa-react] recursion limit {_RECURSION_LIMIT} reached; extracting partial JSON")
    return extract_json(text), collected
