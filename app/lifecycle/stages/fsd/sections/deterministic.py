"""Deterministic (graph-derived) FSD section builders — no LLM required.

Every builder works from ImpactCitation / AffectedNode lists already assembled in FSDContext.
Labels and source_locus come directly from the KB retrieval; no fabrication is possible.
The ``source_type`` tag distinguishes kb_explicit (in analysis.matched) from kb_inferred
(in analysis.affected only) — adopted from xpf feasibility_comprehensive_service.py pattern.
"""

from __future__ import annotations

from typing import Any

from app.lifecycle.stages.analysis.schema import AffectedNode, ImpactCitation
from app.lifecycle.stages.fsd.match.context import FSDContext
from app.lifecycle.stages.fsd.schema import (
    NFR,
    ACRow,
    BusinessRule,
    DataModelItem,
    FunctionalRequirement,
    IntegrationPoint,
    KeyDecision,
    PersonaNeed,
    ProcessFlow,
    ScreenSpec,
    Stub,
)

_ROLE_KINDS = {"Role", "ROLE"}


def _card_prose(bodies: dict[str, dict[str, Any]], card_id: str) -> str | None:
    """Extract text from a card_bodies entry; returns None when absent so callers can fall back."""
    card = bodies.get(card_id)
    if not card:
        return None
    text = str(card.get("prose") or card.get("text_en") or "")
    return text or None


# link_type per KB kind — AC-3 matrix (xpf TraceLink relationship_type pattern).
# Keyed by BOTH full Neo4j labels and the short ID-family codes the analysis emits (FR/BR/SCR/…).
_LINK_FOR_KIND: dict[str, str] = {
    "FunctionalReq": "IMPLEMENTS", "FR": "IMPLEMENTS",
    "BusinessRule": "GOVERNED_BY", "BR": "GOVERNED_BY",
    "Screen": "SCREEN_OF", "SCR": "SCREEN_OF",
    "Process": "IMPLEMENTS", "PROC": "IMPLEMENTS",
    "Workflow": "IMPLEMENTS", "WF": "IMPLEMENTS",
    "Integration": "DEPENDS_ON", "INT": "DEPENDS_ON",
    "ApiOp": "DEPENDS_ON", "API": "DEPENDS_ON",
    "Entity": "DEPENDS_ON", "ENT": "DEPENDS_ON",
    "System": "DEPENDS_ON", "SYS": "DEPENDS_ON",
}


def _citation_to_fr(c: ImpactCitation, source_type: str, bodies: dict[str, dict[str, Any]]) -> FunctionalRequirement:
    return FunctionalRequirement(
        id=c.id,
        title=c.label,
        as_is=_card_prose(bodies, c.id) or c.label,  # full prose when available; label as fallback
        to_be=None,              # filled by reasoned.py after ReAct enrichment
        source_locus=c.source_locus,
        source_type=source_type,
        priority="Medium",
        stub_marker=None,
    )


def _affected_to_fr(n: AffectedNode) -> FunctionalRequirement:
    return FunctionalRequirement(
        id=n.id,
        title=n.label,
        as_is=n.label,
        to_be=None,
        source_locus=n.source_locus,
        source_type="kb_inferred",
        priority="Low",
        stub_marker=None,
    )


def build_functional_requirements(context: FSDContext) -> list[FunctionalRequirement]:
    """FR-* KB cards: explicit (matched) first, inferred (affected) appended, deduped."""
    seen: set[str] = set()
    out: list[FunctionalRequirement] = []
    for c in context.fr_matched:
        seen.add(c.id)
        out.append(_citation_to_fr(c, "kb_explicit", context.card_bodies))
    for n in context.fr_affected:
        if n.id not in seen:
            seen.add(n.id)
            out.append(_affected_to_fr(n))
    return out


def build_process_flows(context: FSDContext) -> list[ProcessFlow]:
    """PROC-* and WF-* KB nodes as FSD process-flow entries."""
    seen: set[str] = set()
    out: list[ProcessFlow] = []
    bodies = context.card_bodies
    for c in context.proc_matched + context.wf_matched:
        if c.id not in seen:
            seen.add(c.id)
            out.append(ProcessFlow(
                id=c.id, title=c.label, kind="Process",
                steps=[_card_prose(bodies, c.id)] if _card_prose(bodies, c.id) else [],
                source_locus=c.source_locus,
            ))
    for n in context.proc_affected + context.wf_affected:
        if n.id not in seen:
            seen.add(n.id)
            out.append(ProcessFlow(
                id=n.id, title=n.label, kind=n.kind,
                steps=[_card_prose(bodies, n.id)] if _card_prose(bodies, n.id) else [],
                source_locus=n.source_locus,
            ))
    return out


