"""Assemble the typed ImpactAnalysis from the deterministic + reasoned section builders."""

from __future__ import annotations

from typing import Any

from app.lifecycle.stages.analysis.match.context import ContextPackage
from app.lifecycle.stages.analysis.schema import AffectedNode, BlindSpot, Blindspot, ImpactAnalysis, LayerGroup
from app.lifecycle.stages.analysis.sections import deterministic as det
from app.lifecycle.stages.analysis.sections import reasoned as rz
from app.lifecycle.templates.registry import Template
from app.utils.logging import log


def _layer_of(kind: str, node_id: str, label: str) -> str:
    """Classify a KB node into UI / service / data architectural layer.

    UI      — screens (SCR-*) + TypeScript/Angular components (CMP-*-TS-*)
    Data    — database entities (ENT-*) + database-backing systems (ASACDP, etc.)
    Service — everything else: Java services, APIs, integrations, workflows, processes
    """
    k = (kind or "").upper()
    nid = (node_id or "").upper()
    lbl = (label or "").upper()
    if k in ("SCR", "SCREEN"):
        return "ui"
    if k in ("ENT", "ENTITY"):
        return "data"
    if k in ("CMP", "COMPONENT"):
        return "ui" if "-TS-" in nid or "ANGULAR" in lbl else "service"
    if k in ("SYS", "SYSTEM"):
        if any(x in lbl for x in ("ASACDP", "DATABASE", "AUTO POLICY DATABASE", "DB ")):
            return "data"
        if any(x in lbl for x in ("ANGULAR", "FRONTEND", "(FRONTEND)", "UI")):
            return "ui"
        return "service"
    return "service"


def _build_layer_groups(
    matched: list, affected: list, data_entities: list[AffectedNode]
) -> LayerGroup:
    """Combine matched + affected nodes into three architectural layers."""
    ui: list[AffectedNode] = []
    service: list[AffectedNode] = []
    data: list[AffectedNode] = list(data_entities)  # ENT-* already classified as data
    seen: set[str] = {n.id for n in data_entities}

    for c in matched:
        if c.id in seen:
            continue
        seen.add(c.id)
        layer = _layer_of(c.kind, c.id, c.label)
        node = AffectedNode(id=c.id, label=c.label, kind=c.kind,
                            source_locus=c.source_locus, summary=c.summary, layer=layer)
        (ui if layer == "ui" else data if layer == "data" else service).append(node)

    for n in affected:
        if n.id in seen:
            continue
        seen.add(n.id)
        layer = _layer_of(n.kind, n.id, n.label)
        n.layer = layer
        (ui if layer == "ui" else data if layer == "data" else service).append(n)

    return LayerGroup(ui=ui, service=service, data=data)


def _enrich_blindspots(blindspot_ids: list[str], matched_cards: list[Any]) -> list[Blindspot]:
    """Enrich blindspot IDs with labels and kinds from matched cards.

    A blindspot is a matched card that has no dependencies in the graph walk.
    Enriching provides context about what was matched but not linked."""
    # Build lookup: id -> {label, kind}
    matched_lookup = {c.id: {"label": c.label, "kind": c.kind} for c in matched_cards}

    enriched: list[Blindspot] = []
    for bs_id in blindspot_ids:
        card = matched_lookup.get(bs_id, {})
        enriched.append(
            Blindspot(
                id=bs_id,
                label=card.get("label"),
                kind=card.get("kind"),
                reason="No dependencies found in the system graph — change may be isolated or graph links are missing",
            )
        )
    return enriched


