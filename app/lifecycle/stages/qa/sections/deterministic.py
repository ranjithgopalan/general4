"""Deterministic QA section builders — no LLM, no fabrication. Cyclomatic ≤ 21 per function.

Builders:
  build_story_test_cases    — Gherkin AC → TestCase list (no KB fallback)
  build_screen_validations  — FSD ScreenSpec → ScreenValidation list (no KB fallback)
  build_business_rule_tests — BRD business_rules → card_bodies fallback → BusinessRuleTest list
  build_workflow_scenarios  — SRD sequence_diagrams → WorkflowScenario list (no KB fallback)
  build_role_access_tests   — DevDocument impl_tasks × stories.as_a → RoleAccessTest list
  build_integration_tests   — SRD integration_design → card_bodies fallback → IntegrationTest list
  build_regression_scope    — derive from story_test_cases + screen_validations
  build_qa_gap_log          — collect all gap markers from above builders
  build_references          — ImpactCitation from prior artifact refs + KB IDs used

Source priority (cite-or-abstain):
  Never fabricate test steps. If source data is absent/empty → stub_marker set, gap_log entry added.
  KB card_bodies fallback only for business_rule_tests and integration_tests.
"""

from __future__ import annotations

import re
from typing import Any

from app.lifecycle.stages.analysis.schema import ImpactCitation
from app.lifecycle.stages.qa.match.context import QAContext
from app.lifecycle.stages.qa.schema import (
    BusinessRuleTest,
    IntegrationTest,
    QAGap,
    RoleAccessTest,
    ScreenValidation,
    TestCase,
    WorkflowScenario,
)

# Mermaid DSL participant message pattern — extract interactions between actors.
_MERMAID_MSG_RE = re.compile(
    r"^\s*(?P<from>\w[\w ]*?)\s*-[->>]+\s*(?P<to>\w[\w ]*?)\s*:\s*(?P<msg>.+)$",
    re.MULTILINE,
)

# Protocol → default assertion templates (never fabricated content — pattern-based).
_PROTOCOL_ASSERTIONS: dict[str, list[str]] = {
    "REST": [
        "Assert HTTP 200 returned on successful call",
        "Assert HTTP 4xx returned for invalid input (parameterized SQL; never raw string concat)",
        "Assert request/response schema matches API contract",
    ],
    "SOAP": [
        "Assert SOAP envelope structure matches WSDL contract",
        "Assert SOAP fault returned on failure (not HTTP 500)",
        "Assert WS-Security headers present when configured",
    ],
    "ESB": [
        "Assert message routed to correct ESB queue",
        "Assert ESB error queue captures failed messages",
        "Assert retry behavior on downstream timeout",
    ],
    "DB": [
        "Assert parameterized query used (no string concatenation — OWASP SQL)",
        "Assert transaction rolled back on error",
        "Assert no direct DDL in application path",
    ],
    "Event": [
        "Assert event published with correct schema",
        "Assert consumer acknowledges event",
        "Assert idempotency: duplicate event does not cause double processing",
    ],
    "gRPC": [
        "Assert proto schema matches generated client",
        "Assert deadline/timeout propagated on calls",
        "Assert status codes per gRPC status spec",
    ],
}

_DEFAULT_PROTOCOL_ASSERTIONS = [
    "Assert integration responds within SLA",
    "Assert errors surfaced to caller (not silently swallowed)",
]


def _is_empty_or_stub(val: str) -> bool:
    """Return True when a field is empty, whitespace-only, or a bracket-format stub."""
    stripped = (val or "").strip()
    return not stripped or (stripped.startswith("[") and stripped.endswith("]"))


# ── story_test_cases ───────────────────────────────────────────────────────────