def build_screen_specs(context: FSDContext) -> list[ScreenSpec]:
    """SCR-* KB nodes as FSD screen specifications."""
    seen: set[str] = set()
    out: list[ScreenSpec] = []
    bodies = context.card_bodies
    for c in context.scr_matched:
        if c.id not in seen:
            seen.add(c.id)
            out.append(ScreenSpec(
                id=c.id, title=c.label,
                purpose=_card_prose(bodies, c.id) or c.label,  # full card prose when available
                source_locus=c.source_locus,
            ))
    for n in context.scr_affected:
        if n.id not in seen:
            seen.add(n.id)
            out.append(ScreenSpec(
                id=n.id, title=n.label,
                purpose=_card_prose(bodies, n.id) or n.label,
                source_locus=n.source_locus,
            ))
    return out


def build_business_rules(context: FSDContext) -> list[BusinessRule]:
    """BR-* KB nodes as FSD business rule entries.

    ``applies_to`` is populated from the matched screen IDs in context — the screens
    co-retrieved with these BRs for the same requirement.  This is an approximation
    (a BR may govern only a subset of matched screens) but far better than the empty
    default, which causes the trace graph to fall back to all-pairs system connections.
    """
    matched_scr_ids = [c.id for c in context.scr_matched]
    seen: set[str] = set()
    out: list[BusinessRule] = []
    bodies = context.card_bodies
    for c in context.br_matched:
        if c.id not in seen:
            seen.add(c.id)
            out.append(BusinessRule(
                id=c.id, title=c.label,
                rule_text=_card_prose(bodies, c.id) or c.label,
                applies_to=matched_scr_ids,
                source_locus=c.source_locus,
            ))
    for n in context.br_affected:
        if n.id not in seen:
            seen.add(n.id)
            out.append(BusinessRule(
                id=n.id, title=n.label,
                rule_text=_card_prose(bodies, n.id) or n.label,
                applies_to=matched_scr_ids,
                source_locus=n.source_locus,
            ))
    return out


def build_data_model(context: FSDContext) -> list[DataModelItem]:
    """ENT-* KB nodes — 'modified' if in matched set, 'referenced' if only affected."""
    seen: set[str] = set()
    out: list[DataModelItem] = []
    for c in context.ent_matched:
        seen.add(c.id)
        out.append(DataModelItem(id=c.id, label=c.label, impact="modified", source_locus=c.source_locus))
    for n in context.ent_affected:
        if n.id not in seen:
            seen.add(n.id)
            out.append(DataModelItem(id=n.id, label=n.label, impact="referenced", source_locus=n.source_locus))
    return out


def build_integration_points(context: FSDContext) -> list[IntegrationPoint]:
    """INT-*/API-*/SYS-* KB nodes as FSD integration points."""
    seen: set[str] = set()
    out: list[IntegrationPoint] = []
    bodies = context.card_bodies
    all_matched = context.int_matched + context.sys_matched
    all_affected = context.int_affected + context.sys_affected
    for c in all_matched:
        if c.id not in seen:
            seen.add(c.id)
            out.append(IntegrationPoint(
                id=c.id, label=c.label, kind=c.kind,
                note=_card_prose(bodies, c.id),  # full card prose in note when available
                source_locus=c.source_locus,
            ))
    for n in all_affected:
        if n.id not in seen:
            seen.add(n.id)
            out.append(IntegrationPoint(
                id=n.id, label=n.label, kind=n.kind,
                note=_card_prose(bodies, n.id),
                source_locus=n.source_locus,
            ))
    return out


def build_acceptance_criteria(context: FSDContext) -> list[ACRow]:
    """AC-3 matrix: one row per matched KB card (FSD section → KB ID → source locus)."""
    out: list[ACRow] = []
    section_for_kind = {
        "FunctionalReq": "3.Functional Requirements", "FR": "3.Functional Requirements",
        "BusinessRule":  "6.Business Rules", "BR": "6.Business Rules",
        "Screen":        "5.Screen Specifications", "SCR": "5.Screen Specifications",
        "Process":       "4.Process Flows", "PROC": "4.Process Flows",
        "Workflow":      "4.Process Flows", "WF": "4.Process Flows",
        "Integration":   "8.Integration Points", "INT": "8.Integration Points",
        "ApiOp":         "8.Integration Points", "API": "8.Integration Points",
        "Entity":        "7.Data Model Impact", "ENT": "7.Data Model Impact",
        "System":        "8.Integration Points", "SYS": "8.Integration Points",
    }
    for c in context.matched:
        link = _LINK_FOR_KIND.get(c.kind, "DEPENDS_ON")
        section = section_for_kind.get(c.kind, "12.References & Evidence")
        out.append(ACRow(
            fsd_section=section,
            kb_id=c.id,
            kb_label=c.label,
            source_locus=c.source_locus,
            link_type=link,
        ))
    return out


