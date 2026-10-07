"""Typed BRDDocument artifact — single source for /brd page + AIG .docx export.

10 sections (Option C Hybrid Lean — docs/tasks/brd-implementation.md):
business_case · scope_definition · business_requirements · business_rules ·
stakeholder_personas · success_criteria · risks_and_compliance · acceptance_criteria ·
key_decisions · references.

PRD-aligned format: AIG logo, version table, [DECIDED]/[OPEN] decision log,
KPI-numbered success criteria, IN/OUT/DEFERRED scope split, sign-off strip.

Word budget rule: LLM sections soft-capped (business_case 250w, success_criteria 500w) —
never mid-sentence truncation, never table/data truncation. Data sections are uncapped.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

SOURCE_TYPES = ("kb_explicit", "kb_inferred", "stub")
PRIORITY_LEVELS = ("High", "Medium", "Low")

# [DECIDED] / [OPEN] / [INSIGHT] / [NOTE] / [ROADMAP] — mirrors PRD decision log tags.
DECISION_TAGS = ("DECIDED", "OPEN", "INSIGHT", "NOTE", "ROADMAP")

# Japan Auto stakeholder personas — matches PRD "Target Personas" table.
STAKEHOLDER_TYPES = ("Underwriter", "Finance", "IT", "Compliance", "BA", "Architect", "Other")

# LLM section soft caps (words). None = uncapped (data sections).
SOFT_CAPS: dict[str, int | None] = {
    "business_case": 250,
    "scope_definition": None,
    "business_requirements": None,
    "business_rules": None,
    "stakeholder_personas": None,
    "success_criteria": 500,
    "risks_and_compliance": None,
    "acceptance_criteria": None,
    "key_decisions": None,
    "references": None,
}

# Re-exported shapes shared with FSD — no duplication.
from app.lifecycle.stages.analysis.schema import ImpactCitation, ScopeDiff  # noqa: E402, F401
from app.lifecycle.stages.fsd.schema import ACRow, Stub  # noqa: E402, F401


class BusinessRequirement(BaseModel):
    """One business requirement row — PRD language: Current State / Proposed Change."""

    id: str
    requirement: str = Field(default="", description="full business prose — never truncated")
    current_state: str | None = Field(default=None, description="current state from KB card prose")
    proposed_change: str | None = Field(
        default=None, description="LLM-reasoned proposed business change; None → BA-TODO stub"
    )
    source_locus: str | None = None
    source_type: str = Field(default="stub", description="kb_explicit | kb_inferred | stub")
    priority: str = Field(default="Medium", description="High | Medium | Low")
    stub_marker: str | None = Field(default=None, description="BA-TODO | None")


class BRDRule(BaseModel):
    """A BR-* KB node as a BRD business rule entry — full prose, never truncated."""

    id: str
    title: str = ""
    rule_text: str = Field(default="", description="full rule prose from KB card — never truncated")
    applies_to: list[str] = Field(default_factory=list, description="SCR-*/PROC-* IDs this rule governs")
    source_locus: str | None = None


class PersonaNeed(BaseModel):
    """One row in the Target Personas & Needs table — mirrors PRD 'Target Personas' format."""

    persona: str = Field(default="", description="e.g. Underwriter / Finance / IT / Compliance / BA / Architect")
    use_case: str = Field(default="", description="what this persona does (PRD 'Use' column)")
    need: str = Field(default="", description="what they need from this specific change")
    source: str = Field(default="kb_card", description="kb_card | stakeholder_input")


class KeyDecision(BaseModel):
    """One entry in the Key Decisions & Open Items log — mirrors PRD [DECIDED]/[OPEN] tags."""

    tag: str = Field(default="OPEN", description="DECIDED | OPEN | INSIGHT | NOTE | ROADMAP")
    description: str = Field(default="", description="full text — never truncated")
    section: str = Field(default="", description="which BRD section this decision relates to")
    marker: str = Field(default="BA-TODO", description="BA-TODO | DEV-TODO (meaningful for OPEN items)")


class BRDACRow(BaseModel):
    """Acceptance criteria row for BRD — same traceability fields as FSD ACRow plus ac_text.

    ac_text is derived from KB card prose only; it is NEVER generated or inferred.
    When ac_text is empty string, the KB card has no prose — the consumer must enrich the
    KB via /kb-build before meaningful acceptance criteria can be produced.
    """

    fsd_section: str = Field(default="", description="BRD section this row belongs to")
    kb_id: str = Field(default="", description="KB card ID (stable ID)")
    kb_label: str = Field(default="", description="KB card display label")
    source_locus: str | None = None
    link_type: str = Field(default="DEPENDS_ON", description="graph edge label")
    ac_text: str = Field(
        default="",
        description=(
            "Acceptance criterion text extracted from KB card prose. "
            "Empty string means the KB card has no prose — run /kb-build to enrich."
        ),
    )


class BRDDocument(BaseModel):
    """The full AIG-standard BRD — single source for /brd page and AIG .docx export.

    10 sections (Option C Hybrid Lean):
      1. business_case         — LLM prose, soft cap 250w
      2. scope_definition      — data, IN/OUT/DEFERRED, uncapped
      3. business_requirements — mixed table, uncapped
      4. business_rules        — data cards, uncapped
      5. stakeholder_personas  — mixed table, uncapped
      6. success_criteria      — LLM list, soft cap 500w
      7. risks_and_compliance  — mixed list, uncapped
      8. acceptance_criteria   — data table (PRD 2-col Output/AC), uncapped
      9. key_decisions         — mixed decision log, uncapped
     10. references            — data chips, uncapped
    """

    workspace_id: str
    fsd_ref: str | None = Field(
        default=None, description="artifact_id of the accepted FSD this BRD derives from"
    )
    analysis_ref: str | None = Field(
        default=None, description="transitively from FSD — for full GROUNDS chain"
    )
    kb_version: str
    persona: str
    template_id: str = "aig-brd"
    template_version: str = "v1"
    requirement: str = ""
    change_class: str = Field(
        default="", description="New | Enhancement | Existing | Derived — inherited from FSD"
    )
    grounding_score: float = 1.0
    stub_count: int = Field(default=0, description="total BA-TODO items across all sections")

    # ── 10 sections (Option C Hybrid Lean) ────────────────────────────────────
    business_case: str = Field(
        default="", description="LLM: 3-5 sentence business impact brief — soft cap 250w"
    )
    scope_definition: ScopeDiff = Field(default_factory=ScopeDiff)
    business_requirements: list[BusinessRequirement] = Field(default_factory=list)
    business_rules: list[BRDRule] = Field(default_factory=list)
    stakeholder_personas: list[PersonaNeed] = Field(default_factory=list)
    success_criteria: list[str] = Field(
        default_factory=list, description="LLM: PRD-style KPI 1 / KPI 2 — soft cap 500w"
    )
    risks_and_compliance: list[str] = Field(default_factory=list)
    acceptance_criteria: list[BRDACRow] = Field(
        default_factory=list,
        description="2-col Output/AC table — ac_text from KB prose only; empty = KB needs enrichment",
    )
    key_decisions: list[KeyDecision] = Field(
        default_factory=list, description="[DECIDED]/[OPEN]/[INSIGHT]/[NOTE]/[ROADMAP] log"
    )
    references: list[ImpactCitation] = Field(default_factory=list)

    # ── metadata ──────────────────────────────────────────────────────────────
    section_basis: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Per-section provenance summary — Import 3 (Genlite pattern). "
            "e.g. {'3.Business Requirements': 'KB-explicit: 2 | KB-inferred: 1 | stub: 0'}"
        ),
    )
    section_truncated: dict[str, bool] = Field(
        default_factory=dict,
        description=(
            "True only for LLM soft-cap hits (business_case, success_criteria). "
            "Data sections are never truncated and never appear here."
        ),
    )
    abstained: bool = False
    generated_at: str | None = None
