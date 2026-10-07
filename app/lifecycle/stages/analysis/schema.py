"""Typed ImpactAnalysis artifact (single source → HTML page + AIG .docx).

SME-grade sections (docs/22): classification · summary · scope 3-col · how it touches the existing
system · what is modified (before→after) · downstream "what breaks" · where changes are needed ·
conflicts with existing nodes · coverage/blindspot · risks & effort · gaps · references. Every
claim-bearing field carries KB citations (cite-or-abstain). Shapes adopted from lmod `re_schemas.py`
+ xpf interface-impact.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

# The four change classes the ANALYSIS stage resolves (docs/04 §4; CLAUDE.md §5). Cosmetic = v2.
CHANGE_CLASSES = ("New", "Enhancement", "Existing", "Derived")
EFFORT_SIZES = ("XS", "S", "M", "L", "XL")


class ImpactCitation(BaseModel):
    id: str
    kind: str = ""
    label: str = ""
    source_locus: str | None = None
    summary: str | None = None  # short card-body excerpt — what this node IS (grounded, from kb.read_many)


class ScopeItem(BaseModel):
    id: str | None = None
    label: str
    kind: str | None = None
    note: str | None = None


class ScopeDiff(BaseModel):
    new: list[ScopeItem] = Field(default_factory=list)
    enhancement: list[ScopeItem] = Field(default_factory=list)
    existing: list[ScopeItem] = Field(default_factory=list)


class AffectedNode(BaseModel):
    """A KB node in the impact set — reached from a matched card via typed edge(s)."""

    id: str
    label: str
    kind: str
    hops: int = 1
    via: list[str] = Field(default_factory=list, description="edge labels connecting it to the change")
    source_locus: str | None = None
    summary: str | None = None  # short card-body excerpt — what this node IS (grounded, from kb.read_many)
    layer: str | None = None    # architectural layer: "ui" | "service" | "data"
    system_id: str | None = None  # owning system KB ID — propagated to FSD/stories for trace graph


class LayerGroup(BaseModel):
    """Affected nodes split by architectural layer for the three-layer impact view."""
    ui: list[AffectedNode] = Field(default_factory=list, description="Screens + Angular/TS components")
    service: list[AffectedNode] = Field(default_factory=list, description="Java services, APIs, integrations, workflows")
    data: list[AffectedNode] = Field(default_factory=list, description="Database tables (ENT-*) + database systems")


class TouchPoint(BaseModel):
    """How the change touches the existing system — a boundary SYS/INT/ROLE node."""

    id: str
    label: str
    kind: str
    relation: str = Field(default="", description="how it is touched (edge label / role)")
    note: str | None = None


class Modification(BaseModel):
    """What is being modified — a node with an as-is → to-be delta."""

    id: str
    label: str
    kind: str
    before: str | None = None
    after: str | None = None
    note: str | None = None


class ModifiedTarget(BaseModel):
    """A directly modified item for PRD (what is being modified section)."""

    id: str
    kind: str  # Screen, Entity, ApiOp, Component, etc.
    label: str
    as_is: str = ""  # Current state from KB prose
    to_be: str = ""  # What changes
    change_type: str = "MODIFIED"  # NEW, MODIFIED, DELETED
    reason: str = ""  # Why it changed
    source_locus: str | None = None


class ChangeLocation(BaseModel):
    """Where a change is needed — a concrete SCR/CMP/API/INT node + its source locus."""

    kind: str
    id: str
    label: str
    locus: str | None = None
    note: str | None = None


class Conflict(BaseModel):
    """A conflict with existing nodes (docs/22 §4) — resolved via DISPUTED → human adjudication."""

    kind: str = Field(description="contradiction | drift | sibling-overlap")
    subject_ids: list[str] = Field(default_factory=list)
    detail: str = ""
    status: str = "DISPUTED"
    severity: str = "medium"


class Risk(BaseModel):
    """A delivery risk the SME should weigh (imad Risk shape)."""

    severity: str = "medium"  # low | medium | high
    description: str = ""
    affected_ids: list[str] = Field(default_factory=list)
    mitigation: str | None = None


class Blindspot(BaseModel):
    """A matched card that was not linked to any affected nodes (no dependencies found)."""
    id: str
    label: str | None = None
    kind: str | None = None
    reason: str = "No dependencies found in the system graph"


class BlindSpot(BaseModel):
    """A technical blind spot detected during semantic graph walk (gap, incompatibility, risk)."""
    severity: str = "MEDIUM"  # CRITICAL | HIGH | MEDIUM | LOW
    type: str = ""  # TABLE_RESOLUTION | CODE_COVERAGE | INTEGRATION_COMPATIBILITY | etc.
    description: str = ""
    action: str = ""
    affected_nodes: list[str] = Field(default_factory=list)


class Coverage(BaseModel):
    total: int = 0
    linked: int = 0
    coverage_pct: float = 0.0
    blindspots: list[Blindspot | str] = Field(default_factory=list)  # list[str] for backward compat


class Classification(BaseModel):
    change_class: str = "New"
    confidence: float = 0.0
    rationale: str = ""


class ImpactAnalysis(BaseModel):
    """The full SME-grade ANALYSIS-stage deliverable (single source for /impact + AIG .docx)."""

    workspace_id: str
    kb_version: str
    persona: str
    template_id: str
    template_version: str
    requirement: str = ""
    classification: Classification = Field(default_factory=Classification)
    narrative: str = Field(default="", description="grounded executive summary (cites [kbid])")
    scope: ScopeDiff = Field(default_factory=ScopeDiff)
    touch_points: list[TouchPoint] = Field(default_factory=list, description="how it touches the existing system")
    modifications: list[Modification] = Field(default_factory=list, description="what is modified (before→after)")
    downstream: list[AffectedNode] = Field(default_factory=list, description="what breaks (reverse walk)")
    change_locations: list[ChangeLocation] = Field(default_factory=list, description="where changes are needed")
    conflicts: list[Conflict] = Field(default_factory=list)
    coverage: Coverage = Field(default_factory=Coverage)
    risks: list[Risk] = Field(default_factory=list)
    effort: str = Field(default="M", description="T-shirt size XS–XL")
    effort_rationale: str | None = None
    affected: list[AffectedNode] = Field(default_factory=list, description="raw impact set")
    matched: list[ImpactCitation] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    references: list[ImpactCitation] = Field(default_factory=list)

    # FIX B (Aug 12, 2026): Grouped impact results (code, systems, integrations, etc.)
    # This replaces the flat "existing" list with organized groupings
    code_affected: list[AffectedNode] = Field(default_factory=list, description="CMP-*, API-* code components")
    systems_affected: list[AffectedNode] = Field(default_factory=list, description="SYS-* systems")
    integrations_affected: list[AffectedNode] = Field(default_factory=list, description="INT-* integrations")
    screens_affected: list[AffectedNode] = Field(default_factory=list, description="SCR-* screens")
    workflows_affected: list[AffectedNode] = Field(default_factory=list, description="WF-* workflows")
    rules_affected: list[AffectedNode] = Field(default_factory=list, description="BR-*, FR-* rules/requirements")
    processes_affected: list[AffectedNode] = Field(default_factory=list, description="PROC-* processes")

    # Semantic walk results (Aug 12, 2026 — SemanticWalker integration)
    # Legacy: kept for backward compat, but code_affected/systems_affected above are now AffectedNode lists
    # code_affected_ids: list[str] = Field(default_factory=list, description="[DEPRECATED] use code_affected")
    # systems_affected_ids: list[str] = Field(default_factory=list, description="[DEPRECATED] use systems_affected")

    # Technical blind spots (embedded in artifact, not separate table)
    blind_spots: list[BlindSpot] = Field(default_factory=list, description="gaps, incompatibilities, risks")

    # [NEW] Technical Analysis Sections (P1/P2 semantic search results)
    # These are the unfiltered results from code_affected, screens_affected, systems_affected
    # used by FSD stage to build the full technical detail (Section 8b)
    technical_components: list[AffectedNode] = Field(
        default_factory=list,
        description="P1 semantic search results: components found (CMP-*, API-*), not filtered by scope class"
    )
    technical_screens: list[AffectedNode] = Field(
        default_factory=list,
        description="P2 semantic search results: screens found (SCR-*), not filtered by scope class"
    )
    technical_systems: list[AffectedNode] = Field(
        default_factory=list,
        description="Systems affected (SYS-*): always shown in technical detail, never filtered"
    )

    # Three-layer architectural grouping (UI / Backend Service / Data)
    layer_groups: LayerGroup = Field(default_factory=LayerGroup)
    # ENT-* database tables directly matched — included separately since compute_affected
    # filters them out as "implementation detail" but they must show in the data layer view.
    data_entities: list[AffectedNode] = Field(default_factory=list)

    # PRD Impact Sections (NEW Aug 18, 2026)
    # what_is_modified: direct targets (screens, entities, APIs, components)
    # where_changes_needed: downstream impact (everything else affected)
    what_is_modified: list[ModifiedTarget] = Field(
        default_factory=list,
        description="Direct targets for PRD 'What is being modified' section"
    )
    where_changes_needed: list[AffectedNode] = Field(
        default_factory=list,
        description="Downstream impact for PRD 'Where changes are needed' section"
    )

    abstained: bool = False
    grounding_score: float = 1.0
    generated_at: str | None = None
