"""Assemble the typed FSDDocument from deterministic + reasoned section builders."""

from __future__ import annotations

from typing import Any

from app.lifecycle.stages.fsd.match.context import FSDContext
from app.lifecycle.stages.fsd.schema import FSDDocument
from app.lifecycle.stages.fsd.sections import deterministic as det
from app.lifecycle.stages.fsd.sections import proposed as prop
from app.lifecycle.stages.fsd.sections import reasoned as rz
from app.lifecycle.templates.registry import Template


def _basis(explicit: int, inferred: int, llm: int = 0, stub: int = 0) -> str:
    """Compact provenance tag for one FSD section (Import 3 — Genlite pattern)."""
    return f"KB-explicit: {explicit} | KB-inferred: {inferred} | LLM-reasoned: {llm} | stub: {stub}"


def assemble_fsd(
    *,
    workspace_id: str,
    kb_version: str,
    persona: str,
    template: Template,
    context: FSDContext,
    enriched: dict[str, Any] | None,
    allowed_ids: set[str],
    grounding_score: float = 1.0,
    generated_at: str | None = None,
) -> FSDDocument:
    """Combine graph-derived + reasoned sections into the single-source FSDDocument artifact."""
    # Deterministic sections — no LLM required
    frs = det.build_functional_requirements(context)
    proc_flows = det.build_process_flows(context)
    screens = det.build_screen_specs(context)
    rules = det.build_business_rules(context)
    data_model = det.build_data_model(context)
    integrations = det.build_integration_points(context)
    ac3 = det.build_acceptance_criteria(context)

    # Reasoned enrichment — applied on top of deterministic base
    frs = rz.apply_fr_enrichment(frs, enriched, allowed_ids)

    # Hybrid fill (docs/29): a net-new / light change matches few KB cards, so the extractive
    # §3 + §10 come back empty. Seed clearly-marked PROPOSED rows from the analysis
    # (modifications / scope) + requirement so the PRD is not blank — never for Existing-class,
    # and only when the extractive builders genuinely found nothing (grounded content always wins).
    if not frs and prop.needs_proposals(context.change_class):
        frs = prop.build_proposed_functional_requirements(context)
    if not ac3 and prop.needs_proposals(context.change_class):
        ac3 = prop.build_proposed_acceptance_criteria(frs)
    context_summary = rz.build_context_summary(enriched, context)
    nfrs = rz.build_nfrs(enriched) or det.build_nfr_baseline()  # deterministic baseline when LLM absent

    # Import 2 (IMAD soft-validation): flag numbers/percentages in LLM to_be that don't
    # appear in the source card prose. Returned as DEV-TODO stubs (empty in fixture mode).
    fact_check_stubs = rz.validate_fr_facts(frs, context.card_bodies)

    # Open items = analysis gaps + agent-proposed stubs + fact-check flags
    gap_stubs = det.build_open_items_from_gaps(context.gaps)
    open_items = rz.build_fsd_open_items(enriched, gap_stubs) + fact_check_stubs

    # Merged-BRD sections (business framing folded into the FSD) — deterministic + grounded
    business_case = det.build_business_case(context)
    persona_needs = det.build_persona_needs(context)
    key_decisions = det.build_key_decisions(open_items)

    # PRD (docs/24 §E) — entry/exit criteria (the net-new content over the FSD)
    entry_criteria = det.build_entry_criteria(context)
    exit_criteria = det.build_exit_criteria(context, open_items)

    # Count all BA-TODO / DEV-TODO stubs across sections
    stub_count = (
        sum(1 for fr in frs if fr.stub_marker)
        + sum(1 for nfr in nfrs if nfr.stub_marker)
        + len(open_items)
    )

    # Import 3 (Genlite pattern): section-level provenance — BA sees at a glance which
    # sections are grounded in KB data vs LLM-reasoned vs stub.
    section_basis = {
        "3.Functional Requirements": _basis(
            sum(1 for f in frs if f.source_type == "kb_explicit"),
            sum(1 for f in frs if f.source_type == "kb_inferred"),
            stub=sum(1 for f in frs if f.source_type == "stub"),
        ),
        "4.Process Flows": _basis(
            len(context.proc_matched) + len(context.wf_matched),
            len(context.proc_affected) + len(context.wf_affected),
        ),
        "5.Screen Specifications": _basis(
            len(context.scr_matched), len(context.scr_affected)
        ),
        "6.Business Rules": _basis(
            len(context.br_matched), len(context.br_affected)
        ),
        "7.Data Model Impact": _basis(
            len(context.ent_matched), len(context.ent_affected)
        ),
        "8.Integration Points": _basis(
            len(context.int_matched) + len(context.sys_matched),
            len(context.int_affected) + len(context.sys_affected),
        ),
        "9.Non-Functional Requirements": _basis(0, 0, llm=len(nfrs)),
        "11.Open Items": _basis(0, 0, stub=len(open_items)),
    }

    return FSDDocument(
        workspace_id=workspace_id,
        analysis_ref=context.analysis_ref,
        kb_version=kb_version,
        persona=persona,
        template_id=template.template_id,
        template_version=template.template_version,
        requirement=context.requirement,
        change_class=context.change_class,
        grounding_score=grounding_score,
        stub_count=stub_count,
        context_summary=context_summary,
        business_case=business_case,
        persona_needs=persona_needs,
        key_decisions=key_decisions,
        scope=context.scope,
        functional_requirements=frs,
        process_flows=proc_flows,
        screen_specs=screens,
        business_rules=rules,
        data_model=data_model,
        integration_points=integrations,
        non_functional_reqs=nfrs,
        acceptance_criteria=ac3,
        entry_criteria=entry_criteria,
        exit_criteria=exit_criteria,
        open_items=open_items,
        references=context.matched,
        section_basis=section_basis,
        # [NEW] Section 8b: Technical Deep-Dive (P1/P2 full impact)
        technical_analysis_detail={
            "components": [
                {
                    "id": c.id,
                    "label": c.label,
                    "kind": c.kind,
                    "via_edges": c.via if hasattr(c, 'via') else [],
                }
                for c in context.technical_components
            ],
            "screens": [
                {
                    "id": s.id,
                    "label": s.label,
                    "kind": s.kind,
                    "via_edges": s.via if hasattr(s, 'via') else [],
                }
                for s in context.technical_screens
            ],
            "systems": [
                {
                    "id": sys.id,
                    "label": sys.label,
                    "kind": sys.kind,
                }
                for sys in context.technical_systems
            ],
        } if (context.technical_components or context.technical_screens or context.technical_systems) else None,
        abstained=not context.matched,
        generated_at=generated_at,
    )
