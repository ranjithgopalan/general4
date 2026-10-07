"""Assemble the typed SRDDocument from deterministic + reasoned section builders.

assemble_srd() is SYNC — async graph-walk results (asIs_diagram, seq_diagrams) are
pre-built by the handler and passed as parameters (same handler-then-assemble pattern
as FSD/BRD but with the async step moved up to the handler).

Construction order:
  1. Deterministic sections (scope, FRs, components, integrations, story refs, references)
  2. Reasoned enrichment applied on top (system_context, component responsibility, diagrams, NFRs)
  3. S3 coverage gate (check_arch_coverage → ARCH-TODO open items)
  4. Soft word cap on system_context (300w — Import 1)
  5. section_basis provenance computed (Import 3 — Genlite)
  6. stub_count tallied
"""

from __future__ import annotations

from typing import Any

from app.lifecycle.stages.architecture.match.context import ArchitectureContext
from app.lifecycle.stages.architecture.schema import SOFT_CAPS, SRDDocument
from app.lifecycle.stages.architecture.sections import deterministic as det
from app.lifecycle.stages.architecture.sections import proposed as prop
from app.lifecycle.stages.architecture.sections import reasoned as rz
from app.lifecycle.stages.architecture.sections.fallback_with_kb import FallbackWithKBLookup
from app.lifecycle.templates.registry import Template


def _basis(explicit: int = 0, inferred: int = 0, llm: int = 0, stub: int = 0) -> str:
    """Compact per-section provenance tag (Import 3 — Genlite pattern)."""
    return (
        f"KB-explicit: {explicit} | KB-inferred: {inferred} "
        f"| LLM-reasoned: {llm} | stub: {stub}"
    )


def _comp_counts(components: list) -> tuple[int, int, int]:
    """Return (explicit, inferred, stub) source_type counts for component_design."""
    explicit = sum(1 for c in components if c.source_type in ("kb_explicit", "kb_lookup"))
    inferred = sum(1 for c in components if c.source_type in ("kb_inferred", "inferred"))
    stub = sum(1 for c in components if c.source_type == "stub")
    return explicit, inferred, stub


def _nfr_counts(nfrs: list) -> tuple[int, int, int]:
    """Return (fsd_derived+explicit, llm_reasoned, stub) counts for non_functional_reqs."""
    explicit = sum(1 for n in nfrs if n.source_type in ("kb_explicit", "fsd_derived"))
    llm = sum(1 for n in nfrs if n.source_type == "llm_reasoned")
    stub = sum(1 for n in nfrs if n.source_type == "stub")
    return explicit, llm, stub


