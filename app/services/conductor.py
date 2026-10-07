"""Workspace conductor — the LangGraph S0–S9 skeleton (docs/19 §5). Framework only: STUB nodes.

Carries a workspace through the OPEN substates (INTAKE -> ... -> PENDING_SYNC) on a durable LangGraph
``StateGraph``. Each stage node is a STUB: it advances the workspace via ``WorkspaceService`` (which
persists + audits) and records a placeholder artifact — NO LLM, no ``kb.query``. The persona/LLM logic
fills these nodes later (docs/19 §10, Step 8). The conductor STOPS at PENDING_SYNC — close + kb-refresh
is a separate, deliberate step. HITL ``interrupt()`` seams pause before any stage listed in HITL_BEFORE.

Checkpointer: an in-memory saver by default (dev/test). ``AsyncPostgresSaver`` is the prod checkpointer,
wired at startup from the psycopg pool (its checkpoint tables come from ``.setup()``); it is left as a
documented seam so the skeleton runs without a DB.
"""

from __future__ import annotations

from typing import Any, TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph
from langgraph.types import interrupt

from app.domain.workspace_state import WorkspaceStateMachine
from app.models.workspace import WorkspaceState
from app.services.workspace_service import WorkspaceService

_SM = WorkspaceStateMachine

# Stage -> (persona, artifact_kind) registry (docs/04 §8a). Config-shaped; not stored.
# QA_TESTING (S7) is the final delivery stage; RELEASE/DevOps removed.
STAGE_REGISTRY: dict[WorkspaceState, tuple[str, str]] = {
    WorkspaceState.ANALYSIS: ("ba", "analysis"),
    WorkspaceState.FSD: ("ba", "FSD"),
    WorkspaceState.BRD: ("ba", "BRD"),
    WorkspaceState.STORIES: ("ba", "stories"),
    WorkspaceState.ARCHITECTURE: ("architect", "SRD"),
    WorkspaceState.DEVELOPMENT: ("developer", "code-notes"),
    WorkspaceState.QA_TESTING: ("qa", "test-plan"),
}

# Stages to pause before for human review (HITL).
# Mandatory approval gates at FSD (requirements sign-off), ARCHITECTURE (design sign-off),
# and DEVELOPMENT (pre-code readiness confirmation). These align with the target architecture
# where human approval is required at each major SDLC milestone before proceeding.
# Reference: smarzban/agent-sdlc gate pattern, dsh-a Phase 1C + 2B approval gates.
HITL_BEFORE: frozenset[WorkspaceState] = frozenset({
    WorkspaceState.FSD,          # BA sign-off on requirements before architecture
    WorkspaceState.ARCHITECTURE,  # Architect sign-off on design before stories
    WorkspaceState.DEVELOPMENT,   # Readiness gate: FSD+SRD+Stories approved before code gen
    WorkspaceState.MERGE,         # DevOps sign-off before branch merge
})

# The conductor advances through OPEN and stops here (close + kb-refresh is a separate, deliberate step).
_STOP_STATES = frozenset({WorkspaceState.PENDING_SYNC, WorkspaceState.CLOSED})


class ConductorState(TypedDict, total=False):
    workspace_id: str
    state: str
    history: list[str]
    stopped: bool


def _make_run_stage(service: WorkspaceService, specialist: Any = None):
    """Build the single looping stage node. With a ``specialist`` each stage produces a grounded artifact
    (kb.query -> artifact + GROUNDS); without one it records a placeholder (framework smoke path)."""

    async def run_stage(state: ConductorState) -> ConductorState:
        ws = await service.get(state["workspace_id"])
        if ws.state in _STOP_STATES:
            return {"state": ws.state.value, "stopped": True}
        nxt = _SM.next_state(ws.state)
        if nxt is not None and nxt in HITL_BEFORE:
            interrupt({"reason": "human-review", "workspace_id": ws.workspace_id, "before": nxt.value})
        advanced = await service.advance(state["workspace_id"])  # advance — persists + audits
        if advanced.state in STAGE_REGISTRY:
            persona, kind = STAGE_REGISTRY[advanced.state]
            if specialist is not None:
                await specialist.run_stage(
                    state["workspace_id"], persona=persona, stage=advanced.state.value, artifact_kind=kind
                )
            else:
                await service.attach_artifact(
                    state["workspace_id"],
                    kind=kind,
                    content=f"[stub] {persona} output for {advanced.state.value}",
                    actor=f"conductor:{persona}",
                )
        history = [*state.get("history", []), advanced.state.value]
        return {"state": advanced.state.value, "history": history, "stopped": advanced.state in _STOP_STATES}

    return run_stage


def _route(state: ConductorState) -> str:
    return "end" if state.get("stopped") else "run_stage"


def get_checkpointer() -> Any:
    """The conductor checkpointer. In-memory for dev/test; AsyncPostgresSaver is the prod seam."""
    return MemorySaver()


def build_conductor(service: WorkspaceService | None = None, specialist: Any = None, checkpointer: Any = None) -> Any:
    """Compile the S0–S9 conductor ``StateGraph``. With a ``specialist``, stages produce grounded artifacts."""
    svc = service or WorkspaceService()
    graph = StateGraph(ConductorState)
    graph.add_node("run_stage", _make_run_stage(svc, specialist))
    graph.set_entry_point("run_stage")
    graph.add_conditional_edges("run_stage", _route, {"run_stage": "run_stage", "end": END})
    return graph.compile(checkpointer=checkpointer or get_checkpointer())


async def run(compiled: Any, workspace_id: str, *, thread_id: str | None = None) -> dict[str, Any]:
    """Drive a workspace through the conductor (``ainvoke``); one checkpoint thread per workspace."""
    config = {"configurable": {"thread_id": thread_id or workspace_id}}
    return await compiled.ainvoke({"workspace_id": workspace_id, "history": []}, config=config)
