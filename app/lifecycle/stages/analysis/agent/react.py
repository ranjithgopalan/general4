"""OPEN ReAct reasoning — deterministic context in, grounded enrichment out.

Runs a LangGraph ``create_react_agent`` over the grounded tools, seeded with the deterministic
ContextPackage. Returns the parsed enrichment JSON (or None → deterministic-only sections) plus the
set of ids the agent actually retrieved (the grounding whitelist). Bounded by ``recursion_limit`` as a
runaway backstop (open loop, per docs/04 §2). No model → returns (None, allowed_ids) immediately.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from collections.abc import AsyncIterator
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.config.settings import get_settings
from app.lifecycle.stages.analysis.agent.tools import build_tools
from app.lifecycle.stages.analysis.match.context import ContextPackage
from app.lifecycle.stages.analysis.patterns.jsonx import extract_json
from app.services.graph_provider import GraphProvider
from app.services.kb_query import KbQueryService
from app.services.telemetry import get_telemetry_service
from app.utils.logging import log
from app.utils.request_context import set_agent_run_id

_PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "analysis.json"


@lru_cache(maxsize=1)
def _system_template() -> str:
    """Load the ReAct system prompt from JSON config (cached)."""
    return json.loads(_PROMPT_PATH.read_text(encoding="utf-8"))["system"]


def _system_prompt(persona: str) -> str:
    return _system_template().replace("{persona}", persona)


def _text_of(message: Any) -> str:
    content = getattr(message, "content", message)
    if isinstance(content, list):
        return " ".join(str(b.get("text", "")) if isinstance(b, dict) else str(b) for b in content)
    return str(content or "")


# BUSINESS-friendly action labels for the agent's tool calls (streamed to the UI). The `detail`
# (built by _tool_step) carries the ACTUAL substance in plain language — what was searched for and
# which business items were found/reviewed — but NEVER the technical form (no KB ids, kinds, or loci).
_TOOL_LABELS = {
    "kb_query": "Searching the knowledge base",
    "graph_neighbors": "Checking connected areas",
    "kb_read": "Reviewing related material",
    "synthesize": "Composing the analysis",
}


def _clip(text: str, limit: int = 90) -> str:
    """Collapse whitespace and truncate to a readable length."""
    text = " ".join((text or "").split())
    return (text[: limit - 1] + "…") if len(text) > limit else text


_EMPTY_MARKERS = {"no matching cards", "no neighbours", ""}

# A "name" that is really source code / a code identifier must never reach the business-facing UI
# (the impact analysis is read by stakeholders). Drop code-shaped tokens: operators/punctuation,
# language keywords, and single camelCase/PascalCase identifiers (e.g. AppFilterTest, paramMap, CommonInfo).
_CODE_TOKENS = ("=", "<", ">", "();", "()", "{", "}", "[]", ";", "new ", "::", "->", "&&", "||", "//")
_CODE_KEYWORDS = {
    "for", "if", "else", "return", "void", "public", "private", "protected", "static", "class",
    "import", "package", "final", "try", "catch", "throw", "throws", "this", "null", "true", "false",
    "int", "string", "boolean", "var", "let", "const", "function", "def",
}
_CAMEL = re.compile(r"[a-z][A-Z]|[A-Z][a-z].*[A-Z]")  # internal caps = a code identifier


def _is_code_like(s: str) -> bool:
    """True when a candidate label is source code / a code identifier, not a business name."""
    t = (s or "").strip()
    if not t:
        return True
    if t.lower() in _CODE_KEYWORDS:
        return True
    if any(tok in t for tok in _CODE_TOKENS):
        return True
    # a single ASCII token with camel/Pascal internal capitalization (class/var name) — not a business label.
    # Multi-word names, ALL-CAPS acronyms (AEM/PEGA), and Japanese labels are kept.
    return " " not in t and t.isascii() and bool(_CAMEL.search(t))


def _names_from_line(line: str) -> list[str]:
    """Extract business item name(s) from one tool-result line (dropping ids/kinds/loci)."""
    stripped = line.strip()
    if stripped.lower().startswith("nodes:"):  # graph_neighbors: "nodes: [ID] a, [ID] b"
        segs = stripped[len("nodes:"):].split(",")
        return [s.split("]", 1)[1].strip() for s in segs if "]" in s]
    if "]" not in stripped:
        return []
    rest = stripped.split("]", 1)[1].strip()  # text after "[ID]"
    if "(" in rest and "):" in rest:  # kb_read: "Label (Kind): body"
        return [rest.split("(", 1)[0].strip()]
    if ":" in rest:  # kb_query: "Kind: Label"
        return [rest.split(":", 1)[1].strip()]
    return [rest]


def _labels_from_result(output: str, cap: int = 3) -> list[str]:
    """Human, business-readable item names from a tool's result text — labels only, no ids/kinds."""
    names: list[str] = []
    for raw in (output or "").splitlines():
        for name in _names_from_line(raw):
            if name.lower() not in _EMPTY_MARKERS and not _is_code_like(name) and name not in names:
                names.append(name)
    return names[:cap]