def build_story_test_cases(ctx: QAContext) -> tuple[list[TestCase], list[QAGap]]:
    """One TestCase per StoryRow derived from Gherkin AC fields.

    Gherkin → TestCase mapping:
      ac_given → preconditions (single-item list)
      ac_when  → steps (single test action step)
      ac_then  → expected_result

    When any Gherkin field is empty after Stories enrichment:
      stub_marker = "QA-BLOCKED"  (the BA must enrich the KB and re-run Stories)
      gap_log gains a "no_gherkin" QAGap with the specific story_id

    Never fabricates Gherkin content. No KB fallback here — bad Gherkin = broken Stories.
    """
    cases: list[TestCase] = []
    gaps: list[QAGap] = []
    counters: dict[str, int] = {}

    for row in ctx.story_rows:
        story_id = (getattr(row, "story_id", "") or "").strip()
        title = (getattr(row, "title", "") or story_id).strip()
        ac_given = (getattr(row, "ac_given", "") or "").strip()
        ac_when = (getattr(row, "ac_when", "") or "").strip()
        ac_then = (getattr(row, "ac_then", "") or "").strip()
        priority = (getattr(row, "priority", "Medium") or "Medium")
        points = int(getattr(row, "points", 0) or 0)
        source_refs = list(getattr(row, "source_refs", []) or [])

        n = counters.get(story_id, 0) + 1
        counters[story_id] = n
        tc_id = f"TC-{story_id}-{n:02d}" if story_id else f"TC-{n:04d}"

        missing_fields = [
            f for f, v in [("ac_given", ac_given), ("ac_when", ac_when), ("ac_then", ac_then)]
            if _is_empty_or_stub(v)
        ]

        if missing_fields:
            gaps.append(QAGap(
                gap_id=f"gap-gherkin-{story_id}",
                description=(
                    f"Story {story_id!r} ({title[:50]}) is missing Gherkin fields: "
                    f"{', '.join(missing_fields)}. "
                    "Re-run Stories stage after enriching the KB cards via /kb-build."
                ),
                gap_type="no_gherkin",
                source=story_id,
                priority="High",
                action="Run /kb-build to enrich KB card prose, then re-run Stories stage",
            ))
            cases.append(TestCase(
                id=tc_id,
                title=title,
                story_id=story_id,
                source_ref=source_refs[0] if source_refs else None,
                test_type="functional",
                preconditions=[],
                steps=[],
                expected_result="",
                source_type="gherkin",
                priority=priority,
                points=points,
                stub_marker="QA-BLOCKED",
            ))
            continue

        cases.append(TestCase(
            id=tc_id,
            title=title,
            story_id=story_id,
            source_ref=source_refs[0] if source_refs else None,
            test_type="functional",
            preconditions=[ac_given],
            steps=[ac_when],
            expected_result=ac_then,
            source_type="gherkin",
            priority=priority,
            points=points,
            stub_marker=None,
        ))

    return cases, gaps


# ── screen_validations ─────────────────────────────────────────────────────────

