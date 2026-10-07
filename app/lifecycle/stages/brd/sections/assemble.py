"""Assemble the typed BRDDocument from deterministic + reasoned section builders.

Word budget (Import 1 — Genlite):
  business_case     → 250w soft cap via _soft_cap_prose()
  success_criteria  → 500w soft cap via _soft_cap_list()
  All other sections: uncapped.

Ambiguity validation (Import 3 — Genlite + connected-layer):
  validate_brd_ambiguities() runs after full assembly — any ungrounded KB ID reference
  in prose becomes a BA-TODO KeyDecision stub.
"""

from __future__ import annotations

from typing import Any

from app.lifecycle.stages.brd.match.context import BRDContext
from app.lifecycle.stages.brd.schema import BRDDocument, KeyDecision, SOFT_CAPS
from app.lifecycle.stages.brd.sections import deterministic as det
from app.lifecycle.stages.brd.sections import reasoned as rz
from app.lifecycle.templates.registry import Template


def _basis(explicit: int = 0, inferred: int = 0, llm: int = 0, stub: int = 0) -> str:
    """Compact per-section provenance tag (Import 3 — Genlite pattern)."""
    return (
        f"KB-explicit: {explicit} | KB-inferred: {inferred} "
        f"| LLM-reasoned: {llm} | stub: {stub}"
    )


def _req_counts(reqs: list) -> tuple[int, int, int]:
    """Return (explicit, inferred, stub) counts for business_requirements."""
    explicit = sum(1 for r in reqs if r.source_type == "kb_explicit")
    inferred = sum(1 for r in reqs if r.source_type == "kb_inferred")
    stub = sum(1 for r in reqs if r.source_type == "stub")
    return explicit, inferred, stub


