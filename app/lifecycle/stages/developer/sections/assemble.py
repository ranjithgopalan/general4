"""Assemble the typed DevDocument from deterministic + ReAct-enriched section builders.

assemble_dev() is SYNC — it is called from the handler after the ReAct enrichment dict
arrives (or after a graceful abstain). Mirrors the SRD/FSD assemble pattern.

Construction order:
  1. Deterministic: impl_tasks + code_stubs + wiring + coverage check
  2. Reasoned enrichment applied on top (development_notes from LLM, dev_gaps augmented)
  3. Merge inherited SRD gaps into dev_gaps
  4. Section basis provenance (Import 3 — Genlite pattern)
  5. stub_count tallied
"""

from __future__ import annotations

from typing import Any

from app.lifecycle.stages.developer.match.context import DevContext
from app.lifecycle.stages.developer.schema import (
    CodegenFile,
    DevDocument,
    DevGap,
    DevNote,
)
from app.lifecycle.stages.developer.sections import deterministic as det
from app.lifecycle.stages.developer.sections import fallback_with_stubs as fallback
from app.lifecycle.stages.developer.sections.lanes import build_change_specs
from app.lifecycle.stages.fsd.schema import Stub
from app.lifecycle.templates.registry import Template


def _basis(explicit: int = 0, inferred: int = 0, llm: int = 0, stub: int = 0) -> str:
    """Compact per-section provenance tag (Import 3 — Genlite pattern)."""
    return (
        f"KB-explicit: {explicit} | KB-inferred: {inferred} "
        f"| LLM-reasoned: {llm} | stub: {stub}"
    )


def _extract_notes(enriched: dict[str, Any] | None) -> list[DevNote]:
    """Extract LLM-reasoned development_notes from ReAct enrichment dict.

    Handles both LEGACY format (flat development_notes list) and NEW format
    (nested implementation_plan with components/apis/database_plan/etc).
    """
    if not enriched:
        return []

    notes = []
    note_id_counter = 0

    # NEW FORMAT: nested implementation_plan
    impl_plan = enriched.get("implementation_plan") or {}

    # Extract from components (per-component implementation guidance)
    components = impl_plan.get("components") or []
    for comp in components[:10]:  # max 10 components
        if isinstance(comp, dict):
            comp_id = comp.get("component_id", "")
            what_to_build = comp.get("what_to_build", "").strip()
            tech_stack = comp.get("tech_stack", "").strip()
            effort = comp.get("effort_estimate_days", "")

            text_parts = []
            if comp_id:
                text_parts.append(f"[{comp_id}]")
            if what_to_build:
                text_parts.append(what_to_build)
            if tech_stack:
                text_parts.append(f"Tech: {tech_stack}")
            if effort and effort != "DEV-TODO":
                text_parts.append(f"Effort: {effort}d")

            if text_parts:
                text = " — ".join(text_parts)
                notes.append(DevNote(
                    note_id=f"dev-note-llm-{note_id_counter:03d}",
                    text=text,
                    author="react-agent",
                ))
                note_id_counter += 1

    # Extract from APIs (per-API implementation guidance)
    apis = impl_plan.get("apis") or []
    for api in apis[:10]:  # max 10 APIs
        if isinstance(api, dict):
            api_id = api.get("api_id", "")
            endpoint = api.get("endpoint", "").strip()
            impl_note = api.get("implementation_note", "").strip()

            text_parts = []
            if api_id:
                text_parts.append(f"[{api_id}]")
            if endpoint:
                text_parts.append(endpoint)
            if impl_note:
                text_parts.append(impl_note)

            if text_parts:
                text = " — ".join(text_parts)
                notes.append(DevNote(
                    note_id=f"dev-note-llm-{note_id_counter:03d}",
                    text=text,
                    author="react-agent",
                ))
                note_id_counter += 1

    # Extract from database plan
    db_plan = impl_plan.get("database_plan") or {}
    if isinstance(db_plan, dict):
        tables = db_plan.get("tables_affected") or []
        if tables:
            text = f"Database: Modify tables {', '.join(tables)} (DBA review + Flyway migration required)"
            notes.append(DevNote(
                note_id=f"dev-note-llm-{note_id_counter:03d}",
                text=text,
                author="react-agent",
            ))
            note_id_counter += 1

        perf = db_plan.get("performance_impact", "").strip()
        if perf:
            notes.append(DevNote(
                note_id=f"dev-note-llm-{note_id_counter:03d}",
                text=f"Performance: {perf}",
                author="react-agent",
            ))
            note_id_counter += 1

    # Extract from deployment checklist
    checklist = impl_plan.get("deployment_checklist") or []
    if checklist:
        checklist_text = " | ".join([str(c) for c in checklist[:5]])  # first 5 items
        notes.append(DevNote(
            note_id=f"dev-note-llm-{note_id_counter:03d}",
            text=f"Deployment: {checklist_text}",
            author="react-agent",
        ))
        note_id_counter += 1

    # Extract from monitoring
    monitoring = impl_plan.get("monitoring_and_alerts") or []
    if monitoring:
        monitoring_text = " | ".join([str(m) for m in monitoring[:3]])  # first 3 metrics
        notes.append(DevNote(
            note_id=f"dev-note-llm-{note_id_counter:03d}",
            text=f"Monitoring: {monitoring_text}",
            author="react-agent",
        ))
        note_id_counter += 1

    # LEGACY FORMAT: flat development_notes list (for backwards compatibility)
    if not notes:  # Only if new format didn't yield anything
        raw = enriched.get("development_notes") or []
        for i, item in enumerate(raw[:20]):  # max 20 LLM notes
            text = ""
            if isinstance(item, str):
                text = item.strip()
            elif isinstance(item, dict):
                text = (item.get("text") or item.get("note") or "").strip()
            if text:
                notes.append(DevNote(
                    note_id=f"dev-note-llm-{i:03d}",
                    text=text,
                    author="react-agent",
                ))

    return notes