def build_screen_validations(ctx: QAContext) -> tuple[list[ScreenValidation], list[QAGap]]:
    """One ScreenValidation per FSD ScreenSpec — no KB fallback.

    FSD ScreenSpec → ScreenValidation mapping:
      ScreenSpec.id             → screen_id
      ScreenSpec.title          → label
      ScreenSpec.purpose        → included in first field_validation as context
      ScreenSpec.fields         → field_validations ("Field '{name}': verify visible/required")
      ScreenSpec.business_rules → rule_validations ("Assert BR-xxx enforced on this screen")

    No KB direct read. If FSD has no screen_specs → one gap entry; no fabricated validations.
    """
    validations: list[ScreenValidation] = []
    gaps: list[QAGap] = []

    if not ctx.fsd_screens:
        gaps.append(QAGap(
            gap_id="gap-fsd-scr-absent",
            description=(
                "FSD has no screen_specs — screen validation section is empty. "
                "Run /kb-build to extract SCR-* KB cards, then re-run FSD stage."
            ),
            gap_type="empty_fsd_scr",
            source="fsd",
            priority="High",
            action="Run /kb-build for SCR-* card extraction, then re-run FSD stage",
        ))
        return validations, gaps

    for scr in ctx.fsd_screens:
        scr_id = (getattr(scr, "id", "") or "").strip()
        label = (getattr(scr, "title", "") or scr_id).strip()
        purpose = (getattr(scr, "purpose", "") or "").strip()
        fields = list(getattr(scr, "fields", []) or [])
        business_rules = list(getattr(scr, "business_rules", []) or [])
        source_locus = getattr(scr, "source_locus", None)

        field_validations: list[str] = []
        if purpose:
            field_validations.append(f"Verify screen purpose: {purpose[:120]}")
        for fname in fields[:10]:
            field_validations.append(f"Field '{fname}': verify visible, required state, and input validation")
        if not fields and not purpose:
            field_validations = []   # empty → stub_marker set below

        rule_validations: list[str] = []
        for br_id in business_rules[:8]:
            rule_validations.append(f"Assert {br_id} is enforced on screen {label}")

        stub_marker = None
        if not field_validations and not rule_validations:
            stub_marker = "FSD-STUB"
            gaps.append(QAGap(
                gap_id=f"gap-scr-{scr_id}",
                description=(
                    f"Screen {scr_id} ({label[:50]}) has no fields and no business rules in FSD — "
                    "no meaningful screen validation can be generated. "
                    "Enrich the FSD ScreenSpec by running /kb-build for this SCR-* card."
                ),
                gap_type="empty_fsd_scr",
                source=scr_id,
                priority="Medium",
                action=f"Run /kb-build to enrich SCR-* card {scr_id}",
            ))

        validations.append(ScreenValidation(
            screen_id=scr_id,
            label=label,
            purpose=purpose,
            field_validations=field_validations,
            rule_validations=rule_validations,
            source_type="fsd_screen",
            source_locus=source_locus,
            stub_marker=stub_marker,
        ))

    return validations, gaps


# ── business_rule_tests ────────────────────────────────────────────────────────

def _card_prose(card_bodies: dict[str, Any], card_id: str) -> str:
    """Extract prose from card_bodies; returns empty string when absent."""
    card = card_bodies.get(card_id)
    if not card:
        return ""
    return str(card.get("prose") or card.get("text_en") or "").strip()


def _rule_to_assertions(rule_text: str, rule_id: str) -> list[str]:
    """Derive test assertions from rule_text prose (deterministic, no LLM).

    Pattern: "Assert that the rule '{rule_id}' is enforced: {rule_text[:150]}"
    Then one additional negative: "Assert that violation of this rule is rejected."
    Capped at 2 assertions to keep the document scannable.
    """
    if not rule_text.strip():
        return []
    cap = rule_text[:150].rsplit(" ", 1)[0] if len(rule_text) > 150 else rule_text
    return [
        f"Assert that rule {rule_id} is enforced: {cap}",
        f"Assert that a violation of rule {rule_id} is rejected or flagged",
    ]


