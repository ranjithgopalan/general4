"""assemble_test_plan() — full QA assembly pipeline.

Pipeline (10 steps):
  1.  build_story_test_cases     — Gherkin → TestCase list (no KB fallback)
  2.  build_screen_validations   — FSD screen_specs → ScreenValidation list (no KB fallback)
  3.  build_business_rule_tests  — BRD rules → card_bodies fallback → BusinessRuleTest list
  4.  build_workflow_scenarios   — SRD sequence_diagrams → WorkflowScenario list (no KB fallback)
  5.  build_role_access_tests    — impl_tasks × stories.as_a → RoleAccessTest list
  6.  build_integration_tests    — SRD integration_design → card_bodies fallback → IntegrationTest list
  7.  build_regression_scope     — derive from test cases + screens + rules (deterministic)
  8.  reasoned.build_test_scope  — LLM: what is under test; deterministic fallback
  9.  reasoned.apply_regression_enrichment — LLM optional additions to regression_scope
  10. assemble TestPlanDocument  — compute stub_count, section_basis, grounding_score
"""

from __future__ import annotations

from typing import Any

from app.lifecycle.common.handler_base import _now_iso
from app.lifecycle.stages.qa.match.context import QAContext
from app.lifecycle.stages.qa.schema import QAGap, TestPlanDocument
from app.lifecycle.stages.qa.sections import deterministic as det
from app.lifecycle.stages.qa.sections import reasoned as rz
from app.lifecycle.templates.registry import Template


def _basis(
    *,
    prior: int = 0,
    kb_fallback: int = 0,
    llm: int = 0,
    stub: int = 0,
) -> str:
    """Compact per-section provenance tag (Genlite Import 3 pattern)."""
    return (
        f"prior-artifact: {prior} | kb-fallback: {kb_fallback} "
        f"| LLM-reasoned: {llm} | stub: {stub}"
    )