def _extract_extra_gaps(
    enriched: dict[str, Any] | None, existing_gaps: list[DevGap]
) -> list[DevGap]:
    """Extract any additional dev gaps from ReAct enrichment that aren't already recorded.

    Handles both LEGACY format (flat dev_gaps list) and NEW format
    (nested implementation_plan.dev_gaps).
    """
    if not enriched:
        return []

    existing_descs = {g.description for g in existing_gaps}
    extras: list[DevGap] = []

    # NEW FORMAT: nested dev_gaps inside implementation_plan
    impl_plan = enriched.get("implementation_plan") or {}
    raw = impl_plan.get("dev_gaps") or []

    # LEGACY FORMAT: flat dev_gaps at top level (backwards compatibility)
    if not raw:
        raw = enriched.get("dev_gaps") or []

    for item in raw[:20]:  # max 20 gaps
        if isinstance(item, str):
            desc = item.strip()
            severity = "medium"
        elif isinstance(item, dict):
            desc = (item.get("description") or "").strip()
            severity = item.get("severity", "medium")
        else:
            continue

        if desc and desc not in existing_descs:
            # NEW FORMAT has 'severity' field; LEGACY has 'gap_status'
            gap_status = item.get("gap_status", "gap") if isinstance(item, dict) else "gap"
            sme_required = item.get("sme_required", True) if isinstance(item, dict) else True

            extras.append(DevGap(
                description=desc,
                gap_status=gap_status,
                sme_required=sme_required,
                source="react_agent",
            ))
            existing_descs.add(desc)  # Prevent duplicates

    return extras


def _build_inherited_srd_gaps(ctx: DevContext) -> list[DevGap]:
    """Convert SRD open_items (ARCH-TODO) into DevGaps for developer visibility."""
    gaps: list[DevGap] = []
    for gap_text in ctx.gaps[:30]:
        gaps.append(DevGap(
            description=f"[From SRD] {gap_text}",
            gap_status="deferred",
            sme_required=False,
            source="srd_inheritance",
        ))
    return gaps