def build_business_rule_tests(ctx: QAContext) -> tuple[list[BusinessRuleTest], list[QAGap]]:
    """One BusinessRuleTest per BRD business rule — card_bodies fallback when BRD is empty.

    Primary: BRDDocument.business_rules (rule_text from KB prose)
    Fallback: card_bodies[br_id] when BRD.business_rules is empty and br_ref_ids were fetched
    Cite-or-abstain: when both are empty → stub_marker="KB-GAP" and gap_log entry added.
    """
    tests: list[BusinessRuleTest] = []
    gaps: list[QAGap] = []

    if ctx.brd_rules:
        # Primary path: BRD business rules
        for rule in ctx.brd_rules:
            rule_id = (getattr(rule, "id", "") or "").strip()
            label = (getattr(rule, "title", "") or rule_id).strip()
            rule_text = (getattr(rule, "rule_text", "") or "").strip()
            source_locus = getattr(rule, "source_locus", None)

            if not rule_text:
                # BRD has the rule but rule_text is empty — try card_bodies
                rule_text = _card_prose(ctx.card_bodies, rule_id)

            assertions = _rule_to_assertions(rule_text, rule_id)
            stub_marker = None
            if not rule_text:
                stub_marker = "KB-GAP"
                gaps.append(QAGap(
                    gap_id=f"gap-br-{rule_id}",
                    description=(
                        f"Business rule {rule_id} ({label[:50]}) has no rule_text in BRD or KB — "
                        "test assertions cannot be derived. "
                        "Run /kb-build to extract rule content from source documents."
                    ),
                    gap_type="empty_brd_rules",
                    source=rule_id,
                    priority="High",
                    action=f"Run /kb-build to enrich BR-* card {rule_id}",
                ))

            tests.append(BusinessRuleTest(
                rule_id=rule_id,
                label=label,
                rule_text=rule_text,
                test_assertions=assertions,
                source_type="brd_rule",
                source_locus=source_locus,
                stub_marker=stub_marker,
            ))
        return tests, gaps

    # Fallback path: BRD has no business_rules; use br_ref_ids from brd.references + card_bodies
    if ctx.br_ref_ids:
        for br_id in ctx.br_ref_ids:
            prose = _card_prose(ctx.card_bodies, br_id)
            label = br_id  # card_bodies might have a label
            card = ctx.card_bodies.get(br_id, {})
            if card:
                label = card.get("label") or br_id

            assertions = _rule_to_assertions(prose, br_id)
            stub_marker = None
            if not prose:
                stub_marker = "KB-GAP"
                gaps.append(QAGap(
                    gap_id=f"gap-br-kb-{br_id}",
                    description=(
                        f"BR-* card {br_id} found in BRD references but has no prose — "
                        "test assertions cannot be derived. "
                        "Run /kb-build to extract rule content from source documents."
                    ),
                    gap_type="kb_gap",
                    source=br_id,
                    priority="High",
                    action=f"Run /kb-build to enrich BR-* card {br_id}",
                ))

            tests.append(BusinessRuleTest(
                rule_id=br_id,
                label=label,
                rule_text=prose,
                test_assertions=assertions,
                source_type="kb_direct",
                source_locus=card.get("source_locus"),
                stub_marker=stub_marker,
            ))
        return tests, gaps

    # Both BRD and fallback are empty → single gap entry
    gaps.append(QAGap(
        gap_id="gap-brd-rules-absent",
        description=(
            "BRD has no business_rules and no BR-* KB references — "
            "business rule test section is empty. "
            "Run /kb-build to extract BR-* cards, then re-run BRD stage."
        ),
        gap_type="empty_brd_rules",
        source="brd",
        priority="High",
        action="Run /kb-build for BR-* card extraction, then re-run BRD stage",
    ))
    return tests, gaps


# ── workflow_scenarios ─────────────────────────────────────────────────────────

def _parse_mermaid_steps(mermaid_dsl: str, title: str) -> list[str]:
    """Extract participant interaction steps from a Mermaid sequenceDiagram block.

    Derives one step per participant message line. Capped at 8 steps.
    Returns [] when dsl is empty or has no parseable message lines.
    """
    if not mermaid_dsl.strip():
        return []
    matches = _MERMAID_MSG_RE.findall(mermaid_dsl)
    steps = []
    for from_, to_, msg in matches[:8]:
        steps.append(f"Step: {from_.strip()} → {to_.strip()}: {msg.strip()[:100]}")
    return steps