def assemble_brd(
    *,
    workspace_id: str,
    kb_version: str,
    persona: str,
    template: Template,
    context: BRDContext,
    enriched: dict[str, Any] | None,
    allowed_ids: set[str],
    grounding_score: float = 1.0,
    generated_at: str | None = None,
) -> BRDDocument:
    """Combine KB-derived + reasoned sections into the single-source BRDDocument artifact.

    Construction order:
      1. Deterministic sections (scope, business_requirements, business_rules, AC, references)
      2. Reasoned enrichment applied on top (proposed_change, stakeholder_personas, KPIs, etc.)
      3. Import 3: validate_brd_ambiguities (post-assembly ungrounded-ID check)
      4. Import 1: word budget soft caps applied to LLM sections
      5. section_basis computed
      6. stub_count tallied
    """

    # ── 1. Deterministic sections — pure KB data, never truncated ──────────────
    scope = det.build_scope_definition(context)
    reqs = det.build_business_requirements(context)
    rules = det.build_business_rules(context)
    ac = det.build_acceptance_criteria(context)
    refs = det.build_references(context)

    # ── 2. Reasoned enrichment applied on top ─────────────────────────────────
    reqs = rz.apply_brd_req_enrichment(reqs, enriched, allowed_ids)
    rules = rz.apply_brd_rule_enrichment(rules, enriched, allowed_ids)
    business_case_raw = rz.build_business_case(enriched, context)
    personas = rz.build_stakeholder_personas(enriched, context)
    success_raw = rz.build_success_criteria(enriched, context)
    risks = rz.build_risks_and_compliance(enriched, context)
    decisions = rz.build_key_decisions(enriched, context)

    # ── 2b. KB prose gaps → [OPEN] key decisions (cite-or-abstain) ───────────
    # Any matched KB card with no card_body prose surfaces here — tells BA exactly
    # which cards to enrich via /kb-build.  No fabricated content ever added.
    kb_gaps = det.build_kb_prose_gaps(context)
    decisions = decisions + kb_gaps

    # ── 2c. Empty sections → [OPEN] key decisions ─────────────────────────────
    if not personas:
        decisions.append(KeyDecision(
            tag="OPEN",
            description=(
                "No ROLE-* KB cards found in scope — stakeholder personas section is empty. "
                "Add Role cards to the KB via /kb-build for this category."
            ),
            section="5.Target Personas & Needs",
            marker="BA-TODO",
        ))

    # ── 3. Import 3: post-assembly ungrounded-ID validation ───────────────────
    # Construct a partial doc for scanning, then append any ambiguity stubs to decisions.
    _partial = BRDDocument(
        workspace_id=workspace_id,
        kb_version=kb_version,
        persona=persona,
        template_id=template.template_id,
        template_version=template.template_version,
        requirement=context.requirement,
        change_class=context.change_class,
        business_case=business_case_raw,
        success_criteria=success_raw,
        risks_and_compliance=risks,
        key_decisions=decisions,
        business_requirements=reqs,
    )
    ambiguity_stubs = rz.validate_brd_ambiguities(_partial, allowed_ids)
    decisions = decisions + ambiguity_stubs

    # ── 4. Import 1: soft word-cap on LLM sections ────────────────────────────
    section_truncated: dict[str, bool] = {}

    bc_cap = SOFT_CAPS.get("business_case")
    if bc_cap is not None:
        business_case, bc_trunc = rz._soft_cap_prose(business_case_raw, bc_cap)
        if bc_trunc:
            section_truncated["business_case"] = True
    else:
        business_case = business_case_raw

    sc_cap = SOFT_CAPS.get("success_criteria")
    if sc_cap is not None:
        success_criteria, sc_trunc = rz._soft_cap_list(success_raw, sc_cap)
        if sc_trunc:
            section_truncated["success_criteria"] = True
    else:
        success_criteria = success_raw

    # ── 5. Section basis (per-section provenance) ─────────────────────────────
    req_explicit, req_inferred, req_stub = _req_counts(reqs)
    section_basis = {
        "1.Business Case": _basis(llm=1),
        "2.Scope & Boundaries": _basis(
            explicit=len(context.scope.new) + len(context.scope.enhancement),
            inferred=len(context.scope.existing),
            stub=len(context.gaps),
        ),
        "3.Business Requirements": _basis(explicit=req_explicit, inferred=req_inferred, stub=req_stub),
        "4.Business Rules & Policies": _basis(explicit=len(context.br_matched)),
        "5.Target Personas & Needs": _basis(
            explicit=len(context.role_matched) + len(context.dom_matched),
            llm=max(0, len(personas) - len(context.role_matched) - len(context.dom_matched)),
        ),
        "6.Success Criteria": _basis(llm=len(success_criteria)),
        "7.Risks & Compliance": _basis(llm=len(risks)),
        "8.Acceptance Criteria": _basis(explicit=len(ac)),
        "9.Key Decisions & Open Items": _basis(stub=len(decisions)),
        "10.References & Evidence": _basis(explicit=len(refs)),
    }

    # ── 6. Stub count ─────────────────────────────────────────────────────────
    stub_count = (
        sum(1 for r in reqs if r.stub_marker)
        + sum(1 for kd in decisions if kd.marker == "BA-TODO")
    )

    return BRDDocument(
        workspace_id=workspace_id,
        fsd_ref=context.fsd_ref,
        analysis_ref=context.analysis_ref,
        kb_version=kb_version,
        persona=persona,
        template_id=template.template_id,
        template_version=template.template_version,
        requirement=context.requirement,
        change_class=context.change_class,
        grounding_score=grounding_score,
        stub_count=stub_count,
        business_case=business_case,
        scope_definition=scope,
        business_requirements=reqs,
        business_rules=rules,
        stakeholder_personas=personas,
        success_criteria=success_criteria,
        risks_and_compliance=risks,
        acceptance_criteria=ac,
        key_decisions=decisions,
        references=refs,
        section_basis=section_basis,
        section_truncated=section_truncated,
        abstained=not context.matched,
        generated_at=generated_at,
    )
