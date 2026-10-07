"""Typed FSDDocument artifact — single source for /fsd page + AIG .docx export.

12 sections (docs/04 §4.4): context · scope · functional-requirements · process-flows ·
screen-specs · business-rules · data-model · integration-points · NFRs · AC-3 matrix ·
open-items · references. Every KB-covered field carries a citation; uncovered fields are
labelled stubs (BA-TODO / DEV-TODO). Adopted from xpf BRD row model (before/after per FR)
+ analysis schema shapes (ImpactCitation, ScopeDiff re-used verbatim).
"""

from __future__ import annotations

from pydantic import BaseModel, Field


SOURCE_TYPES = ("kb_explicit", "kb_inferred", "stub", "proposed")
PRIORITY_LEVELS = ("High", "Medium", "Low")
NFR_CATEGORIES = ("Performance", "Security", "Availability", "Scalability", "Compliance", "Accessibility")

# Re-exported for callers who need both schema modules.
from app.lifecycle.stages.analysis.schema import ImpactCitation, ScopeDiff  # noqa: E402,F401


class FunctionalRequirement(BaseModel):
    """One FR row — before/after delta with KB provenance tag (xpf BRD row pattern)."""

    id: str
    title: str = ""
    as_is: str | None = Field(default=None, description="verbatim from KB card or None if stub")
    to_be: str | None = Field(default=None, description="LLM-reasoned or None → BA-TODO stub")
    source_locus: str | None = None
    source_type: str = Field(default="stub", description="kb_explicit | kb_inferred | stub")
    priority: str = Field(default="Medium", description="High | Medium | Low")
    stub_marker: str | None = Field(default=None, description="BA-TODO | DEV-TODO | None")


class ProcessFlow(BaseModel):
    """A PROC-* or WF-* KB node as an FSD process-flow entry."""

    id: str
    title: str = ""
    kind: str = Field(default="Process", description="Process | Workflow")
    steps: list[str] = Field(default_factory=list)
    actors: list[str] = Field(default_factory=list)
    source_locus: str | None = None


class ScreenSpec(BaseModel):
    """A SCR-* KB node as an FSD screen specification."""

    id: str
    title: str = ""
    purpose: str = ""
    fields: list[str] = Field(default_factory=list)
    business_rules: list[str] = Field(default_factory=list, description="BR-* IDs governing this screen")
    source_locus: str | None = None


class BusinessRule(BaseModel):
    """A BR-* KB node as an FSD business rule entry."""

    id: str
    title: str = ""
    rule_text: str = ""
    applies_to: list[str] = Field(default_factory=list, description="SCR-*/PROC-* IDs this rule governs")
    source_locus: str | None = None


class DataModelItem(BaseModel):
    """An ENT-* KB node impacted by the change."""

    id: str
    label: str = ""
    kind: str = "Entity"
    impact: str = Field(default="referenced", description="modified | new | referenced")
    note: str | None = None
    source_locus: str | None = None


class IntegrationPoint(BaseModel):
    """An INT-*/API-*/SYS-* KB node representing a system boundary."""

    id: str
    label: str = ""
    kind: str = ""
    direction: str = Field(default="", description="inbound | outbound | bidirectional")
    protocol: str | None = None
    note: str | None = None
    source_locus: str | None = None


class NFR(BaseModel):
    """A non-functional requirement — LLM-reasoned; stubs where KB has no coverage."""

    id: str = ""
    category: str = Field(default="", description="one of NFR_CATEGORIES")
    description: str = ""
    rationale: str | None = None
    stub_marker: str | None = Field(default=None, description="BA-TODO if not KB-grounded")


class ACRow(BaseModel):
    """One AC-3 traceability row: FSD section → KB card → source locus."""

    fsd_section: str
    kb_id: str
    kb_label: str = ""
    source_locus: str | None = None
    link_type: str = Field(default="IMPLEMENTS", description="IMPLEMENTS | GOVERNED_BY | SCREEN_OF | DEPENDS_ON")


class Stub(BaseModel):
    """An open item / stub that requires SME or BA input (BA-TODO / DEV-TODO)."""

    id: str = ""
    section: str = ""
    description: str = ""
    marker: str = Field(default="BA-TODO", description="BA-TODO | DEV-TODO")
    priority: str = "Medium"