def _tool_step(name: str, tool_input: Any, output: str | None = None) -> dict[str, str]:
    """A business-friendly step for one agent tool call: an action label + a plain-language ``detail``
    describing what was searched for / found / reviewed. No ids, kinds, loci, or raw technical text.
    ``output`` is the tool's result (available on tool-END) — used to name the business items found."""
    label = _TOOL_LABELS.get(name, "Analyzing")
    detail = ""
    try:
        ti = tool_input if isinstance(tool_input, dict) else {}
        found = _labels_from_result(output) if output else []
        if name == "kb_query":
            query = _clip(str(ti.get("query", "")), 70)
            if query and found:
                detail = f"“{query}” — found {', '.join(found)}"
            elif query:
                detail = f"“{query}”"
            elif found:
                detail = ", ".join(found)
        elif name in ("kb_read", "graph_neighbors"):
            detail = ", ".join(found)
    except Exception:  # noqa: BLE001 — a label is cosmetic; never break the stream
        detail = ""
    step = {"tool": name, "label": label}
    if detail:
        step["detail"] = detail
    return step


async def run_react(
    *, model: Any, kb: KbQueryService, graph: GraphProvider, persona: str, context: ContextPackage
) -> AsyncIterator[tuple[str, Any]]:
    """Async generator: yields ('step', {tool, label}) live as the agent works, then exactly ONE
    ('result', (enrichment JSON | None, retrieved-id whitelist)). Streaming the tool calls gives the
    UI real 'agent steps' instead of a long opaque wait; the final result is identical to before.
    Bounded by recursion_limit; any failure yields ('result', (None, allowed_ids)) — deterministic fallback.

    Observability: generates an ``agent_run_id`` per execution and sets it in the ContextVar so
    tool calls and telemetry events can be correlated without parameter threading.  Token usage is
    extracted from ``on_chat_model_end`` events and included in the final AGENT_RUN telemetry event.
    """
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
    recursion_limit = get_settings().FE_REACT_RECURSION_LIMIT
    log.info(f"[react:analysis] arid={agent_run_id} persona={persona} recursion_limit={recursion_limit} starting")
    try:
        from langgraph.prebuilt import create_react_agent

        tools = build_tools(kb, graph, persona, collected, agent_run_id=agent_run_id)
        agent = create_react_agent(model, tools=tools, prompt=_system_prompt(persona))
        user = context.format_for_llm() + "\n\nNow return the STRICT JSON described in your instructions."
        final_text = ""
        async for event in agent.astream_events(
            {"messages": [("user", user)]}, version="v2", config={"recursion_limit": recursion_limit}
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
                    final_text = text  # last non-empty model message = the final JSON answer
                # Capture token usage from the Bedrock response metadata
                usage = getattr(output, "usage_metadata", None)
                if isinstance(usage, dict):
                    tokens_in += usage.get("input_tokens", 0)
                    tokens_out += usage.get("output_tokens", 0)
        yield ("step", {"tool": "synthesize", "label": "Composing the analysis"})
        yield ("result", (extract_json(final_text), collected))
    except Exception as exc:  # pragma: no cover — runtime Bedrock path; never fail the stage
        status = "error"
        log.warning(f"[react:analysis] arid={agent_run_id} FAILED ({exc}); using deterministic sections only.")
        yield ("result", (None, collected))
    finally:
        latency_ms = (time.perf_counter() - t0) * 1000
        log.info(
            f"[react:analysis] arid={agent_run_id} persona={persona} "
            f"iterations={iterations} tokens_in={tokens_in} tokens_out={tokens_out} "
            f"status={status} {latency_ms:.0f}ms"
        )
        try:
            get_telemetry_service().emit_agent_run(
                agent_run_id=agent_run_id,
                stage="analysis",
                persona=persona,
                iterations=iterations,
                status=status,
                latency_ms=latency_ms,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
            )
        except Exception:  # pragma: no cover — telemetry must never break the stage
            pass