def assemble_impact_analysis(
    *,
    workspace_id: str,
    kb_version: str,
    persona: str,
    template: Template,
    requirement: str,
    context: ContextPackage,
    enriched: dict[str, Any] | None,
    allowed_ids: set[str],
    grounding_score: float = 1.0,
    generated_at: str | None = None,
    blind_spots: list[Any] | None = None,
    what_is_modified: list[dict] | None = None,
    where_changes_needed: list[Any] | None = None,
) -> ImpactAnalysis:
    """Combine graph-derived + reasoned sections into the single-source ImpactAnalysis artifact."""
    # B1 determinism: classification/scope/modifications/effort are derived from the graph-matched
    # context ONLY — never from the LLM agent's `enriched` output. The agent contributes prose/advisory
    # fields alone (narrative, gaps, risks, drift phrasing), which are whitelist-filtered.
    classification = rz.build_classification(context.matched, context.affected, context.intent_confidence)

    # Three-layer grouping: split matched + affected into UI / Service / Data buckets.
    # ENT-* entities are captured separately (filtered from compute_affected as "implementation detail"
    # but required for the data-layer view).
    data_entities = [
        AffectedNode(id=c.id, label=c.label, kind=c.kind,
                     source_locus=c.source_locus, summary=c.summary, layer="data")
        for c in context.matched if (c.kind or "").upper() in ("ENT", "ENTITY")
    ]
    layer_groups = _build_layer_groups(context.matched, context.affected, data_entities)
    effort, effort_rationale = rz.build_effort(classification, context.matched, context.affected)

    # P1 Fix: Enrich blindspots with labels and kinds from matched cards
    enriched_coverage = context.coverage
    if enriched_coverage.blindspots:
        # Convert string blindspots to enriched Blindspot objects
        enriched_coverage.blindspots = _enrich_blindspots(
            [bs if isinstance(bs, str) else bs.id for bs in enriched_coverage.blindspots],
            context.matched,
        )

    # FIX B (Aug 12, 2026): Group affected nodes by type
    # Instead of returning a flat list of 50 items, group by kind for clarity
    code_affected = [n for n in context.affected if n.kind in ("CMP", "Component", "API", "ApiOp")]
    systems_affected = [n for n in context.affected if n.kind in ("SYS", "System")]
    integrations_affected = [n for n in context.affected if n.kind in ("INT", "Integration")]
    screens_affected = [n for n in context.affected if n.kind in ("SCR", "Screen")]
    workflows_affected = [n for n in context.affected if n.kind in ("WF", "Workflow")]
    rules_affected = [n for n in context.affected if n.kind in ("BR", "BusinessRule", "FR", "FunctionalReq")]
    processes_affected = [n for n in context.affected if n.kind in ("PROC", "Process")]

    # Merge blind spots from multiple sources:
    # 1. Parameter (from technical intent analysis)
    # 2. Context.blind_spots (from impact analyzer, passed through ContextPackage)
    # 3. Semantic walk results (if available)
    blind_spots_list = blind_spots or []
    if context.blind_spots:
        blind_spots_list.extend(context.blind_spots)
        log.info(f"[assemble] Added {len(context.blind_spots)} blind spot(s) from context")
    if context.semantic_walk_result and context.semantic_walk_result.blind_spots:
        blind_spots_list.extend(context.semantic_walk_result.blind_spots)
        log.info(f"[assemble] Added {len(context.semantic_walk_result.blind_spots)} blind spot(s) from semantic walk")

    # Deduplicate by description; coerce @dataclass BlindSpot (technical_intent.models) to the
    # Pydantic BlindSpot (schema.py) — Pydantic 2 rejects dataclass instances as invalid input.
    blind_spots_deduped: list[BlindSpot] = []
    seen: set[str] = set()
    for bs in blind_spots_list:
        desc = getattr(bs, "description", str(bs))
        if desc not in seen:
            seen.add(desc)
            blind_spots_deduped.append(
                bs if isinstance(bs, BlindSpot) else BlindSpot(
                    severity=getattr(bs, "severity", "MEDIUM"),
                    type=getattr(bs, "type", ""),
                    description=desc,
                    action=getattr(bs, "action", ""),
                )
            )

    log.info(f"[assemble] Blind spots: {len(blind_spots_list)} collected → {len(blind_spots_deduped)} after dedup")

    # Log the grouping for transparency
    log.info(
        f"[assemble] Grouped affected nodes: code={len(code_affected)}, systems={len(systems_affected)}, "
        f"integrations={len(integrations_affected)}, screens={len(screens_affected)}, "
        f"workflows={len(workflows_affected)}, rules={len(rules_affected)}, processes={len(processes_affected)}"
    )

    return ImpactAnalysis(
        workspace_id=workspace_id,
        kb_version=kb_version,
        persona=persona,
        template_id=template.template_id,
        template_version=template.template_version,
        requirement=requirement,
        classification=classification,
        narrative=rz.build_narrative(enriched, context.matched, context.affected),
        scope=rz.build_scope(context.matched, context.affected),
        touch_points=det.build_touch_points(context.matched, context.affected),
        modifications=rz.build_modifications(context.matched),
        downstream=context.downstream,
        change_locations=det.build_change_locations(context.affected),
        conflicts=context.conflicts + rz.build_drift_conflicts(enriched, allowed_ids),
        coverage=enriched_coverage,
        risks=rz.build_risks(enriched, allowed_ids),
        effort=effort,
        effort_rationale=effort_rationale,
        affected=context.affected,
        matched=context.matched,
        gaps=rz.build_gaps(enriched) + rz.detect_requirement_gaps(requirement, context.matched, context.affected),
        references=context.matched,
        # FIX B: Grouped impact results (Aug 12, 2026)
        code_affected=code_affected,
        systems_affected=systems_affected,
        integrations_affected=integrations_affected,
        screens_affected=screens_affected,
        workflows_affected=workflows_affected,
        rules_affected=rules_affected,
        processes_affected=processes_affected,
        # [NEW] Technical Analysis Sections (P1/P2 semantic search results)
        # These capture the full unfiltered results for FSD consumption
        technical_components=code_affected,  # All components from P1 semantic search
        technical_screens=screens_affected,  # All screens from P2 semantic search
        technical_systems=systems_affected,  # All systems (always shown)
        blind_spots=blind_spots_deduped,
        layer_groups=layer_groups,
        data_entities=data_entities,
        # [NEW] PRD Impact Sections (Aug 18, 2026)
        # Direct targets vs downstream impact for clear PRD sections
        what_is_modified=[item for item in (what_is_modified or [])],
        where_changes_needed=where_changes_needed or [],
        abstained=not context.matched,
        grounding_score=grounding_score,
        generated_at=generated_at,
    )
