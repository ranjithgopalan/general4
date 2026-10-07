"""Typed SRDDocument artifact — single source for /srd page + AIG .docx export.

9 sections (docs/24 §3): system_context · scope_definition · functional_requirements ·
component_design · integration_design · sequence_diagrams · non_functional_reqs ·
open_items · references. Plus two Mermaid DSL diagram fields (asIs_diagram, toBe_diagram).
Every KB-covered field carries a citation; uncovered fields are ARCH-TODO stubs.
Cross-repo: Genlite mandatory-diagram pattern (diagrams are first-class sections),
IMAD graph-derived DSL, XPF domain-profile injection, S3 coverage gate.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

# Re-export shared types to keep callers in one import.
from app.lifecycle.stages.analysis.schema import ImpactCitation, ScopeDiff  # noqa: F401
from app.lifecycle.stages.fsd.schema import FunctionalRequirement, Stub  # noqa: F401

# KB kind groups the Architect persona reads (CLAUDE.md §6, docs/10).
ARCH_KINDS = frozenset({
    "System", "Integration", "Component", "ApiOp",
    "Workflow", "Sequence", "FunctionalReq", "Entity",
})

NFR_CATEGORIES = (
    "Performance", "Security", "Availability",
    "Scalability", "Compliance", "Maintainability",
)

# Soft word caps for LLM-written sections (same Import 1 pattern as BRD).
SOFT_CAPS: dict[str, int] = {
    "system_context": 300,
}


class SystemComponent(BaseModel):
    """A CMP-*/SYS-* KB node as a component design entry."""

    id: str
    label: str = ""
    kind: str = Field(default="Component", description="Component | System | ApiOp")
    responsibility: str = ""
    interfaces: list[str] = Field(
        default_factory=list, description="INT-* ids this component connects to"
    )
    source_locus: str | None = None
    source_type: str = Field(
        default="stub", description="kb_explicit | kb_lookup | inferred | stub"
    )
    # KB VERIFICATION (NEW)
    confidence: float = Field(default=0.9, description="0.0-1.0: how well verified against KB")
    kb_reference: str | None = Field(default=None, description="KB card id used for verification")


class IntegrationPoint(BaseModel):
    """An INT-*/API-* KB node as an architecture integration point."""

    id: str
    label: str = ""
    kind: str = Field(default="", description="Integration | ApiOp")
    protocol: str | None = Field(
        default=None, description="REST | SOAP | ESB | DB | Event | gRPC"
    )
    direction: str = Field(
        default="", description="inbound | outbound | bidirectional"
    )
    from_component: str = Field(default="", description="CMP-*/SYS-* id")
    to_component: str = Field(default="", description="CMP-*/SYS-* id")
    source_locus: str | None = None
    # KB VERIFICATION (NEW)
    source_type: str = Field(
        default="kb_explicit", description="kb_explicit | kb_lookup | inferred"
    )
    confidence: float = Field(default=0.9, description="0.0-1.0: how well verified against KB")
    kb_reference: str | None = Field(default=None, description="KB card id used for verification")


class SequenceDiagram(BaseModel):
    """Per-story Mermaid sequenceDiagram block (Genlite: diagrams are mandatory)."""

    story_id: str
    title: str = ""
    mermaid_dsl: str = ""
    participants: list[str] = Field(default_factory=list)
    source_type: str = Field(
        default="stub", description="kb_derived | llm_reasoned | stub"
    )


class NFRItem(BaseModel):
    """Architecture-level non-functional requirement."""

    id: str = ""
    category: str = Field(default="", description="one of NFR_CATEGORIES")
    requirement: str = ""
    rationale: str | None = None
    metric: str | None = Field(
        default=None, description="Measurable target e.g. 'p99 < 500ms'"
    )
    source_type: str = Field(
        default="stub",
        description="kb_explicit | fsd_derived | llm_reasoned | stub",
    )


class ApiSpec(BaseModel):
    """C4 — an API-* operation as a design-level contract (method/path/from→to/payload)."""

    id: str
    label: str = ""
    http_method: str = Field(default="", description="GET | POST | PUT | DELETE | (SOAP action)")
    path: str = ""
    from_component: str = Field(default="", description="caller CMP-*/SYS-* id")
    to_component: str = Field(default="", description="handler CMP-*/SYS-* id")
    request_fields: list[str] = Field(default_factory=list)
    response_fields: list[str] = Field(default_factory=list)
    source_locus: str | None = None
    source_type: str = Field(
        default="stub", description="kb_explicit | kb_lookup | test-fixture | inferred | stub"
    )
    # KB VERIFICATION (NEW)
    confidence: float = Field(default=0.5, description="0.0-1.0: how well verified against KB")
    kb_reference: str | None = Field(default=None, description="KB card id used for verification")


class DataEntity(BaseModel):
    """C5 — an ENT-*(DB table) node as a data-model entry for the ER section."""

    id: str
    label: str = ""
    key_columns: list[str] = Field(default_factory=list)
    relationships: list[str] = Field(
        default_factory=list, description="FK targets — 'column → TABLE' (from REFERENCES edges)"
    )
    source_locus: str | None = None
    source_type: str = Field(default="stub", description="kb_explicit | stub")


class SchemaChange(BaseModel):
    """C6 — a proposed DB schema delta for the change (ADD/ALTER/NEW), KB-cited."""

    table: str
    op: str = Field(default="ALTER", description="ADD | ALTER | NEW")
    column: str = ""
    column_type: str = Field(default="", description="VARCHAR(50), INTEGER, etc.")
    rationale: str = ""
    kb_id: str | None = Field(default=None, description="ENT-* / FR-* / BR-* the change traces to")
    source_type: str = Field(default="stub", description="kb_lookup | kb_grounded | inferred | stub")
    # KB VERIFICATION (NEW)
    confidence: float = Field(default=0.5, description="0.0-1.0: how well verified against KB")
    kb_reference: str | None = Field(default=None, description="ENT-* card id from which table name verified")


class SRDStoryRef(BaseModel):
    """Traceability row: story → KB IDs → SRD sections."""

    story_id: str
    title: str = ""
    kb_ids: list[str] = Field(default_factory=list)
    diagram_sections: list[str] = Field(
        default_factory=list,
        description="SRD section keys: component_design | integration_design | sequence_diagrams",
    )
    link_type: str = "IMPLEMENTS"


class SRDDocument(BaseModel):
    """The full AIG-standard SRD — single source for the /srd page and the AIG .docx export."""

    workspace_id: str
    stories_ref: str | None = Field(
        default=None, description="artifact_id of the accepted StoriesDocument"
    )
    brd_ref: str | None = None
    fsd_ref: str | None = None
    analysis_ref: str | None = None
    kb_version: str
    persona: str
    template_id: str = "aig-srd"
    template_version: str = "v1"
    requirement: str = ""
    change_class: str = ""
    grounding_score: float = 1.0
    stub_count: int = 0

    # ── 9 sections (srd.v1.json order) ───────────────────────────────────────
    system_context: str = Field(
        default="", description="LLM: architect narrative grounded in KB (≤300w)"
    )
    scope_definition: ScopeDiff = Field(default_factory=ScopeDiff)
    functional_requirements: list[FunctionalRequirement] = Field(default_factory=list)
    component_design: list[SystemComponent] = Field(default_factory=list)
    integration_design: list[IntegrationPoint] = Field(default_factory=list)
    api_specs: list[ApiSpec] = Field(default_factory=list)               # C4
    data_model: list[DataEntity] = Field(default_factory=list)           # C5
    schema_changes: list[SchemaChange] = Field(default_factory=list)     # C6
    sequence_diagrams: list[SequenceDiagram] = Field(default_factory=list)
    non_functional_reqs: list[NFRItem] = Field(default_factory=list)
    open_items: list[Stub] = Field(default_factory=list)
    references: list[ImpactCitation] = Field(default_factory=list)

    # ── Mermaid DSL diagrams (shown as code blocks in the UI) ────────────────
    asIs_diagram: str = Field(
        default="",
        description="Mermaid flowchart DSL — AS-IS system topology from graph walk",
    )
    toBe_diagram: str = Field(
        default="",
        description="Mermaid flowchart DSL — TO-BE topology after the change (LLM diff)",
    )
    endToEnd_diagram: str = Field(
        default="",
        description="C2 — Mermaid flowchart DSL — full end-to-end topology across all retrieved "
        "SYS/CMP/INT/API nodes (layered by graph bands)",
    )
    erd_diagram: str = Field(
        default="",
        description="C5 — Mermaid erDiagram DSL — data model (prefer corpus ERD DIAG-JAUTO-002)",
    )

    # ── Traceability: story → KB → SRD section ───────────────────────────────
    story_refs: list[SRDStoryRef] = Field(default_factory=list)

    # ── Provenance (Import 3 — Genlite section_basis pattern) ────────────────
    section_basis: dict[str, str] = Field(default_factory=dict)
    abstained: bool = False
    generated_at: str | None = None