def assemble_test_plan(
    *,
    workspace_id: str,
    kb_version: str,
    persona: str,
    template: Template,
    context: QAContext,
    enriched: dict[str, Any] | None,
    generated_at: str | None = None,
) -> TestPlanDocument:
    """Combine deterministic + reasoned sections into the single-source TestPlanDocument.

    Never raises — any internal error leaves sections at safe defaults.
    Returns a TestPlanDocument. Test execution records are NOT part of this pipeline
    (they are written separately via POST /ws/{id}/qa/execute).
    """
    all_gaps: list[QAGap] = []

    # ── 1. Story test cases ────────────────────────────────────────────────────
    test_cases, gherkin_gaps = det.build_story_test_cases(context)
    all_gaps.extend(gherkin_gaps)

    # ── 2. Screen validations ──────────────────────────────────────────────────
    screen_validations, scr_gaps = det.build_screen_validations(context)
    all_gaps.extend(scr_gaps)

    # ── 3. Business rule tests ─────────────────────────────────────────────────
    br_tests, br_gaps = det.build_business_rule_tests(context)
    all_gaps.extend(br_gaps)

    # ── 4. Workflow scenarios ──────────────────────────────────────────────────
    wf_scenarios, wf_gaps = det.build_workflow_scenarios(context)
    all_gaps.extend(wf_gaps)

    # ── 5. Role access tests ───────────────────────────────────────────────────
    role_tests, role_gaps = det.build_role_access_tests(context)
    all_gaps.extend(role_gaps)

    # ── 6. Integration tests ───────────────────────────────────────────────────
    int_tests, int_gaps = det.build_integration_tests(context)
    all_gaps.extend(int_gaps)

    # ── 7. Regression scope (deterministic base) ───────────────────────────────
    regression_scope = det.build_regression_scope(test_cases, screen_validations, br_tests)

    # ── 8. LLM test_scope ─────────────────────────────────────────────────────
    test_scope = rz.build_test_scope(enriched, context)

    # ── 9. LLM regression enrichment + gap enrichment ─────────────────────────
    regression_scope = rz.apply_regression_enrichment(regression_scope, enriched)
    llm_gaps = rz.build_gap_log_enrichment(enriched, all_gaps)
    all_gaps.extend(llm_gaps)

    # ── 10. References ─────────────────────────────────────────────────────────
    references = det.build_references(context)

    # ── Stub count ─────────────────────────────────────────────────────────────
    stub_count = (
        sum(1 for tc in test_cases if tc.stub_marker)
        + sum(1 for sv in screen_validations if sv.stub_marker)
        + sum(1 for br in br_tests if br.stub_marker)
        + sum(1 for wf in wf_scenarios if wf.stub_marker)
        + sum(1 for ra in role_tests if ra.stub_marker)
        + sum(1 for it in int_tests if it.stub_marker)
    )

    # ── Grounding score ────────────────────────────────────────────────────────
    total_items = (
        len(test_cases) + len(screen_validations) + len(br_tests)
        + len(wf_scenarios) + len(role_tests) + len(int_tests)
    )
    if total_items == 0:
        grounding_score = 0.0
    else:
        grounding_score = round(max(0.0, (total_items - stub_count) / total_items), 3)

    # ── Section basis provenance ───────────────────────────────────────────────
    stories_prior = len(test_cases) - sum(1 for tc in test_cases if tc.stub_marker)
    scr_prior = len(screen_validations) - sum(1 for sv in screen_validations if sv.stub_marker)
    br_kb_fallback = sum(1 for br in br_tests if br.source_type == "kb_direct")
    br_prior = len(br_tests) - br_kb_fallback
    wf_prior = len(wf_scenarios) - sum(1 for wf in wf_scenarios if wf.stub_marker)
    int_kb_fallback = sum(1 for it in int_tests if it.source_type == "kb_direct")
    int_prior = len(int_tests) - int_kb_fallback

    section_basis = {
        "1.Test Scope": _basis(llm=1),
        "2.Story Test Cases": _basis(
            prior=stories_prior,
            stub=sum(1 for tc in test_cases if tc.stub_marker),
        ),
        "3.Screen Validations": _basis(
            prior=scr_prior,
            stub=sum(1 for sv in screen_validations if sv.stub_marker),
        ),
        "4.Business Rule Tests": _basis(
            prior=br_prior,
            kb_fallback=br_kb_fallback,
            stub=sum(1 for br in br_tests if br.stub_marker),
        ),
        "5.Workflow Scenarios": _basis(
            prior=wf_prior,
            stub=sum(1 for wf in wf_scenarios if wf.stub_marker),
        ),
        "6.Role Access Tests": _basis(
            prior=len(role_tests) - sum(1 for ra in role_tests if ra.stub_marker),
            stub=sum(1 for ra in role_tests if ra.stub_marker),
        ),
        "7.Integration Tests": _basis(
            prior=int_prior,
            kb_fallback=int_kb_fallback,
            stub=sum(1 for it in int_tests if it.stub_marker),
        ),
        "8.Regression Scope": _basis(prior=len(regression_scope)),
        "9.Gap Log": _basis(stub=len(all_gaps), llm=len(llm_gaps)),
        "10.References": _basis(prior=len(references)),
    }

    return TestPlanDocument(
        workspace_id=workspace_id,
        dev_ref=context.dev_ref,
        stories_ref=context.stories_ref,
        srd_ref=context.srd_ref,
        fsd_ref=context.fsd_ref,
        brd_ref=context.brd_ref,
        kb_version=kb_version,
        persona=persona,
        template_id=template.template_id,
        template_version=template.template_version,
        requirement=context.requirement,
        change_class=context.change_class,
        grounding_score=grounding_score,
        stub_count=stub_count,
        test_scope=test_scope,
        story_test_cases=test_cases,
        screen_validations=screen_validations,
        business_rule_tests=br_tests,
        workflow_scenarios=wf_scenarios,
        role_access_tests=role_tests,
        integration_tests=int_tests,
        regression_scope=regression_scope,
        gap_log=all_gaps,
        references=references,
        section_basis=section_basis,
        abstained=(total_items == 0 and not context.requirement),
        generated_at=generated_at or _now_iso(),
    )