def build_workflow_scenarios(ctx: QAContext) -> tuple[list[WorkflowScenario], list[QAGap]]:
    """One WorkflowScenario per SRD SequenceDiagram — no KB fallback.

    SequenceDiagram → WorkflowScenario mapping:
      seq.story_id      → workflow_id
      seq.title         → title
      seq.participants  → participants
      seq.mermaid_dsl   → scenario_steps (parsed) + expected_outcome
      stub_marker       = "ARCH-STUB" when mermaid_dsl is empty

    No KB direct fallback. Empty SRD sequence_diagrams → gap entry; no fabrication.
    """
    scenarios: list[WorkflowScenario] = []
    gaps: list[QAGap] = []

    if not ctx.srd_seq_diagrams:
        gaps.append(QAGap(
            gap_id="gap-srd-seq-absent",
            description=(
                "SRD has no sequence_diagrams — workflow scenario section is empty. "
                "Re-run Architecture stage to produce Mermaid sequence diagrams."
            ),
            gap_type="empty_srd_seq",
            source="srd",
            priority="Medium",
            action="Re-run Architecture stage (SRD) to generate sequence_diagrams",
        ))
        return scenarios, gaps

    for seq in ctx.srd_seq_diagrams:
        workflow_id = (getattr(seq, "story_id", "") or "").strip()
        title = (getattr(seq, "title", "") or workflow_id).strip()
        participants = list(getattr(seq, "participants", []) or [])
        mermaid_dsl = (getattr(seq, "mermaid_dsl", "") or "").strip()

        steps = _parse_mermaid_steps(mermaid_dsl, title)
        expected_outcome = ""
        stub_marker = None

        if not mermaid_dsl or not steps:
            stub_marker = "ARCH-STUB"
            gaps.append(QAGap(
                gap_id=f"gap-srd-seq-{workflow_id}",
                description=(
                    f"SRD sequence diagram for story {workflow_id!r} ({title[:50]}) "
                    "has empty or unparseable mermaid_dsl — workflow scenario steps cannot be derived. "
                    "Re-run Architecture stage to complete the diagram."
                ),
                gap_type="empty_srd_seq",
                source=workflow_id,
                priority="Medium",
                action=f"Re-run Architecture stage to complete sequence diagram for {workflow_id}",
            ))
        else:
            expected_outcome = (
                f"Workflow '{title}' completes successfully with all participants "
                f"({', '.join(participants[:4])}) responding as per sequence diagram."
            )

        scenarios.append(WorkflowScenario(
            workflow_id=workflow_id,
            title=title,
            participants=participants,
            scenario_steps=steps,
            expected_outcome=expected_outcome,
            source_type="srd_sequence",
            stub_marker=stub_marker,
        ))

    return scenarios, gaps


# ── role_access_tests ──────────────────────────────────────────────────────────

def build_role_access_tests(ctx: QAContext) -> tuple[list[RoleAccessTest], list[QAGap]]:
    """One RoleAccessTest per (role, story) pair from DevDocument impl_tasks × stories.as_a.

    Cross-reference:
      stories.as_a          → the actor / persona role for this story
      impl_tasks.screens    → the screens this story touches

    For each unique (role, story_id) pair:
      - positive assertion: "Assert {role} can access screens {screens}"
      - negative assertion: "Assert a user NOT in the {role} role cannot access those screens"

    No direct ROLE-* KB read. No fabricated role lists.
    """
    access_tests: list[RoleAccessTest] = []
    gaps: list[QAGap] = []

    # Build story_id → as_a lookup from story_rows
    role_by_story: dict[str, str] = {}
    for row in ctx.story_rows:
        sid = (getattr(row, "story_id", "") or "").strip()
        as_a = (getattr(row, "as_a", "") or "").strip()
        if sid and as_a:
            role_by_story[sid] = as_a

    # Build story_id → screens from impl_tasks
    screens_by_story: dict[str, list[str]] = {}
    for task in ctx.impl_tasks:
        sid = (getattr(task, "story_id", "") or "").strip()
        screens = list(getattr(task, "screens_to_update", []) or [])
        if sid:
            screens_by_story[sid] = screens

    # Emit one RoleAccessTest per story that has both a role and screens
    seen: set[str] = set()
    for sid, role in role_by_story.items():
        screens = screens_by_story.get(sid, [])
        key = f"{role}:{sid}"
        if key in seen:
            continue
        seen.add(key)

        if not role:
            gaps.append(QAGap(
                gap_id=f"gap-role-{sid}",
                description=(
                    f"Story {sid!r} has no 'as_a' (role) field — "
                    "role access test cannot be generated. "
                    "Fix the Stories stage: the as_a field must name a concrete persona."
                ),
                gap_type="no_role",
                source=sid,
                priority="High",
                action="Fix Stories stage: ensure as_a field is populated for each story",
            ))
            continue

        assertions = []
        if screens:
            screen_list = ", ".join(screens[:4])
            assertions.append(
                f"Assert that '{role}' can access the following screens: {screen_list}"
            )
            assertions.append(
                f"Assert that a user NOT in the '{role}' role is denied access to: {screen_list}"
            )
        else:
            assertions.append(
                f"Assert that '{role}' can perform the action described in story {sid}"
            )
            assertions.append(
                f"Assert that an unauthorised role is denied performing this action"
            )

        access_tests.append(RoleAccessTest(
            role_label=role,
            story_id=sid,
            screens=screens,
            test_assertions=assertions,
            source_type="dev_role",
            stub_marker=None,
        ))

    if not access_tests and not gaps:
        if ctx.dev_source == "absent" or not ctx.impl_tasks:
            gaps.append(QAGap(
                gap_id="gap-dev-absent",
                description=(
                    "DevDocument is absent or has no implementation_plan — "
                    "role access tests cannot be generated. "
                    "Accept the Developer stage first."
                ),
                gap_type="no_role",
                source="dev",
                priority="High",
                action="Accept Developer stage before generating QA test plan",
            ))

    return access_tests, gaps