class PersonaNeed(BaseModel):
    """One row in the Target Personas & Needs table (merged from the BRD)."""

    persona: str = Field(default="", description="e.g. Agent / Underwriter / DevOps / Compliance")
    use_case: str = Field(default="", description="what this persona does")
    need: str = Field(default="", description="what they need from this specific change")
    source: str = Field(default="kb_card", description="kb_card | stakeholder_input")


class KeyDecision(BaseModel):
    """One entry in the Key Decisions & Open Items log (merged from the BRD)."""

    tag: str = Field(default="OPEN", description="DECIDED | OPEN | INSIGHT | NOTE | ROADMAP")
    description: str = Field(default="", description="full text")
    section: str = Field(default="", description="which section this decision relates to")
    marker: str = Field(default="BA-TODO", description="BA-TODO | DEV-TODO")


class Criterion(BaseModel):
    """docs/24 §E — a PRD entry/exit criterion (the net-new content a PRD adds over the FSD).

    Entry = preconditions for the change to start (e.g. "ANALYSIS accepted, KB grounded"); exit =
    done-conditions (from AC-3 coverage + open-item count). Non-KB-grounded criteria are BA-TODO."""

    id: str
    text: str
    met: bool = False
    source_type: str = Field(default="stub", description="kb_grounded | ba_reasoned | stub")
    source_locus: str | None = None


class FSDDocument(BaseModel):
    """The full AIG-standard FSD/PRD — single source for the /fsd (PRD) page and the AIG .docx export.

    The PRD (docs/24 §E) is the FSD superset: same sections plus ``entry_criteria``/``exit_criteria``.
    Per E2 it is a rename-in-place (display + capability alias), not a parallel stage — the on-disk
    artifact identity stays ``kind="fsd"`` (zero data migration)."""

    workspace_id: str
    analysis_ref: str | None = Field(default=None, description="artifact_id of the analysis this FSD derives from")
    kb_version: str
    persona: str
    template_id: str = "aig-fsd"
    template_version: str = "v1"
    requirement: str = ""
    change_class: str = Field(default="", description="New | Enhancement | Existing | Derived — from analysis")
    grounding_score: float = 1.0
    stub_count: int = Field(default=0, description="total BA-TODO / DEV-TODO items")

    # Sections (mirror template key order)
    context_summary: str = Field(default="", description="LLM: project background grounded in KB")
    business_case: str = Field(default="", description="business framing — why this change matters (merged BRD)")
    persona_needs: list[PersonaNeed] = Field(default_factory=list)  # merged BRD: target personas & needs
    key_decisions: list[KeyDecision] = Field(default_factory=list)  # merged BRD: decisions & open items log
    scope: ScopeDiff = Field(default_factory=ScopeDiff)
    functional_requirements: list[FunctionalRequirement] = Field(default_factory=list)
    process_flows: list[ProcessFlow] = Field(default_factory=list)
    screen_specs: list[ScreenSpec] = Field(default_factory=list)
    business_rules: list[BusinessRule] = Field(default_factory=list)
    data_model: list[DataModelItem] = Field(default_factory=list)
    integration_points: list[IntegrationPoint] = Field(default_factory=list)
    non_functional_reqs: list[NFR] = Field(default_factory=list)
    acceptance_criteria: list[ACRow] = Field(default_factory=list)
    entry_criteria: list[Criterion] = Field(default_factory=list)  # PRD (docs/24 §E) — net-new
    exit_criteria: list[Criterion] = Field(default_factory=list)   # PRD (docs/24 §E) — net-new
    open_items: list[Stub] = Field(default_factory=list)
    references: list[ImpactCitation] = Field(default_factory=list)

    # [NEW] Section 8b: Technical Deep-Dive (P1/P2 full impact analysis)
    # Contains complete list of components, screens, systems from semantic analysis
    # Structure: {"components": [...], "screens": [...], "systems": [...]}
    technical_analysis_detail: dict | None = Field(
        default=None,
        description="Technical deep-dive section with complete component, screen, system details from P1/P2 analysis"
    )

    # Import 3 (Genlite pattern): per-section provenance summary — computed deterministically
    # in assemble_fsd(). BA uses this to see at a glance which sections need review.
    # e.g. {"3.Functional Requirements": "KB-explicit: 2 | KB-inferred: 1 | stub: 1"}
    section_basis: dict[str, str] = Field(
        default_factory=dict,
        description="Per-section derivation summary: KB-explicit N | KB-inferred N | LLM-reasoned N | stub N",
    )

    abstained: bool = False
    generated_at: str | None = None