async def assemble_srd(
    *,
    workspace_id: str,
    kb_version: str,
    persona: str,
    template: Template,
    context: ArchitectureContext,
    enriched: dict[str, Any] | None,
    allowed_ids: set[str],
    asIs_diagram: str,
    seq_diagrams: list[dict],
    endToEnd_diagram: str = "",
    grounding_score: float = 1.0,
    generated_at: str | None = None,
    kb_service: Any | None = None,
) -> SRDDocument:
    """Combine KB-derived + reasoned sections into the single-source SRDDocument.

    Args:
        asIs_diagram: pre-built Mermaid DSL string from handler's async graph walk.
        seq_diagrams: pre-built list of dicts from handler's async build_sequence_diagrams().
        kb_service: KB query service for grounding fallback designs in real KB data.
    """

    # ── 1. Deterministic sections ─────────────────────────────────────────────
    scope = det.build_scope_definition(context)
    frs = det.build_functional_requirements(context)
    components = det.build_component_design(context)
    integrations = det.build_integration_design(context)
    story_refs = det.build_story_refs(context)
    refs = det.build_references(context)

    # ── C4/C5/C6 — API specs · data model + ERD · schema-change specs ─────────
    api_specs = det.build_api_specs(context)
    data_model = det.build_data_model(context)
    erd_diagram = det.build_erd_diagram(context, data_model)
    schema_changes = det.build_schema_changes(context, data_model)

    # ── KB-GROUNDED FALLBACKS — verify empty sections against real KB data ─────
    if kb_service:
        fallback_gen = FallbackWithKBLookup(kb_service)

        # Build scope from context (from impact analysis or FSD)
        scope_def = det.build_scope_definition(context)

        # Fill schema changes with KB-verified table names if section is empty
        if not schema_changes:
            schema_changes = await fallback_gen.build_schema_changes_with_kb(
                context.requirement,
                scope_def,
                enriched,
            )

        # Fill integrations with KB-verified protocols/queues if section is empty
        if not integrations:
            integrations = await fallback_gen.build_integrations_with_kb(
                context.requirement,
                scope_def,
                enriched,
            )

        # Fill API specs with KB-verified patterns if section is empty
        if not api_specs:
            api_specs = await fallback_gen.build_api_specs_with_kb(
                context.requirement,
                components,
                enriched,
            )

    # Hybrid fill (docs/29): a net-new / light change matches little existing architecture, so §4
    # Component Design comes back empty. Seed clearly-marked PROPOSED components from the requirement
    # + FSD FRs — never for Existing-class, and only when the extractive builder found nothing.
    if not components and prop.needs_proposals(context.change_class):
        components = prop.build_proposed_components(context)

    # ── 2. Reasoned enrichment applied on top ─────────────────────────────────
    system_context_raw = rz.build_system_context(enriched, context)
    components = rz.enrich_component_design(components, enriched, allowed_ids)
    integrations = rz.enrich_integration_design(integrations, enriched, allowed_ids)
    toBe_diagram = rz.build_toBe_diagram(enriched, context, asIs_diagram)
    seq_diag_objs = rz.enrich_sequence_diagrams(seq_diagrams, enriched, allowed_ids)
    nfrs = rz.build_nfr_items(enriched, context)

    # ── 3. S3 coverage gate → ARCH-TODO open items ────────────────────────────
    coverage_stubs = det.check_arch_coverage(components, integrations)
    open_items = rz.build_open_items(enriched, context, coverage_stubs)

    # ── 4. Soft word cap on system_context (300w — Import 1) ──────────────────
    sc_cap = SOFT_CAPS.get("system_context")
    if sc_cap:
        system_context, _ = rz._soft_cap_prose(system_context_raw, sc_cap)
    else:
        system_context = system_context_raw

    # ── 5. Section basis (per-section provenance — Import 3) ──────────────────
    comp_exp, comp_inf, comp_stub = _comp_counts(components)
    nfr_exp, nfr_llm, nfr_stub = _nfr_counts(nfrs)
    scope_items = (
        len(scope.new) + len(scope.enhancement) + len(scope.existing)
        if scope else 0
    )
    section_basis = {
        "1.System Context": _basis(llm=1),
        "2.Scope": _basis(
            explicit=len(scope.new) + len(scope.enhancement) if scope else 0,
            inferred=len(scope.existing) if scope else 0,
            stub=max(0, scope_items - len(scope.new if scope else []) - len(scope.enhancement if scope else [])),
        ),
        "3.Functional Requirements": _basis(explicit=len(frs)),
        "4.Component Design": _basis(explicit=comp_exp, inferred=comp_inf, stub=comp_stub),
        "5.Integration Design": _basis(explicit=len(integrations)),
        "5b.API Specs": _basis(
            explicit=sum(1 for a in api_specs if a.source_type in ("kb_explicit", "kb_lookup")),
            stub=sum(1 for a in api_specs if a.source_type == "stub"),
        ),
        "5c.Data Model": _basis(
            explicit=sum(1 for d in data_model if d.source_type in ("kb_explicit", "kb_lookup")),
            stub=sum(1 for d in data_model if d.source_type == "stub"),
        ),
        "5d.Schema Changes": _basis(
            explicit=sum(1 for s in schema_changes if s.source_type in ("kb_lookup", "kb_grounded")),
            inferred=sum(1 for s in schema_changes if s.source_type == "inferred"),
            llm=sum(1 for s in schema_changes if s.source_type == "llm_reasoned"),
        ),
        "6.Sequence Diagrams": _basis(
            llm=sum(1 for d in seq_diag_objs if d.source_type == "llm_reasoned"),
            stub=sum(1 for d in seq_diag_objs if d.source_type == "stub"),
        ),
        "7.Non-Functional Reqs": _basis(explicit=nfr_exp, llm=nfr_llm, stub=nfr_stub),
        "8.Open Items": _basis(stub=len(open_items)),
        "9.References": _basis(explicit=len(refs)),
    }

    # ── 6. Stub count ─────────────────────────────────────────────────────────
    stub_count = (
        comp_stub
        + sum(1 for i in integrations if not i.protocol)
        + nfr_stub
        + len(coverage_stubs)
        + sum(1 for a in api_specs if a.source_type == "stub")
        + sum(1 for d in data_model if d.source_type == "stub")
    )

    abstained = not context.matched

    return SRDDocument(
        workspace_id=workspace_id,
        stories_ref=context.stories_ref,
        brd_ref=context.brd_ref,
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
        system_context=system_context,
        scope_definition=scope,
        functional_requirements=frs,
        component_design=components,
        integration_design=integrations,
        api_specs=api_specs,
        data_model=data_model,
        schema_changes=schema_changes,
        sequence_diagrams=seq_diag_objs,
        non_functional_reqs=nfrs,
        open_items=open_items,
        references=refs,
        asIs_diagram=asIs_diagram,
        toBe_diagram=toBe_diagram,
        endToEnd_diagram=endToEnd_diagram,
        erd_diagram=erd_diagram,
        story_refs=story_refs,
        section_basis=section_basis,
        abstained=abstained,
        generated_at=generated_at,
    )