def assemble_dev(
    *,
    workspace_id: str,
    kb_version: str,
    persona: str,
    template: Template,
    context: DevContext,
    enriched: dict[str, Any] | None,
    allowed_ids: set[str],
    fsd_load_failed: bool = False,
    grounding_score: float = 1.0,
    generated_at: str | None = None,
) -> DevDocument:
    """Combine KB-derived + ReAct-enriched sections into the single-source DevDocument.

    Args:
        enriched: dict returned by the ReAct agent (or None on abstain/error).
                  Expected keys: development_notes, dev_gaps.
        allowed_ids: developer-visible KB IDs (from DevContext.allowed_ids).
        fsd_load_failed: True if FSDDocument failed to load (alerts developer).
    """

    # ── 1. Deterministic sections ─────────────────────────────────────────────
    impl_tasks, det_gaps = det.build_impl_tasks(context)
    code_stubs = det.build_code_stubs(context)

    # ── 1a. Fallback stub generation (when API specs / schema changes exist) ───
    # Mirrors Architecture stage fallback pattern: API spec + schema change → stubs.
    # For ENHANCEMENT: modify existing services. For NEW: create new services + controllers.
    fallback_stubs = fallback.build_inferred_stubs(context)
    code_stubs.extend(fallback_stubs)  # Append to deterministic stubs

    change_plan = det.build_change_plan(code_stubs)  # high-level files-to-change summary (no code)
    wiring_tasks = det.build_wiring_tasks(context)
    change_specs = build_change_specs(context)  # docs/24 §D — frontend/backend/integration/db lanes
    open_items = det.check_dev_coverage(impl_tasks, code_stubs, wiring_tasks, context)

    # ── 2. Reasoned enrichment on top ─────────────────────────────────────────
    llm_notes = _extract_notes(enriched)
    extra_gaps = _extract_extra_gaps(enriched, det_gaps)

    # ── 3. Dev gaps: deterministic + inherited SRD + LLM extras + FSD alert ───
    srd_gaps = _build_inherited_srd_gaps(context)
    dev_gaps = det_gaps + srd_gaps + extra_gaps

    # Alert if FSD load failed — developer won't see FR deltas or BR summaries
    if fsd_load_failed:
        dev_gaps.append(DevGap(
            description="FSD (Functional Spec) failed to load — FR deltas (as_is→to_be) and BR summaries will be missing from implementation tasks",
            gap_status="deferred",
            sme_required=False,
            source="fsd_load_failure",
        ))

    # ── 4. Section basis (per-section provenance — Import 3) ──────────────────
    auto_count = sum(1 for t in impl_tasks if t.ownership == "AUTO")
    hybrid_count = sum(1 for t in impl_tasks if t.ownership == "HYBRID")
    todo_count = sum(1 for t in impl_tasks if t.ownership == "DEV-TODO")

    stub_stubs = sum(1 for s in code_stubs if s.ownership == "DEV-TODO")
    auto_stubs = sum(1 for s in code_stubs if s.ownership == "AUTO")
    hybrid_stubs = sum(1 for s in code_stubs if s.ownership == "HYBRID")
    # Inferred stubs from API specs + schema changes (fallback generation)
    inferred_api_stubs = sum(1 for s in code_stubs if "api_inferred" in (s.source_type or ""))
    inferred_schema_stubs = sum(1 for s in code_stubs if "schema_inferred" in (s.source_type or ""))
    inferred_form_stubs = sum(1 for s in code_stubs if "form_inferred" in (s.source_type or ""))

    section_basis = {
        "1.Implementation Plan": _basis(
            explicit=auto_count,
            inferred=hybrid_count,
            stub=todo_count,
        ),
        "2.Code Stubs": _basis(
            explicit=auto_stubs,
            inferred=hybrid_stubs + inferred_api_stubs + inferred_schema_stubs + inferred_form_stubs,
            stub=stub_stubs,
        ),
        "3.Integration Wiring": _basis(
            explicit=sum(1 for w in wiring_tasks if w.ownership != "DEV-TODO"),
            stub=sum(1 for w in wiring_tasks if w.ownership == "DEV-TODO"),
        ),
        "4.Development Notes": _basis(
            llm=len(llm_notes),
        ),
        "5.Open Items": _basis(
            stub=len(open_items),
        ),
        "6.Change Specs": _basis(
            explicit=sum(1 for c in change_specs if c.gap_status == "direct"),
            stub=sum(1 for c in change_specs if c.gap_status == "gap"),
        ),
    }

    # ── 5. Stub count ─────────────────────────────────────────────────────────
    stub_count = todo_count + stub_stubs + len(open_items) + sum(
        1 for c in change_specs if c.gap_status == "gap"
    )

    abstained = (not context.story_refs and not context.cmp_cards and not context.api_cards)

    # HONEST grounding score (was hardcoded 1.0 → every doc showed "Grounded 100%", even an empty
    # DEV-TODO-only one). Real score = fraction of implementation units that are grounded (AUTO/HYBRID
    # /direct), not DEV-TODO/gap stubs; 0 when abstained or there is nothing to implement.
    total_units = len(impl_tasks) + len(code_stubs) + len(wiring_tasks) + len(change_specs)
    grounded_units = (
        auto_count + hybrid_count + auto_stubs + hybrid_stubs
        + sum(1 for w in wiring_tasks if w.ownership != "DEV-TODO")
        + sum(1 for c in change_specs if c.gap_status == "direct")
    )
    grounding_score = 0.0 if (abstained or total_units == 0) else round(min(1.0, grounded_units / total_units), 4)

    return DevDocument(
        workspace_id=workspace_id,
        srd_ref=context.srd_ref,
        kb_version=kb_version,
        persona=persona,
        template_id=template.template_id,
        template_version=template.template_version,
        requirement=context.requirement,
        change_class=context.change_class,
        grounding_score=grounding_score,
        stub_count=stub_count,
        change_plan=change_plan,
        implementation_plan=impl_tasks,
        code_stubs=code_stubs,
        integration_wiring=wiring_tasks,
        change_specs=change_specs,
        development_notes=llm_notes,
        open_items=open_items,
        codegen_files=[],           # populated by the separate codegen route
        codegen_output_dir=None,    # populated by the separate codegen route
        dev_gaps=dev_gaps,
        section_basis=section_basis,
        abstained=abstained,
        generated_at=generated_at,
    )