def build_entry_criteria(context: FSDContext) -> list["Criterion"]:
    """PRD — Definition of Ready: business preconditions for development to begin (stakeholder-facing,
    no internal SDLC-gate jargon). Where a condition is objectively derivable it is marked met."""
    from app.lifecycle.stages.fsd.schema import Criterion

    grounded = bool(context.matched)
    return [
        Criterion(id="entry-need", text="Business objective and the change are clearly defined",
                  met=True, source_type="ba_reasoned"),
        Criterion(id="entry-impact", text="Impact on the existing system has been assessed and reviewed",
                  met=grounded, source_type="kb_grounded"),
        Criterion(id="entry-ac", text="Acceptance criteria are defined and testable",
                  met=True, source_type="ba_reasoned"),
        Criterion(id="entry-deps", text="Data, integration and reporting touchpoints are identified",
                  met=False, source_type="ba_reasoned"),
        Criterion(id="entry-scope", text="Scope (in / out) is agreed with the Product Owner",
                  met=False, source_type="ba_reasoned"),
    ]


def build_exit_criteria(context: FSDContext, open_items: list) -> list["Criterion"]:
    """PRD — Definition of Done: business conditions for the change to be considered complete
    (stakeholder-facing). ``met`` reflects objectively-derivable state where possible."""
    from app.lifecycle.stages.fsd.schema import Criterion

    no_open = len(open_items) == 0
    return [
        Criterion(id="exit-ac", text="All acceptance criteria are met and verified",
                  met=False, source_type="ba_reasoned"),
        Criterion(id="exit-nfr", text="Functional and non-functional requirements are satisfied",
                  met=False, source_type="ba_reasoned"),
        Criterion(id="exit-open", text="All open items are resolved or explicitly accepted by the business",
                  met=no_open, source_type="kb_grounded"),
        Criterion(id="exit-docs", text="The solution is documented and the knowledge base is updated",
                  met=False, source_type="ba_reasoned"),
        Criterion(id="exit-signoff", text="Product Owner sign-off is recorded",
                  met=False, source_type="ba_reasoned"),
    ]


def build_nfr_baseline() -> list[NFR]:
    """Standard NFR checklist (BA-TODO) used when neither the KB nor the LLM supplies NFRs.

    These are section templates (categories + prompts), not fabricated values — the BA fills the
    specifics. Prevents §9 from rendering empty while respecting cite-or-abstain (marked BA-TODO).
    """
    defaults = [
        ("Performance", "Response-time and throughput targets for the new screen/flow."),
        ("Security", "PII handling, authentication/authorization, and audit logging per AIG policy."),
        ("Availability", "Availability and recovery targets per the platform SLA."),
        ("Compliance", "Japan regulatory and AIG governance requirements applicable to this change."),
        ("Accessibility", "Japanese (ja-JP) localization and accessibility standards."),
    ]
    return [
        NFR(id=f"nfr-{i + 1}", category=c, description=f"{d} — to be defined.", stub_marker="BA-TODO")
        for i, (c, d) in enumerate(defaults)
    ]


def build_open_items_from_gaps(gaps: list[str]) -> list[Stub]:
    """Convert analysis gaps to BA-TODO stubs in the Open Items section."""
    return [
        Stub(
            id=f"stub-gap-{i + 1}",
            section="11.Open Items",
            description=g,
            marker="BA-TODO",
            priority="Medium",
        )
        for i, g in enumerate(gaps)
    ]


# ── Merged-BRD sections (business framing folded into the FSD) ──────────────────────
def build_business_case(context: FSDContext) -> str:
    """Business framing (merged BRD): why this change matters — grounded in the KB match, no fabrication."""
    req = (context.requirement or "").strip()
    if not req:
        return ""
    kinds = ", ".join(sorted({c.kind for c in context.matched})) or "no matched concepts"
    return (
        f"This {context.change_class or 'change'} to the Japan Auto system addresses: {req} "
        f"It is grounded in {len(context.matched)} KB concept(s) ({kinds}), so the delivered work traces "
        f"back to the existing knowledge base."
    )


def build_persona_needs(context: FSDContext) -> list[PersonaNeed]:
    """Target personas & needs (merged BRD) from the matched ROLE cards — grounded, no fabrication."""
    out: list[PersonaNeed] = []
    seen: set[str] = set()
    for c in context.matched:
        if c.kind in _ROLE_KINDS and c.id not in seen:
            seen.add(c.id)
            prose = (_card_prose(context.card_bodies, c.id) or "").strip()
            out.append(PersonaNeed(
                persona=c.label or c.id,
                use_case=prose[:160],
                need=f"Needs this change to support: {(context.requirement or '').strip()[:120]}",
                source="kb_card",
            ))
    return out


def build_key_decisions(open_items: list[Stub]) -> list[KeyDecision]:
    """Key decisions & open items log (merged BRD) — surfaces the FSD's open items as decision entries."""
    return [
        KeyDecision(tag="OPEN", description=s.description, section=s.section or "", marker=s.marker or "BA-TODO")
        for s in open_items
    ]