# ── integration_tests ──────────────────────────────────────────────────────────

def build_integration_tests(ctx: QAContext) -> tuple[list[IntegrationTest], list[QAGap]]:
    """One IntegrationTest per SRD IntegrationPoint — card_bodies fallback when SRD empty.

    Primary: SRDDocument.integration_design (IntegrationPoint)
    Fallback: card_bodies[int_id] when SRD.integration_design is empty and int_ref_ids fetched
    Assertions derive from protocol using _PROTOCOL_ASSERTIONS template map.
    """
    tests: list[IntegrationTest] = []
    gaps: list[QAGap] = []

    def _assertions_for(protocol: str | None) -> list[str]:
        if protocol and protocol in _PROTOCOL_ASSERTIONS:
            return list(_PROTOCOL_ASSERTIONS[protocol])
        return list(_DEFAULT_PROTOCOL_ASSERTIONS)

    if ctx.srd_int_design:
        # Primary path: SRD integration_design
        seen: set[str] = set()
        for intg in ctx.srd_int_design:
            int_id = (getattr(intg, "id", "") or "").strip()
            if int_id in seen:
                continue
            seen.add(int_id)
            label = (getattr(intg, "label", "") or int_id).strip()
            protocol = getattr(intg, "protocol", None)
            from_cmp = (getattr(intg, "from_component", "") or "").strip()
            to_cmp = (getattr(intg, "to_component", "") or "").strip()
            source_locus = getattr(intg, "source_locus", None)

            stub_marker = None
            if not protocol:
                stub_marker = "ARCH-STUB"
                gaps.append(QAGap(
                    gap_id=f"gap-int-proto-{int_id}",
                    description=(
                        f"Integration {int_id} ({label[:50]}) has no protocol defined in SRD — "
                        "integration test assertions cannot be protocol-specific. "
                        "Architect must confirm REST/SOAP/ESB/DB/Event/gRPC."
                    ),
                    gap_type="empty_srd_int",
                    source=int_id,
                    priority="Medium",
                    action=f"Architect must set protocol for integration {int_id}",
                ))

            tests.append(IntegrationTest(
                integration_id=int_id,
                label=label,
                protocol=protocol,
                from_component=from_cmp,
                to_component=to_cmp,
                test_assertions=_assertions_for(protocol),
                source_type="srd_integration",
                source_locus=source_locus,
                stub_marker=stub_marker,
            ))
        return tests, gaps

    # Fallback: SRD has no integration_design; use int_ref_ids from srd.references + card_bodies
    if ctx.int_ref_ids:
        for int_id in ctx.int_ref_ids:
            card = ctx.card_bodies.get(int_id, {})
            label = card.get("label") or int_id
            source_locus = card.get("source_locus")

            tests.append(IntegrationTest(
                integration_id=int_id,
                label=label,
                protocol=None,
                test_assertions=list(_DEFAULT_PROTOCOL_ASSERTIONS),
                source_type="kb_direct",
                source_locus=source_locus,
                stub_marker="ARCH-STUB",
            ))
        gaps.append(QAGap(
            gap_id="gap-srd-int-fallback",
            description=(
                f"SRD integration_design is empty — {len(ctx.int_ref_ids)} integration(s) "
                "sourced from SRD references via KB fallback. "
                "Protocol-specific assertions not available. Re-run Architecture stage."
            ),
            gap_type="empty_srd_int",
            source="srd",
            priority="Medium",
            action="Re-run Architecture stage to populate integration_design with protocol details",
        ))
        return tests, gaps

    gaps.append(QAGap(
        gap_id="gap-srd-int-absent",
        description=(
            "SRD has no integration_design and no INT-* references — "
            "integration test section is empty. Re-run Architecture stage."
        ),
        gap_type="empty_srd_int",
        source="srd",
        priority="Low",
        action="Re-run Architecture stage to include INT-* integration points",
    ))
    return tests, gaps


