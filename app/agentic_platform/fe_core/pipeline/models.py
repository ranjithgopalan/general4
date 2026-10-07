"""Pipeline domain model.

Stage states follow PRD 7.1 exactly. Two distinctions there are easy to lose and
are enforced here:

  * ``COMPLETED`` means generation *and* artefact persistence both succeeded. It
    does NOT mean approved (FR-022: "a successful stage without a persisted
    artifact is treated as failed").
  * a **rerun** creates a new run/step and a new artefact version; a **retry** of
    the same attempt uses the idempotency key and must not create a duplicate
    (FR-023).

Per-stage tool allowlists and permission policy are declared as data (FR-018),
so a stage cannot be granted more capability than its YAML says.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from enum import Enum
from itertools import groupby as _groupby
from typing import Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator, model_validator


class StageStatus(str, Enum):
    """Readiness of the capability that owns a stage."""

    FITS = "fits"        # runs today with no plugin change
    CHANGES = "changes"  # existing plugin needs a bounded extension
    GAP = "gap"          # no implementation exists yet


class StageState(str, Enum):
    """PRD 7.1 state set."""

    NOT_READY = "not_ready"
    READY = "ready"
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SUPERSEDED = "superseded"


#: Transitions the runner and API may perform. Anything else is a conflict.
ALLOWED_TRANSITIONS: dict[StageState, frozenset[StageState]] = {
    StageState.NOT_READY: frozenset({StageState.READY, StageState.SUPERSEDED}),
    StageState.READY: frozenset({StageState.QUEUED, StageState.NOT_READY,
                                 StageState.CANCELLED}),
    StageState.QUEUED: frozenset({StageState.RUNNING, StageState.CANCELLED,
                                  StageState.FAILED}),
    StageState.RUNNING: frozenset({StageState.WAITING_FOR_APPROVAL,
                                   StageState.COMPLETED, StageState.FAILED,
                                   StageState.CANCELLED}),
    StageState.WAITING_FOR_APPROVAL: frozenset({StageState.COMPLETED,
                                                StageState.FAILED,
                                                StageState.CANCELLED,
                                                StageState.SUPERSEDED}),
    StageState.COMPLETED: frozenset({StageState.SUPERSEDED}),
    StageState.FAILED: frozenset({StageState.QUEUED, StageState.SUPERSEDED}),
    StageState.CANCELLED: frozenset({StageState.QUEUED, StageState.SUPERSEDED}),
    StageState.SUPERSEDED: frozenset(),
}

#: Terminal for the purposes of "may the next stage start".
TERMINAL_STATES = frozenset({StageState.COMPLETED, StageState.SUPERSEDED})


def can_transition(current: StageState, target: StageState) -> bool:
    return target in ALLOWED_TRANSITIONS.get(current, frozenset())


class OwnerKind(str, Enum):
    PLUGIN = "plugin"      # a GATHER plugin skill
    BUILTIN = "builtin"    # implemented in this service
    EXTERNAL = "external"  # an existing harness outside this service


class StageOwner(BaseModel):
    kind: OwnerKind
    plugin: str | None = None
    skill: str | None = None
    handler: str | None = None
    note: str | None = None

    @model_validator(mode="after")
    def _check(self) -> "StageOwner":
        if self.kind is OwnerKind.PLUGIN and not (self.plugin and self.skill):
            raise ValueError("plugin owners require both 'plugin' and 'skill'")
        if self.kind is OwnerKind.BUILTIN and not self.handler:
            raise ValueError("builtin owners require 'handler'")
        return self

    @property
    def qualified_skill(self) -> str | None:
        """Namespaced skill invocation, e.g. 'GATHER-axis:axis-implement'."""
        if self.kind is OwnerKind.PLUGIN and self.plugin and self.skill:
            return f"{self.plugin}:{self.skill}"
        return None


class StagePermissions(BaseModel):
    """FR-018: per-stage tool allowlist and policy.

    There is no field that disables permission checking. `policy` maps onto
    `dontAsk` / `acceptEdits` / `plan`, never `bypassPermissions`.
    """

    allow: list[str] = Field(default_factory=list)
    deny: list[str] = Field(default_factory=list)
    policy: Literal["deny_unlisted", "accept_edits", "plan_only"] = "deny_unlisted"

    @model_validator(mode="after")
    def _no_overlap(self) -> "StagePermissions":
        overlap = set(self.allow) & set(self.deny)
        if overlap:
            raise ValueError(
                f"tool(s) both allowed and denied: {sorted(overlap)}"
            )
        return self


class Stage(BaseModel):
    key: str = Field(pattern=r"^[a-z0-9][a-z0-9\-]*$")
    seq: int = Field(ge=1)
    #: When non-null, this stage participates in a server-side fork/join group.
    #: All members of the group share the same non-null value and the same `seq`.
    #: The graph builder fans out to all members in parallel after the preceding
    #: stage's gate approves, then joins them at a barrier node before the next seq.
    parallel_group: str | None = None
    name: str
    deliverable: str
    owner: StageOwner
    status: StageStatus = StageStatus.FITS

    #: Which of the eight personas approves this stage (PRD 5.1, FR-P3).
    #: `approval_role` is accepted as an input alias so a pipeline written
    #: against the previous role names still loads.
    approval_persona: str = Field(validation_alias=AliasChoices(
        "approval_persona", "approval_role"))

    #: Which artefact tier this stage's outputs belong to. Together with
    #: `approval_persona` this is what FR-P3 checks; declaring it in the YAML
    #: keeps the pipeline the source of truth (FR-018).
    artifact_tier: str | None = None

    #: Personas who contribute to the stage but do not approve it. Recorded so
    #: the UI can show who belongs in a workspace step (PRD 8.1/8.2 "Owner").
    contributing_personas: list[str] = Field(default_factory=list)

    #: Which agent group this stage belongs to for UI display (e.g. "Business Analyst Agent").
    #: Used by the sidebar to group stages under agent headings. Optional; stages without
    #: a group are shown under "Other" as a safe fallback.
    agent_group: str | None = None

    consumes: list[str] = Field(default_factory=list)
    produces: list[str] = Field(default_factory=list)

    #: Questions asked of the local RAG store to build this stage's Knowledge
    #: Base context, on top of the stage deliverable. A stage that reads a large
    #: source document (the RED) needs several focused questions -- one query
    #: made from the stage title returned three near-random chunks of a
    #: 20,000-chunk report.
    retrieval_queries: list[str] = Field(default_factory=list)

    target: Literal["ui", "api", "db", "policies", "tests", "docs", "none"] = "none"

    # Per-stage overrides. `model` lets cheap document stages run on a smaller
    # model while code generation stays on the strongest one.
    # `required_runner` pins the stage to a specific runner type (e.g. "cli").
    # Workers whose FE_RUNNER does not match will skip these runs entirely.
    required_runner: str | None = None
    model: str | None = None
    effort: Literal["low", "medium", "high", "xhigh", "max"] | None = None
    max_turns: int | None = None
    timeout_seconds: int | None = None
    #: What happens when the turn cap fires:
    #:   fail     -- the run is FAILED (default; a capped run is not trusted)
    #:   salvage  -- if the deliverable the output contract demands is complete
    #:               on disk, accept it, mark the run `capped`, and apply every
    #:               normal check (deliverables, grounding, gate). For long
    #:               document stages that write incrementally and are cut off
    #:               on their closing edit.
    max_turns_policy: Literal["fail", "salvage"] = "fail"

    #: Post-run grounding check against the card KB (fe_core.kb.cards).
    #:   off      -- not checked
    #:   warn     -- report citation coverage in the run log / gate payload (default)
    #:   required -- FAIL the run when it cites no known card id or cites an
    #:               unknown one. Only meaningful once a card KB exists for the
    #:               application; with no KB the check degrades to `warn`.
    grounding: Literal["off", "warn", "required"] = "warn"

    @field_validator("grounding", mode="before")
    @classmethod
    def _grounding_yaml_bools(cls, v):
        # YAML reads a bare `off` as False and `on` as True.
        if v is False:
            return "off"
        if v is True:
            return "required"
        return v

    #: Configuration for `owner: {kind: builtin, handler: react}` -- the bounded
    #: ReAct builtin (worker_app.builtins.react_stage): `schema` (JSON schema of
    #: the output), `id_fields` (which fields hold KB ids), `max_steps`,
    #: `required_key`, `instructions`.
    react: dict | None = None

    #: Configuration for `owner: {kind: builtin, handler: kb-extract}`.
    #: Keys: app_code (str), keep_alias_prefixes (list[str]).
    kb_extract: dict | None = None

    #: Which card kinds this stage receives via _card_context().
    #: Empty list means no card injection. Used by Global (G2+), Architecture
    #: and Mini stages to declare which typed cards they need.
    card_kinds: list[str] = Field(default_factory=list)

    #: Reverse-engineering stages: copy the application's raw corpus
    #: (Settings.corpus_root_for) into <worktree>/inputs/corpus before the run.
    corpus: bool = False
    #: After a successful run, publish <worktree>/kb as the application's card KB
    #: (Settings.kb_root_for) -- only when the deterministic kb-check gate passed.
    publish_kb: bool = False

    permissions: StagePermissions = Field(default_factory=StagePermissions)

    blocked_reason: str | None = None
    notes: str | None = None

    model_config = ConfigDict(populate_by_name=True)

    @property
    def runnable(self) -> bool:
        return self.status is not StageStatus.GAP or self.owner.kind is OwnerKind.BUILTIN

    @property
    def approval_role(self) -> str:
        """Backwards-compatible accessor for the approving persona."""
        return self.approval_persona


def _validate_seqs(stages: list[Stage]) -> None:
    """Seq must have no gaps from 1; duplicate seq only within the same parallel_group.

    A stage with ``parallel_group`` non-null may share its ``seq`` with other
    members of the same group — this is what enables a server-side fork/join.
    No other duplicate-seq situation is permitted.
    """
    seqs = [s.seq for s in stages]
    unique_seqs = sorted(set(seqs))
    if unique_seqs != list(range(1, len(unique_seqs) + 1)):
        raise ValueError(f"stage seq must have no gaps from 1, got {sorted(seqs)}")
    by_seq: dict[int, list[Stage]] = defaultdict(list)
    for s in stages:
        by_seq[s.seq].append(s)
    for seq_val, members in by_seq.items():
        if len(members) == 1:
            continue
        groups = {m.parallel_group for m in members}
        if None in groups or len(groups) != 1:
            raise ValueError(
                f"seq {seq_val} has multiple stages but they don't all share the "
                f"same non-null parallel_group: {[m.key for m in members]}"
            )


class Pipeline(BaseModel):
    name: str
    title: str
    description: str = ""
    kb_application: str | None = None
    default_permissions: StagePermissions | None = None
    stages: list[Stage]

    #: Which workspace tier this pipeline runs in (PRD 5.3). The Global pipeline
    #: runs once; the Architecture pipeline is a singleton run after FRD; the Mini
    #: pipeline runs once per EPIC.
    tier: Literal["global", "mini", "architecture"] = "global"

    #: For a Mini or Architecture pipeline, the Global pipeline whose approved
    #: outputs it may consume.
    parent: str | None = None

    #: (Architecture pipeline) the Global stage key after which the Global thread
    #: parks at arch_sync_gate until this Architecture Workspace closes.
    arch_after: str | None = None

    #: (Global pipeline) artefact types produced by the Architecture Workspace
    #: that Global stages may consume (e.g. srd). Cross-checked against the
    #: architecture pipeline's `produces` at load time.
    arch_inputs: list[str] = Field(default_factory=list)

    #: Artefact types this pipeline consumes from its parent tier. Declared
    #: explicitly rather than inferred so that a typo fails at load time: the
    #: registry cross-checks every entry against the parent's `produces`.
    inherited_inputs: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate(self) -> "Pipeline":
        keys = [s.key for s in self.stages]
        if len(keys) != len(set(keys)):
            dupes = sorted({k for k in keys if keys.count(k) > 1})
            raise ValueError(f"duplicate stage keys: {dupes}")

        _validate_seqs(self.stages)

        if self.tier == "mini" and not self.parent:
            raise ValueError(
                "a 'mini' pipeline must name its 'parent' global pipeline: its "
                "stages consume artefacts the Global Workspace produced"
            )
        if self.tier == "architecture":
            if not self.parent:
                raise ValueError(
                    "an 'architecture' pipeline must name its 'parent' global pipeline"
                )
            if not self.arch_after:
                raise ValueError(
                    "an 'architecture' pipeline must declare 'arch_after': the Global "
                    "stage key after which the Global thread waits for it"
                )
        if self.tier == "global" and self.inherited_inputs:
            raise ValueError(
                "a 'global' pipeline has no parent tier, so 'inherited_inputs' "
                f"cannot be satisfied: {self.inherited_inputs}"
            )

        # A stage may only consume artefact types produced strictly upstream, or
        # inherited from the parent tier, or (Global) supplied by the Architecture
        # Workspace via arch_inputs.
        # Parallel stages (same seq / parallel_group) are processed as a batch:
        # ALL members' consumes are validated first, THEN their produces are added.
        # This prevents members from consuming each other's outputs.
        produced: set[str] = set(self.inherited_inputs) | set(self.arch_inputs)
        hint = ("; declare it in 'inherited_inputs' if the Global "
                "Workspace produces it" if self.tier == "mini" else "")
        for _, grp in _groupby(self.ordered, key=lambda s: s.seq):
            batch = list(grp)
            for stage in batch:
                unknown = [c for c in stage.consumes if c not in produced]
                if unknown:
                    raise ValueError(
                        f"stage '{stage.key}' consumes {unknown} which no upstream "
                        f"stage produces (available: {sorted(produced) or 'none'}){hint}"
                    )
            for stage in batch:
                produced.update(stage.produces)

        # FR-P3 as a configuration check: a stage whose approving persona does
        # not own its artefact tier is unapprovable, and would either block the
        # pipeline or -- worse -- let a Global persona sign off code.
        from app.agentic_platform.fe_core.auth.personas import (
            ArtifactTier, may_approve_tier, owners_of, persona_from_name,
        )
        for stage in self.stages:
            persona = persona_from_name(stage.approval_persona)
            if persona is None:
                raise ValueError(
                    f"stage '{stage.key}' names unknown approval persona "
                    f"'{stage.approval_persona}'"
                )
            if stage.artifact_tier is None:
                continue
            try:
                tier = ArtifactTier(stage.artifact_tier)
            except ValueError:
                raise ValueError(
                    f"stage '{stage.key}' declares unknown artifact_tier "
                    f"'{stage.artifact_tier}'"
                ) from None
            if not may_approve_tier(persona, tier):
                allowed = sorted(p.value for p in owners_of(tier))
                raise ValueError(
                    f"stage '{stage.key}': persona '{persona.value}' cannot "
                    f"approve '{tier.value}' artefacts. Owners: {allowed}"
                )

        # Apply pipeline-level default permissions to stages that declare none.
        if self.default_permissions is not None:
            default = self.default_permissions
            for stage in self.stages:
                if not stage.permissions.allow and not stage.permissions.deny:
                    stage.permissions = default.model_copy(deep=True)
        return self

    @property
    def ordered(self) -> list[Stage]:
        return sorted(self.stages, key=lambda s: s.seq)

    def stage(self, key: str) -> Stage:
        for s in self.stages:
            if s.key == key:
                return s
        raise KeyError(f"unknown stage: {key}")

    def next_after(self, key: str) -> Stage | None:
        """Return the first stage whose seq is strictly greater than `key`'s seq.

        For parallel stages that share the same ``seq``, this returns the same
        successor regardless of which member is queried — so both ``ui-code`` and
        ``api-code`` (seq 2) return ``db-integration`` (seq 3).
        """
        current_seq = self.stage(key).seq
        return next((s for s in self.ordered if s.seq > current_seq), None)

    def required_plugins(self) -> list[str]:
        return sorted({
            s.owner.plugin for s in self.stages
            if s.owner.kind is OwnerKind.PLUGIN and s.owner.plugin
        })


class StageRun(BaseModel):
    """One execution attempt of one stage. Persisted, never memory-only.

    Carries the full provenance set required by PRD FR-019.
    """

    run_id: str
    pipeline: str
    stage_key: str
    kb_application_id: str

    # Which workspace instance this run belongs to (PRD 5.3). A run without one
    # is single-tier legacy state; the executor supplies it for every new run.
    workspace_id: str | None = None
    tier: str | None = None      # "global" | "mini"
    epic_id: str | None = None

    state: StageState = StageState.QUEUED
    attempt: int = 1
    idempotency_key: str | None = None
    correlation_id: str | None = None
    # Stable ID for one full BRD→delivery lifecycle; set once when the LangGraph
    # thread starts and propagated through every stage run in that workflow.
    workflow_run_id: str | None = None

    # provenance (FR-019 / FR-P3)
    initiated_by: str | None = None
    initiated_by_persona: str | None = None
    plugin: str | None = None
    plugin_version: str | None = None
    model: str | None = None
    runner: str | None = None
    source_artifact_ids: list[str] = Field(default_factory=list)
    #: KB chunk ids retrieved into the prompt (become GROUNDS links on the artefact)
    kb_chunk_ids: list[str] = Field(default_factory=list)
    worktree: str | None = None

    queued_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None

    artifact_ids: list[str] = Field(default_factory=list)
    files_written: list[str] = Field(default_factory=list)
    tools_invoked: list[str] = Field(default_factory=list)
    tools_denied: list[str] = Field(default_factory=list)

    input_tokens: int = 0
    output_tokens: int = 0
    # Prompt-cache and cost accounting from the harness (FR-020 observability).
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cost_usd: float | None = None
    num_turns: int | None = None
    max_turns: int | None = None      # the cap this run was given
    #: Post-run grounding report (fe_core.kb.cards.GroundingReport.as_dict()).
    grounding: dict | None = None
    #: True when the turn cap fired but the run was salvaged/recovered because
    #: its deliverable was complete -- shown to the approver.
    capped: bool = False
    session_id: str | None = None

    error: str | None = None
    error_code: str | None = None
    log: list[str] = Field(default_factory=list)

    # RE Graph extraction scope (PRD stage only). Set from the run dialog;
    # gear_id is read server-side from workspace.gear_id (or from the run if overridden at onboarding).
    module_scope: str | None = None
    gear_id: str | None = None
    intake_source: str | None = None
    business_area: str | None = None
    role: str | None = None

    # Runner affinity set at queue time from the stage YAML (stage.required_runner).
    # Workers skip any run whose required_runner doesn't match their FE_RUNNER.
    required_runner: str | None = None

    # --- worker-service bookkeeping (FE_EXECUTION=worker) --------------------
    claimed_by: str | None = None          # worker id that picked the run up
    claimed_at: datetime | None = None
    heartbeat_at: datetime | None = None   # stale heartbeat -> requeue
    cancel_requested: bool = False
    graph_thread_id: str | None = None     # LangGraph thread (FE_ORCHESTRATOR=langgraph)
    # In langgraph mode the API records the human decision here instead of
    # approving directly; the worker feeds it to the graph as Command(resume).
    resume_intent: dict | None = None

    def transition(self, target: StageState, *, error: str | None = None,
                   error_code: str | None = None) -> None:
        """Move to `target`, refusing an invalid transition (FR-024)."""
        if target is not self.state and not can_transition(self.state, target):
            raise InvalidStateTransition(
                f"run {self.run_id}: cannot move {self.state.value} -> {target.value}"
            )
        self.state = target
        now = datetime.now(timezone.utc)
        if target is StageState.QUEUED and self.queued_at is None:
            self.queued_at = now
        if target is StageState.RUNNING and self.started_at is None:
            self.started_at = now
        if target in (StageState.COMPLETED, StageState.FAILED,
                      StageState.CANCELLED):
            self.finished_at = now
        if error:
            self.error = error
        if error_code:
            self.error_code = error_code


class InvalidStateTransition(RuntimeError):
    """Raised on an illegal state change; surfaced as HTTP 409."""
