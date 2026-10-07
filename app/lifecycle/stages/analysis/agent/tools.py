"""Grounded tools for the ANALYSIS ReAct agent (closures over the deterministic providers).

Each tool records every id it surfaces into a shared ``collected`` set — the whitelist the grounding
gate later enforces on the agent's output (04 §11b: LLM-suggested ids are whitelist-filtered).

Every tool invocation is wrapped with:
  - A short ``tool_call_id`` (first 8 hex chars of a UUID) for correlation
  - Execution timing (perf_counter — sub-ms precision)
  - A structured log line: tool | tcid | agent_run_id | result_count | latency_ms
  - A TOOL_CALL telemetry event (status ok/error) — never raises on telemetry failure
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from app.services.graph_provider import GraphProvider
from app.services.kb_query import KbQueryService
from app.services.telemetry import get_telemetry_service
from app.utils.logging import log


def build_tools(
    kb: KbQueryService,
    graph: GraphProvider,
    persona: str,
    collected: set[str],
    agent_run_id: str = "",
) -> list[Any]:
    """Build the [kb_query, graph_neighbors, kb_read] toolset bound to the providers + whitelist sink.

    ``agent_run_id`` is stamped on every TOOL_CALL telemetry event so tool invocations can be
    correlated with the enclosing agent run without threading the ID through LangGraph internals.
    """
    from langchain_core.tools import tool

    def _tool_call_id() -> str:
        return str(uuid.uuid4())[:8]

    def _emit(tool_name: str, tcid: str, latency_ms: float, status: str, result_count: int = 0) -> None:
        try:
            get_telemetry_service().emit_tool_call(
                tool_name=tool_name,
                tool_call_id=tcid,
                agent_run_id=agent_run_id,
                latency_ms=latency_ms,
                status=status,
                result_count=result_count,
            )
        except Exception:  # pragma: no cover — telemetry must never break a tool
            pass

    @tool
    async def kb_query(query: str) -> str:
        """Search the KB for cards relevant to a query; returns cited card ids, kinds and labels."""
        tcid = _tool_call_id()
        t0 = time.perf_counter()
        try:
            answer = await kb.query(question=query, persona=persona)
            for c in answer.citations:
                collected.add(c.id)
            result = "\n".join(f"[{c.id}] {c.kind}: {c.label}" for c in answer.citations) or "no matching cards"
            hit_count = len(answer.citations)
            latency_ms = (time.perf_counter() - t0) * 1000
            log.info(f"[tool:kb_query] tcid={tcid} arid={agent_run_id} q_len={len(query)} hits={hit_count} {latency_ms:.0f}ms")
            _emit("kb_query", tcid, latency_ms, "ok", hit_count)
            return result
        except Exception as exc:
            latency_ms = (time.perf_counter() - t0) * 1000
            log.warning(f"[tool:kb_query] tcid={tcid} arid={agent_run_id} FAILED {latency_ms:.0f}ms: {exc}")
            _emit("kb_query", tcid, latency_ms, "error")
            raise

    @tool
    async def graph_neighbors(node_id: str) -> str:
        """List the typed-edge neighbours of a KB node id (trace what governs / depends on it)."""
        tcid = _tool_call_id()
        t0 = time.perf_counter()
        try:
            sub = await graph.neighbors(node_id, direction="both", depth=1)
            for n in sub.nodes:
                collected.add(n.id)
            edges = "; ".join(f"{e.source}-{e.label}->{e.target}" for e in sub.edges[:20])
            result = (edges or "no neighbours") + "\nnodes: " + ", ".join(f"[{n.id}] {n.label}" for n in sub.nodes)
            node_count = len(sub.nodes)
            latency_ms = (time.perf_counter() - t0) * 1000
            log.info(f"[tool:graph_neighbors] tcid={tcid} arid={agent_run_id} node={node_id!r} neighbours={node_count} {latency_ms:.0f}ms")
            _emit("graph_neighbors", tcid, latency_ms, "ok", node_count)
            return result
        except Exception as exc:
            latency_ms = (time.perf_counter() - t0) * 1000
            log.warning(f"[tool:graph_neighbors] tcid={tcid} arid={agent_run_id} node={node_id!r} FAILED {latency_ms:.0f}ms: {exc}")
            _emit("graph_neighbors", tcid, latency_ms, "error")
            raise

    @tool
    async def kb_read(node_id: str) -> str:
        """Read a KB node's card body + top evidence by id."""
        tcid = _tool_call_id()
        t0 = time.perf_counter()
        try:
            detail = await graph.node_detail(node_id)
            latency_ms = (time.perf_counter() - t0) * 1000
            if detail is None:
                log.info(f"[tool:kb_read] tcid={tcid} arid={agent_run_id} node={node_id!r} not_found {latency_ms:.0f}ms")
                _emit("kb_read", tcid, latency_ms, "not_found")
                return f"{node_id}: not found"
            collected.add(node_id)
            body = detail.prose or detail.node.summary or ""
            log.info(f"[tool:kb_read] tcid={tcid} arid={agent_run_id} node={node_id!r} body_len={len(body)} {latency_ms:.0f}ms")
            _emit("kb_read", tcid, latency_ms, "ok", 1)
            return f"[{node_id}] {detail.node.label} ({detail.node.kind}): {body}"
        except Exception as exc:
            latency_ms = (time.perf_counter() - t0) * 1000
            log.warning(f"[tool:kb_read] tcid={tcid} arid={agent_run_id} node={node_id!r} FAILED {latency_ms:.0f}ms: {exc}")
            _emit("kb_read", tcid, latency_ms, "error")
            raise

    return [kb_query, graph_neighbors, kb_read]
