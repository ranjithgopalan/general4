"""FSD OPEN ReAct reasoning — reuses analysis agent tools; FSD-specific prompt.

Imports ``build_tools`` and ``extract_json`` directly from the analysis agent so tool
definitions are maintained in one place. Only the prompt path and the context type
(FSDContext vs ContextPackage) differ from the analysis react module.
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import AsyncIterator
from functools import lru_cache
from pathlib import Path
from typing import Any

# Reuse tool definitions + step-labelling from analysis — no duplication.
from app.lifecycle.stages.analysis.agent.react import _tool_step
from app.lifecycle.stages.analysis.agent.tools import build_tools
from app.lifecycle.stages.analysis.patterns.jsonx import extract_json
from app.lifecycle.stages.fsd.match.context import FSDContext
from app.services.graph_provider import GraphProvider
from app.services.kb_query import KbQueryService
from app.services.telemetry import get_telemetry_service
from app.utils.logging import log
from app.utils.request_context import set_agent_run_id

_PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "fsd.json"
_RECURSION_LIMIT = 12  # same backstop as analysis (open loop, docs/04 §2)


@lru_cache(maxsize=1)
def _system_template() -> str:
    return json.loads(_PROMPT_PATH.read_text(encoding="utf-8"))["system"]


def _system_prompt(persona: str) -> str:
    return _system_template().replace("{persona}", persona)


def _text_of(message: Any) -> str:
    """Extract text content from a LangGraph message (mirrors analysis react helper)."""
    content = getattr(message, "content", message)
    if isinstance(content, list):
        return " ".join(str(b.get("text", "")) if isinstance(b, dict) else str(b) for b in content)
    return str(content or "")


async def run_react(
    *, model: Any, kb: KbQueryService, graph: GraphProvider, persona: str, context: FSDContext
) -> AsyncIterator[tuple[str, Any]]:
    """Async generator: yields ('step', {tool,label}) live as the agent works, then ONE
    ('result', (enrichment JSON | None, whitelist)). Mirrors the analysis agent's streaming."""
    agent_run_id = str(uuid.uuid4())[:12]
    set_agent_run_id(agent_run_id)
    collected: set[str] = set(context.allowed_ids)
    if model is None:
        yield ("result", (None, collected))
        return
    t0 = time.perf_counter()
    iterations = 0
    tokens_in = 0
    tokens_out = 0
    status = "ok"
    log.info(f"[react:fsd] arid={agent_run_id} persona={persona} recursion_limit={_RECURSION_LIMIT} starting")
    try:
        from langgraph.prebuilt import create_react_agent

        tools = build_tools(kb, graph, persona, collected, agent_run_id=agent_run_id)
        agent = create_react_agent(model, tools=tools, prompt=_system_prompt(persona))
        user = context.format_for_llm() + "\n\nNow return the STRICT JSON described in your instructions."
        final_text = ""
        async for event in agent.astream_events(
            {"messages": [("user", user)]}, version="v2", config={"recursion_limit": _RECURSION_LIMIT}
        ):
            etype = event.get("event")
            if etype == "on_tool_end":  # emit on END so the tool RESULT is available for real labels
                iterations += 1
                data = event.get("data") or {}
                yield ("step", _tool_step(event.get("name", "tool"), data.get("input"), _text_of(data.get("output"))))
            elif etype == "on_chat_model_end":
                data = event.get("data") or {}
                output = data.get("output")
                text = _text_of(output)
                if text.strip():
                    final_text = text
                usage = getattr(output, "usage_metadata", None)
                if isinstance(usage, dict):
                    tokens_in += usage.get("input_tokens", 0)
                    tokens_out += usage.get("output_tokens", 0)
        yield ("step", {"tool": "synthesize", "label": "Composing the PRD"})
        yield ("result", (extract_json(final_text), collected))
    except Exception as exc:  # pragma: no cover — runtime Bedrock path; never fail the stage
        status = "error"
        log.warning(f"[react:fsd] arid={agent_run_id} FAILED ({exc}); using deterministic sections only.")
        yield ("result", (None, collected))
    finally:
        latency_ms = (time.perf_counter() - t0) * 1000
        log.info(
            f"[react:fsd] arid={agent_run_id} persona={persona} "
            f"iterations={iterations} tokens_in={tokens_in} tokens_out={tokens_out} "
            f"status={status} {latency_ms:.0f}ms"
        )
        try:
            get_telemetry_service().emit_agent_run(
                agent_run_id=agent_run_id,
                stage="fsd",
                persona=persona,
                iterations=iterations,
                status=status,
                latency_ms=latency_ms,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
            )
        except Exception:  # pragma: no cover — telemetry must never break the stage
            pass
