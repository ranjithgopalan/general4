"""Typed TestPlanDocument artifact — single source for /qa page + AIG .docx export.

10 sections: test_scope · story_test_cases · screen_validations · business_rule_tests ·
workflow_scenarios · role_access_tests · integration_tests · regression_scope · gap_log · references.

Source hierarchy (confirmed design):
  story_test_cases   → StoriesDocument Gherkin (no KB fallback; broken Gherkin = QA-BLOCKED)
  screen_validations → FSDDocument.screen_specs (FSD AC rows; no KB fallback)
  business_rule_tests→ BRDDocument.business_rules → card_bodies fallback if BRD rules empty
  workflow_scenarios → SRDDocument.sequence_diagrams (no KB fallback; ARCH-STUB when empty)
  role_access_tests  → DevDocument.implementation_plan × StoriesDocument.as_a
  integration_tests  → SRDDocument.integration_design → card_bodies fallback if SRD empty

accept() gate: FAIL/BLOCKED executions block sign-off unless override_justification supplied.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

# Re-export shared shapes.
from app.lifecycle.stages.analysis.schema import ImpactCitation  # noqa: F401
from app.lifecycle.stages.fsd.schema import Stub  # noqa: F401

# QA persona allowed kinds (personas.json §qa).
QA_KINDS = frozenset({"Screen", "BusinessRule", "FunctionalReq", "Workflow", "Process", "Role"})

# Test execution statuses (QATM pattern).
TEST_STATUSES = ("TODO", "PASS", "FAIL", "BLOCKED", "SKIP")

# Source tags distinguish where each test case came from.
SOURCE_TYPES = (
    "gherkin",       # from Stories Gherkin AC
    "fsd_screen",    # from FSD screen_specs
    "brd_rule",      # from BRD business_rules
    "srd_sequence",  # from SRD sequence_diagrams
    "dev_role",      # from DevDocument impl_tasks × story.as_a
    "srd_integration",  # from SRD integration_design
    "kb_direct",     # KB card fallback (business_rule_tests / integration_tests only)
)


class TestCase(BaseModel):
    """One QA test case derived from a Gherkin story.

    Derived deterministically from StoriesDocument Gherkin AC fields:
      ac_given  → preconditions
      ac_when   → steps (single step describing the trigger action)
      ac_then   → expected_result

    When Gherkin fields are empty after Stories enrichment, stub_marker = "QA-BLOCKED"
    and gap_log gains a "no_gherkin" entry — never fabricates test steps.
    """

    id: str = Field(description="TC-{story_id}-{n} format")
    title: str = ""
    story_id: str | None = None
    source_ref: str | None = None          # KB card ID that grounds this story
    test_type: str = Field(
        default="functional",
        description="functional | negative | edge_case | regression",
    )
    preconditions: list[str] = Field(default_factory=list, description="from Gherkin Given")
    steps: list[str] = Field(default_factory=list, description="from Gherkin When → test actions")
    expected_result: str = Field(default="", description="from Gherkin Then")
    source_type: str = Field(default="gherkin", description="always 'gherkin' for story_test_cases")
    priority: str = Field(default="Medium", description="inherited from StoryRow.priority")
    points: int = Field(default=0, description="inherited from StoryRow.points")
    stub_marker: str | None = Field(
        default=None,
        description="QA-BLOCKED when Gherkin fields are empty; None when fully populated",
    )


class ScreenValidation(BaseModel):
    """Screen validation derived from FSD screen_specs.

    One row per ScreenSpec in the FSDDocument.  qa uses the FSD screen spec
    (title, purpose, fields, business_rules) to build concrete validation points.
    No KB direct read — if FSD.screen_specs is empty, surfaces as a gap.
    """

    screen_id: str = Field(description="SCR-* KB card ID from FSD ScreenSpec.id")
    label: str = ""
    purpose: str = ""
    field_validations: list[str] = Field(
        default_factory=list,
        description="One validation point per FSD field — 'Field {name}: verify visible/required/editable'",
    )
    rule_validations: list[str] = Field(
        default_factory=list,
        description="One point per governing business rule ID — 'Assert BR-xxx enforced on this screen'",
    )
    source_type: str = Field(default="fsd_screen", description="always 'fsd_screen'")
    source_locus: str | None = None
    stub_marker: str | None = None     # "FSD-STUB" when ScreenSpec has no fields and no rules


class BusinessRuleTest(BaseModel):
    """Business rule test derived from BRD business_rules.

    Primary: BRDDocument.business_rules (rule_text from KB prose).
    Fallback: card_bodies[br_id] when BRD.business_rules is empty — surfaced as 'kb_direct'.
    When both are empty, stub_marker = "KB-GAP" and gap_log gains a "empty_brd_rules" entry.
    """

    rule_id: str = Field(description="BR-* KB card ID")
    label: str = ""
    rule_text: str = Field(
        default="",
        description="From BRD rule_text (kb_explicit) or card_bodies prose (kb_direct). Empty = KB-GAP.",
    )
    test_assertions: list[str] = Field(
        default_factory=list,
        description=(
            "Verifiable assertions derived from rule_text. "
            "Empty when rule_text is empty — assertion fabrication is not allowed."
        ),
    )
    source_type: str = Field(
        default="brd_rule",
        description="brd_rule (from BRD.business_rules) | kb_direct (from card_bodies fallback)",
    )
    source_locus: str | None = None
    stub_marker: str | None = None     # "KB-GAP" when rule_text is empty


class WorkflowScenario(BaseModel):
    """Workflow scenario derived from SRD sequence_diagrams.

    One scenario per SequenceDiagram in SRDDocument. Derives test steps from the
    Mermaid DSL participant messages. When mermaid_dsl is empty, stub_marker = "ARCH-STUB".
    No KB direct fallback — empty SRD sequence diagrams surface as gaps.
    """

    workflow_id: str = Field(description="story_id from SRDDocument SequenceDiagram")
    title: str = ""
    participants: list[str] = Field(default_factory=list, description="from SRD SequenceDiagram")
    scenario_steps: list[str] = Field(
        default_factory=list,
        description="Test steps derived from SequenceDiagram mermaid_dsl (participant interactions)",
    )
    expected_outcome: str = Field(
        default="",
        description="Synthesized from SequenceDiagram title + participants when mermaid_dsl non-empty",
    )
    source_type: str = Field(default="srd_sequence", description="always 'srd_sequence'")
    stub_marker: str | None = None   # "ARCH-STUB" when mermaid_dsl empty


class RoleAccessTest(BaseModel):
    """Role access test cross-referenced from DevDocument impl_tasks × Stories.as_a.

    One row per (role, screen) pair extracted from DevDocument.implementation_plan.
    QA verifies that only authorised roles can access each screen.
    No direct KB Role-* read — uses the stories as_a field + SRD story_refs.
    """

    role_label: str = Field(description="as_a value from StoryRow (the actor / persona)")
    story_id: str = ""
    screens: list[str] = Field(
        default_factory=list,
        description="SCR-* IDs from ImplTask.screens_to_update for this story",
    )
    test_assertions: list[str] = Field(
        default_factory=list,
        description=(
            "Verifiable assertions: 'Assert that {role_label} can access {screen}'. "
            "One per screen + one negative assertion for a non-authorised role."
        ),
    )
    source_type: str = Field(default="dev_role", description="always 'dev_role'")
    stub_marker: str | None = None   # "NO-ROLE" when as_a is empty


class IntegrationTest(BaseModel):
    """Integration test derived from SRD integration_design.

    Primary: SRDDocument.integration_design (IntegrationPoint).
    Fallback: card_bodies[int_id] when SRD.integration_design is empty — surfaced as 'kb_direct'.
    """

    integration_id: str = Field(description="INT-*/API-* KB card ID")
    label: str = ""
    protocol: str | None = None
    from_component: str = ""
    to_component: str = ""
    test_assertions: list[str] = Field(
        default_factory=list,
        description=(
            "Protocol-appropriate assertions: "
            "'Assert REST 200 on success' / 'Assert SOAP fault handled' / etc."
        ),
    )
    source_type: str = Field(
        default="srd_integration",
        description="srd_integration | kb_direct (fallback when SRD integration_design empty)",
    )
    source_locus: str | None = None
    stub_marker: str | None = None   # "ARCH-STUB" when protocol is missing


class QAGap(BaseModel):
    """One gap in the test plan — surfaces missing prior artifact data.

    Never fabricates content. Gap types map to the upstream fix needed:
      no_gherkin     → re-run Stories stage (KB cards need prose enrichment)
      empty_fsd_scr  → FSD has no screen_specs; run /kb-build for SCR-* cards
      empty_brd_rules→ BRD business_rules empty; run /kb-build for BR-* cards
      no_role        → Story missing as_a field; update Stories stage
      empty_srd_seq  → SRD has no sequence_diagrams; re-run Architecture stage
      empty_srd_int  → SRD has no integration_design; re-run Architecture stage
      kb_gap         → KB card has no prose even after fallback
    """

    gap_id: str = ""
    description: str
    gap_type: str = Field(
        description=(
            "no_gherkin | empty_fsd_scr | empty_brd_rules | "
            "no_role | empty_srd_seq | empty_srd_int | kb_gap"
        ),
    )
    source: str = Field(default="", description="story_id or rule_id or screen_id that is missing")
    priority: str = "High"
    action: str = Field(default="", description="what to do to fix this gap")


class TestExecution(BaseModel):
    """Execution record for one TestCase — written by QA during test execution.

    Separate from test generation — test_executions is an append-only log written
    via POST /ws/{id}/qa/execute. The accept() gate checks for FAIL/BLOCKED.
    """

    test_id: str
    status: str = Field(
        default="TODO",
        description="TODO | PASS | FAIL | BLOCKED | SKIP",
    )
    notes: str = ""
    override_justification: str | None = Field(
        default=None,
        description=(
            "Required when status=FAIL|BLOCKED at accept() gate. "
            "Records why QA is accepting despite known failures."
        ),
    )
    executed_at: str | None = None
    executed_by: str | None = None


class TestPlanDocument(BaseModel):
    """The full AIG QA test plan — single source for /qa page and AIG .docx export.

    10 sections derived deterministically from prior SDLC artifacts:
      1.  test_scope          — LLM: what is being tested, out-of-scope items
      2.  story_test_cases    — from Stories Gherkin (no KB fallback)
      3.  screen_validations  — from FSD screen_specs (no KB fallback)
      4.  business_rule_tests — from BRD business_rules → card_bodies fallback
      5.  workflow_scenarios  — from SRD sequence_diagrams (no KB fallback)
      6.  role_access_tests   — dev impl_tasks × stories.as_a
      7.  integration_tests   — from SRD integration_design → card_bodies fallback
      8.  regression_scope    — derived list of regression areas from existing test cases
      9.  gap_log             — all gaps (missing Gherkin, empty FSD, etc.)
      10. references          — prior artifact IDs + KB citations

    accept() gate: blocks if any TestExecution.status is FAIL|BLOCKED and no override.
    """

    workspace_id: str
    dev_ref: str | None = Field(
        default=None, description="artifact_id of accepted DevDocument (immediate prerequisite)"
    )
    stories_ref: str | None = None
    srd_ref: str | None = None
    fsd_ref: str | None = None
    brd_ref: str | None = None
    kb_version: str
    persona: str
    template_id: str = "aig-test-plan"
    template_version: str = "v1"
    requirement: str = ""
    change_class: str = ""
    grounding_score: float = 1.0
    stub_count: int = Field(default=0, description="total QA-BLOCKED + KB-GAP + ARCH-STUB items")

    # ── 10 sections ───────────────────────────────────────────────────────────
    test_scope: str = Field(
        default="",
        description="LLM: what is under test, test environment, what is excluded. ≤300w.",
    )
    story_test_cases: list[TestCase] = Field(
        default_factory=list, description="One TestCase per story — from Gherkin AC"
    )
    screen_validations: list[ScreenValidation] = Field(
        default_factory=list, description="One ScreenValidation per FSD screen_spec"
    )
    business_rule_tests: list[BusinessRuleTest] = Field(
        default_factory=list, description="One BusinessRuleTest per BRD business rule"
    )
    workflow_scenarios: list[WorkflowScenario] = Field(
        default_factory=list, description="One WorkflowScenario per SRD sequence_diagram"
    )
    role_access_tests: list[RoleAccessTest] = Field(
        default_factory=list, description="One RoleAccessTest per (role, screen) pair"
    )
    integration_tests: list[IntegrationTest] = Field(
        default_factory=list, description="One IntegrationTest per SRD integration point"
    )
    regression_scope: list[str] = Field(
        default_factory=list, description="Regression areas derived from test_case + screen IDs"
    )
    gap_log: list[QAGap] = Field(
        default_factory=list, description="All gaps (no Gherkin, empty BRD rules, etc.)"
    )
    references: list[ImpactCitation] = Field(default_factory=list)

    # ── Test execution records (append-only via /qa/execute) ─────────────────
    test_executions: list[TestExecution] = Field(
        default_factory=list,
        description="Filled by QA during test execution — not generated by the pipeline",
    )

    # ── Provenance (Genlite section_basis pattern) ────────────────────────────
    section_basis: dict[str, str] = Field(default_factory=dict)
    abstained: bool = False
    generated_at: str | None = None