# ── regression_scope ───────────────────────────────────────────────────────────

def build_regression_scope(
    test_cases: list[TestCase],
    screen_validations: list[ScreenValidation],
    brd_rule_tests: list[BusinessRuleTest],
) -> list[str]:
    """Derive a regression-scope list from test cases, screens, and rules.

    Pattern: one regression area per story (from test_cases) + one per screen + one per rule.
    Capped at 20 items to keep the section scannable.
    """
    scope: list[str] = []
    seen: set[str] = set()

    for tc in test_cases[:10]:
        if not tc.stub_marker and tc.story_id and tc.story_id not in seen:
            seen.add(tc.story_id)
            scope.append(
                f"Regression: re-verify story {tc.story_id} ({tc.title[:50]}) after any change to related KB cards"
            )

    for sv in screen_validations[:5]:
        if not sv.stub_marker and sv.screen_id not in seen:
            seen.add(sv.screen_id)
            scope.append(
                f"Regression: re-validate screen {sv.screen_id} ({sv.label[:50]}) on any UI change"
            )

    for br in brd_rule_tests[:5]:
        if not br.stub_marker and br.rule_id not in seen:
            seen.add(br.rule_id)
            scope.append(
                f"Regression: re-test rule {br.rule_id} ({br.label[:50]}) on any BR-related change"
            )

    return scope[:20]


# ── references ─────────────────────────────────────────────────────────────────

def build_references(ctx: QAContext) -> list[ImpactCitation]:
    """Collect all artifact + KB ID citations for the references section."""
    refs: list[ImpactCitation] = []
    seen: set[str] = set()

    def _add(ref_id: str, kind: str, label: str, locus: str | None = None) -> None:
        if ref_id and ref_id not in seen:
            seen.add(ref_id)
            refs.append(ImpactCitation(id=ref_id, kind=kind, label=label, source_locus=locus))

    # Prior artifact refs (immediate prerequisite first)
    if ctx.dev_ref:
        _add(ctx.dev_ref, "DevDocument", "Derived from Developer Document")
    if ctx.stories_ref:
        _add(ctx.stories_ref, "StoriesDocument", "Gherkin AC source")
    if ctx.srd_ref:
        _add(ctx.srd_ref, "SRDDocument", "Workflow + integration source")
    if ctx.fsd_ref:
        _add(ctx.fsd_ref, "FSDDocument", "Screen spec source")
    if ctx.brd_ref:
        _add(ctx.brd_ref, "BRDDocument", "Business rules source")

    # KB IDs used
    for kb_id in sorted(ctx.allowed_ids):
        card = ctx.card_bodies.get(kb_id, {})
        _add(
            kb_id,
            card.get("kind") or "KBCard",
            card.get("label") or kb_id,
            card.get("source_locus"),
        )

    return refs
