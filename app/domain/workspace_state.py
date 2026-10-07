"""Workspace state machine — pure, deterministic transition rules (docs/04 §8b, docs/19 §4).

NO I/O, no LLM. This module answers only two questions: *is this transition structurally legal?*
and *what is the happy-path successor?* Runtime EXIT conditions (did the FSD assemble? did the
grounding spine pass?) are the service's responsibility (Step 3) — the machine encodes just the legal
transition graph, including the ``ANALYSIS -> CLOSED`` short-circuit for an Existing-class requirement.
"""

from __future__ import annotations

from app.models.workspace import Route, WorkspaceState, WorkspaceType

WS = WorkspaceState


class TransitionError(ValueError):
    """Raised when a requested workspace state transition is not allowed."""


# Happy-path order (docs/04 §8). CLOSED is terminal.
# S0-S7: INTAKE→ANALYSIS→FSD→BRD→STORIES→ARCHITECTURE→DEVELOPMENT→QA_TESTING→PENDING_SYNC→CLOSED
HAPPY_PATH: tuple[WorkspaceState, ...] = (
    WS.INTAKE,
    WS.ANALYSIS,
    WS.FSD,  # FSD is now the single "Business & Functional Spec" — the BRD stage was merged into it
    WS.ARCHITECTURE,  # Design + Architect precedes Stories (design-first): FSD → ARCHITECTURE → STORIES
    WS.STORIES,
    WS.DEVELOPMENT,
    WS.QA_TESTING,
    WS.MERGE,  # DevOps: merge delivered work + kick off the background KB-refresh round-trip (docs/23)
    WS.PENDING_SYNC,
    WS.CLOSED,
)

# Legal transitions (structural). ANALYSIS -> CLOSED is the Existing-class short-circuit (docs/04 §8b).
# S7 (QA_TESTING) now goes directly to PENDING_SYNC (RELEASE/DevOps stage removed).
TRANSITIONS: dict[WorkspaceState, frozenset[WorkspaceState]] = {
    WS.INTAKE: frozenset({WS.ANALYSIS}),
    WS.ANALYSIS: frozenset({WS.FSD, WS.CLOSED}),
    WS.FSD: frozenset({WS.ARCHITECTURE}),      # BRD merged into FSD: FSD → ARCHITECTURE directly
    WS.BRD: frozenset({WS.ARCHITECTURE}),      # legacy only — BRD stage removed; old BRD rows advance here
    WS.ARCHITECTURE: frozenset({WS.STORIES}),  # ARCHITECTURE → STORIES (stories written against the design)
    WS.STORIES: frozenset({WS.DEVELOPMENT}),   # STORIES → DEVELOPMENT
    WS.DEVELOPMENT: frozenset({WS.QA_TESTING}),
    WS.QA_TESTING: frozenset({WS.MERGE}),          # QA accept -> MERGE (DevOps) stage
    WS.MERGE: frozenset({WS.PENDING_SYNC}),        # Merge triggers the background KB-refresh -> syncing
    WS.PENDING_SYNC: frozenset({WS.CLOSED}),
    WS.CLOSED: frozenset(),
}

# Convenience default route per type (docs/04 §8c); route may still be set explicitly at intake.
# Routes: A (New Feature), B (Enhancement), C (reserved), D (Tech Mod), E (Upgrade), F (Defect)
_DEFAULT_ROUTE: dict[WorkspaceType, Route] = {
    WorkspaceType.NEW_FEATURE: Route.A,
    WorkspaceType.ENHANCEMENT: Route.B,
    WorkspaceType.TECH_MODERNIZATION: Route.D,
    WorkspaceType.UPGRADE: Route.E,
    WorkspaceType.DEFECT_RESOLUTION: Route.F,
}


class WorkspaceStateMachine:
    """Structural transition rules for a workspace (stateless — all methods are class-level)."""

    @staticmethod
    def initial_state() -> WorkspaceState:
        return WS.INTAKE

    @staticmethod
    def allowed_from(state: WorkspaceState) -> frozenset[WorkspaceState]:
        return TRANSITIONS[state]

    @staticmethod
    def is_terminal(state: WorkspaceState) -> bool:
        return not TRANSITIONS[state]

    @classmethod
    def can_transition(cls, src: WorkspaceState, dst: WorkspaceState) -> bool:
        return dst in TRANSITIONS[src]

    @classmethod
    def validate(cls, src: WorkspaceState, dst: WorkspaceState) -> None:
        """Raise ``TransitionError`` if ``src -> dst`` is not a legal transition."""
        if not cls.can_transition(src, dst):
            allowed = ", ".join(sorted(s.value for s in TRANSITIONS[src])) or "(none — terminal)"
            raise TransitionError(f"illegal transition {src.value} -> {dst.value}; allowed: {allowed}")

    @staticmethod
    def next_state(state: WorkspaceState) -> WorkspaceState | None:
        """The happy-path successor (INTAKE->ANALYSIS->...->CLOSED), or None at the terminal state.

        States not on the happy path (e.g. legacy BRD, since the BRD stage was merged into the FSD) fall
        back to their legacy transition target so old rows can still advance."""
        if state not in HAPPY_PATH:
            legacy = TRANSITIONS.get(state)
            return next(iter(legacy)) if legacy else None
        idx = HAPPY_PATH.index(state)
        return HAPPY_PATH[idx + 1] if idx + 1 < len(HAPPY_PATH) else None

    @staticmethod
    def default_route(wtype: WorkspaceType) -> Route | None:
        return _DEFAULT_ROUTE.get(wtype)
