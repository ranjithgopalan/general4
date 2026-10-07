"""Typed StoriesDocument artifact — single source for /stories page and Rally CSV export.

The STORIES stage (S4) converts the accepted BRD into a Rally-ready, fully grounded backlog.
Every story row is field-grounded against KB cards. Every field verdict is recorded.

Grounding modes (class-aware — fixed 2026-08-08):
  CONTENT     (Enhancement / Derived)  — word-overlap check; verdicts: OK | WRONG_CONTEXT
  CONTEXTUAL  (New / Migration)        — structural check only; verdicts: OK_CONTEXTUAL | STUB

Points by class (Q1, fixed): New=8 / Enhancement=5 / Derived=3.
Export: CSV only (Rally format, HTML descriptions).
"""

from __future__ import annotations

from pydantic import BaseModel, Field

# Re-export shared shapes so callers only need one import.
from app.lifecycle.stages.analysis.schema import ImpactCitation, ScopeDiff  # noqa: F401
from app.lifecycle.stages.fsd.schema import Stub  # noqa: F401

CHANGE_CLASSES = ("New", "Enhancement", "Derived")
PRIORITY_LEVELS = ("High", "Medium", "Low")

# Field grounding verdicts — recorded per prose field per story.
# OK             — content-overlap verified (Enhancement/Derived)
# OK_CONTEXTUAL  — structural citation exists (New/Migration)
# WRONG_CONTEXT  — content-overlap failed (Enhancement/Derived only; never emitted for New)
# FABRICATED     — same as WRONG_CONTEXT; reserved for hard block scenarios
# STUB           — no KB citation at all; always actionable regardless of class
GROUND_VERDICTS = ("OK", "OK_CONTEXTUAL", "WRONG_CONTEXT", "FABRICATED", "STUB")

# Points by change class (Q1 decision — fixed, v1).
POINTS_BY_CLASS: dict[str, int] = {"New": 8, "Enhancement": 5, "Derived": 3}

# Split threshold — stories at or above this get SPLIT-REQUIRED (genlite pattern).
SPLIT_THRESHOLD: int = 8


class StoryRow(BaseModel):
    """One Rally user story — all 7 prose fields + grounding verdicts + metadata."""

    story_id: str = Field(default="", description="STR-JAUTO-001 format, assigned after enrichment")
    title: str = Field(
        default="",
        description=(
            "Impact-first title (genlite rule): MUST start with user-impact verb. "
            "Good: 'Enable underwriters to apply EV discount during quoting'. "
            "Bad: 'Implement EV discount logic' (implementation-language)."
        ),
    )
    as_a: str = Field(default="", description="Actor / persona role")
    i_want: str = Field(default="", description="Business need — what the actor wants to do")
    so_that: str = Field(default="", description="Expected business outcome")
    ac_given: str = Field(default="", description="Gherkin Given clause — precondition")
    ac_when: str = Field(default="", description="Gherkin When clause — trigger action")
    ac_then: str = Field(default="", description="Gherkin Then clause — observable outcome")

    change_class: str = Field(
        default="New",
        description="New | Enhancement | Derived — drives points + grounding mode + link_type",
    )
    points: int = Field(
        default=8, description="Q1: New=8 / Enhancement=5 / Derived=3. BA overrides in Rally."
    )
    points_rationale: str = Field(
        default="",
        description="e.g. 'New requirement (8 pts) — full AC set needed' (genlite pattern)",
    )
    points_action: str = Field(
        default="Accept", description="Accept | Decompose (genlite: >8 pts → Decompose)"
    )
    priority: str = Field(default="Medium", description="High | Medium | Low")

    # Traceability — drives GROUNDS DB links and AC-3 matrix.
    source_refs: list[str] = Field(
        default_factory=list,
        description="KB card IDs this story is grounded to (BR-*/FR-*/SYS-* etc.)",
    )
    source_locus: str | None = Field(
        default=None, description="Primary KB card source locus (file §section char:start-end)"
    )

    # Per-field grounding (Q3 — class-aware, 7 fields × N stories).
    field_ground: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Verdict per prose field: OK | OK_CONTEXTUAL | WRONG_CONTEXT | FABRICATED | STUB. "
            "Keys: title, as_a, i_want, so_that, ac_given, ac_when, ac_then."
        ),
    )

    source_type: str = Field(
        default="stub",
        description="kb_explicit | kb_inferred | stub — provenance of this story's KB grounding",
    )
    stub_marker: str | None = Field(
        default=None,
        description=(
            "BA-TODO | FABRICATED:{field} | SPLIT-REQUIRED | NO-KB-CITATION | DUPLICATE — "
            "first marker wins; field cleared when BA resolves the story in Rally."
        ),
    )


class StoryACRow(BaseModel):
    """One row in the AC-3 traceability matrix — per story, per KB citation set."""

    story_id: str
    kb_ids: list[str] = Field(default_factory=list)
    source_loci: list[str] = Field(default_factory=list)
    link_type: str = Field(
        default="IMPLEMENTS",
        description="IMPLEMENTS (New/Enhancement) | DERIVES_FROM_RULE (Derived class)",
    )
    diagram_ref: str = Field(
        default="",
        description="Blank at S4 (STORIES). Architecture stage fills this in v2.",
    )


class StoriesDocument(BaseModel):
    """The full Stories artifact — N story rows + AC-3 matrix + open items.

    This is the SSOT for the /stories page and the Rally CSV export.
    The DB and S3 store this document; markdown/HTML are disposable renders.
    """

    workspace_id: str
    brd_ref: str | None = Field(
        default=None, description="artifact_id of the accepted BRD this Stories artifact derives from"
    )
    analysis_ref: str | None = Field(
        default=None, description="transitively from BRD→FSD (for full GROUNDS chain)"
    )
    kb_version: str
    persona: str
    template_id: str = "aig-stories"
    template_version: str = "v1"
    requirement: str = ""
    change_class: str = Field(
        default="", description="Workspace-level change class inherited from BRD"
    )

    # Grounding score — grounded_fields / total_prose_fields across all stories.
    # Both OK (content) and OK_CONTEXTUAL (structural) count as grounded.
    grounding_score: float = Field(
        default=1.0,
        description=(
            "Grounded fields / total prose fields. OK + OK_CONTEXTUAL count as grounded. "
            "Honest across mixed-class backlogs (New + Enhancement in same workspace)."
        ),
    )
    stub_count: int = Field(
        default=0, description="Count of stories with any non-None stub_marker"
    )

    stories: list[StoryRow] = Field(default_factory=list)
    traceability: list[StoryACRow] = Field(default_factory=list)
    open_items: list[Stub] = Field(default_factory=list)
    references: list[ImpactCitation] = Field(default_factory=list)

    # Per-section provenance (genlite Import 3 pattern).
    section_basis: dict[str, str] = Field(default_factory=dict)

    abstained: bool = Field(
        default=False,
        description="True when BRD has no references — cite-or-abstain; no stories generated",
    )
    generated_at: str | None = None
