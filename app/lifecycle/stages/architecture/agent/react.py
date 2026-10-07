"""Architecture-stage ReAct agent — enriches deterministic SRD sections.

Cloned from brd/agent/react.py; substitutes the SRD prompt and ArchitectureContext.
The agent produces: system_context, component responsibilities, integration protocols,
TO-BE Mermaid diagram, sequence diagrams, arch NFRs, and open ARCH-TODO items.
Returns (None, collected_ids) when model is None (fixture / test mode).
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
from app.lifecycle.stages.architecture.match.context import ArchitectureContext
from app.utils.logging import log

_PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "srd.json"
_RECURSION_LIMIT = 14  # slightly higher than BRD — architect prompts are more complex


@lru_cache(maxsize=1)
def _system_template() -> str:
    return json.loads(_PROMPT_PATH.read_text(encoding="utf-8"))["system"]


def _system_prompt(persona: str) -> str:
    return _system_template().replace("{persona}", persona or "Architect")


def _text_of(msg: object) -> str:
    """Extract text content from a LangGraph message object."""
    if hasattr(msg, "content"):
        c = msg.content
        if isinstance(c, str):
            return c
        if isinstance(c, list):
            parts = [p.get("text", "") if isinstance(p, dict) else str(p) for p in c]
            return " ".join(parts)
    return str(msg)


async def run_react(
    *,
    model: object,
    kb: object,
    graph: object,
    persona: str,
    context: ArchitectureContext,
) -> AsyncIterator[tuple[str, Any]]:
    """Async generator: yields ('step', {tool,label}) live as the agent works, then ONE
    ('result', (enriched | None, collected_ids)). Mirrors the analysis agent's streaming."""
    collected: set[str] = set(context.allowed_ids)
    if model is None:
        yield ("result", (None, collected))
        return
    try:
        from langgraph.prebuilt import create_react_agent  # lazy import — runtime only

        tools = build_tools(kb, graph, persona, collected)
        agent = create_react_agent(model, tools=tools, prompt=_system_prompt(persona))
        user_msg = context.format_for_llm() + "\n\nNow return the STRICT JSON only (no prose, no code fences)."
        final_text = ""
        async for event in agent.astream_events(
            {"messages": [("user", user_msg)]}, version="v2", config={"recursion_limit": _RECURSION_LIMIT}
        ):
            etype = event.get("event")
            if etype == "on_tool_end":  # emit on END so the tool RESULT is available for real labels
                data = event.get("data") or {}
                yield ("step", _tool_step(event.get("name", "tool"), data.get("input"), _text_of(data.get("output"))))
            elif etype == "on_chat_model_end":
                text = _text_of((event.get("data") or {}).get("output"))
                if text.strip():
                    final_text = text
        yield ("step", {"tool": "synthesize", "label": "Composing the architecture (SRD)"})
        yield ("result", (extract_json(final_text), collected))
    except Exception as exc:  # pragma: no cover — runtime Bedrock path; never fail the stage
        log.warning(f"[architecture] ReAct reasoning failed ({exc}); using deterministic sections only.")
        yield ("result", (None, collected))
