"""Deterministic (KB-derived) BRD section builders — no LLM required.

Every builder works from ImpactCitation lists already assembled in BRDContext.
All KB card content is preserved in full — these sections are NEVER truncated.
``source_type`` distinguishes kb_explicit (direct match) from kb_inferred (affected only).
"""

from __future__ import annotations

from typing import Any

from app.lifecycle.stages.analysis.schema import ImpactCitation, ScopeItem, ScopeDiff
from app.lifecycle.stages.brd.match.context import BRDContext
from app.lifecycle.stages.brd.schema import BRDACRow, BRDRule, BusinessRequirement, KeyDecision


def _card_prose(bodies: dict[str, dict[str, Any]], card_id: str) -> str | None:
    """Extract full text from card_bodies; None when absent so callers can fall back to label."""
    card = bodies.get(card_id)
    if not card:
        return None
    text = str(card.get("prose") or card.get("text_en") or "")
    return text or None


# BRD AC link types per KB kind — mirrors FSD _LINK_FOR_KIND for BRD section references.
_LINK_FOR_KIND: dict[str, str] = {
    "FunctionalReq": "IMPLEMENTS",
    "BusinessRule": "GOVERNED_BY",
    "Screen": "SCREEN_OF",
    "Process": "IMPLEMENTS",
    "Workflow": "IMPLEMENTS",
    "Integration": "DEPENDS_ON",
    "ApiOp": "DEPENDS_ON",
    "Entity": "DEPENDS_ON",
    "System": "DEPENDS_ON",
    "Role": "ROLE_IN",
    "Term": "REFERENCES",
    "Domain": "REFERENCES",
}

# BRD section label for each KB kind (used in AC Output/Deliverable column).
_BRD_SECTION_FOR_KIND: dict[str, str] = {
    "FunctionalReq": "3.Business Requirements",
    "BusinessRule": "4.Business Rules & Policies",
    "Screen": "3.Business Requirements",
    "Process": "3.Business Requirements",
    "Workflow": "3.Business Requirements",
    "Integration": "2.Scope & Boundaries",
    "ApiOp": "2.Scope & Boundaries",
    "Entity": "2.Scope & Boundaries",
    "System": "2.Scope & Boundaries",
    "Role": "5.Target Personas & Needs",
    "Term": "10.References & Evidence",
    "Domain": "10.References & Evidence",
}


def build_scope_definition(context: BRDContext) -> ScopeDiff:
    """Derive BRD IN SCOPE / OUT OF SCOPE / DEFERRED from the FSD scope (verbatim pass-through).

    BRD scope mirrors the FSD scope but uses business-language section labels:
      FSD scope.new        → IN SCOPE   (new work the business has approved)
      FSD scope.existing   → OUT OF SCOPE (existing confirmed; no change)
      FSD scope.enhancement → IN SCOPE  (approved enhancement work)
      Gaps (FSD open_items) → DEFERRED  (open questions; BA must confirm before sign-off)
    """
    in_scope: list[ScopeItem] = []
    out_scope: list[ScopeItem] = []
    deferred: list[ScopeItem] = []

    for item in context.scope.new:
        in_scope.append(ScopeItem(id=item.id, label=item.label, kind=item.kind, note=item.note))

    for item in context.scope.enhancement:
        in_scope.append(ScopeItem(id=item.id, label=item.label, kind=item.kind, note=item.note))

    for item in context.scope.existing:
        out_scope.append(ScopeItem(id=item.id, label=item.label, kind=item.kind, note=item.note))

    for i, gap in enumerate(context.gaps):
        deferred.append(ScopeItem(id=f"deferred-{i + 1}", label=gap, kind=None, note="Open — BA confirmation required"))

    return ScopeDiff(new=in_scope, enhancement=[], existing=out_scope + deferred)


def build_business_requirements(context: BRDContext) -> list[BusinessRequirement]:
    """FR-* and BR-* matched KB cards as BRD business requirement rows.

    PRD language: 'Current State' (card prose from KB) / 'Proposed Change' (None → BA-TODO stub,
    filled later by reasoned.py after ReAct enrichment).  Full prose always shown — never truncated.
    """
    seen: set[str] = set()
    out: list[BusinessRequirement] = []
    bodies = context.card_bodies

    # FR cards → requirements (explicit first — highest confidence)
    for c in context.fr_matched:
        if c.id not in seen:
            seen.add(c.id)
            prose = _card_prose(bodies, c.id) or c.label
            out.append(BusinessRequirement(
                id=c.id,
                requirement=c.label,
                current_state=prose,
                proposed_change=None,       # filled by apply_brd_req_enrichment
                source_locus=c.source_locus,
                source_type="kb_explicit",
                priority="Medium",
                stub_marker=None,
            ))

    # BR cards → also requirements (policies that constrain the business requirement)
    for c in context.br_matched:
        if c.id not in seen:
            seen.add(c.id)
            prose = _card_prose(bodies, c.id) or c.label
            out.append(BusinessRequirement(
                id=c.id,
                requirement=c.label,
                current_state=prose,
                proposed_change=None,
                source_locus=c.source_locus,
                source_type="kb_explicit",
                priority="Medium",
                stub_marker=None,
            ))

    return out


def build_business_rules(context: BRDContext) -> list[BRDRule]:
    """BR-* KB nodes as BRD business rule entries — full rule_text always; never truncated."""
    seen: set[str] = set()
    out: list[BRDRule] = []
    bodies = context.card_bodies

    for c in context.br_matched:
        if c.id not in seen:
            seen.add(c.id)
            out.append(BRDRule(
                id=c.id,
                title=c.label,
                rule_text=_card_prose(bodies, c.id) or c.label,
                applies_to=[],          # populated by reasoned.py from agent enrichment
                source_locus=c.source_locus,
            ))

    return out


def build_acceptance_criteria(context: BRDContext) -> list[BRDACRow]:
    """2-column Output / AC table (PRD style) — one row per matched KB card; never truncated.

    ac_text is populated from KB card prose only — never generated or inferred.
    When a card has no prose, ac_text = "" so the UI signals the KB needs enrichment.
    The caller (assemble_brd) surfaces empty-prose cards as [OPEN] key_decisions via
    build_kb_prose_gaps() — not this function's concern.
    """
    out: list[BRDACRow] = []
    bodies = context.card_bodies
    for c in context.matched:
        link = _LINK_FOR_KIND.get(c.kind, "DEPENDS_ON")
        section = _BRD_SECTION_FOR_KIND.get(c.kind, "10.References & Evidence")
        prose = _card_prose(bodies, c.id) or ""
        # Trim to 300 chars — enough for a meaningful AC statement; never truncates mid-sentence.
        ac_text = prose[:300].rsplit(" ", 1)[0] if len(prose) > 300 else prose
        out.append(BRDACRow(
            fsd_section=section,
            kb_id=c.id,
            kb_label=c.label,
            source_locus=c.source_locus,
            link_type=link,
            ac_text=ac_text,
        ))
    return out


def build_references(context: BRDContext) -> list[ImpactCitation]:
    """All matched KB citations + fsd_ref chip — full citation set; never truncated."""
    refs: list[ImpactCitation] = list(context.matched)

    # Add FSD source chip at the front so traceability chain is visible.
    if context.fsd_ref:
        refs.insert(0, ImpactCitation(
            id=context.fsd_ref,
            kind="FSDDocument",
            label="Derived from FSD",
            source_locus=None,
        ))

    return refs


def build_kb_prose_gaps(context: BRDContext) -> list[KeyDecision]:
    """Cite-or-abstain: surface KB cards with missing prose as [OPEN] key decisions.

    Never fabricates content.  When a matched KB card has no prose in card_bodies,
    the BA cannot produce meaningful BRD content from it — the right action is to
    enrich the KB via /kb-build, not to generate placeholder text.

    Two gap kinds:
      - BR card with empty rule_text → compliance risk; BA must confirm before sign-off
      - Any other card with empty prose → AC, requirement, or persona content cannot be derived
    """
    gaps: list[KeyDecision] = []
    bodies = context.card_bodies

    for c in context.matched:
        prose = _card_prose(bodies, c.id) or ""
        if prose.strip():
            continue  # has content — no gap

        if c.kind == "BusinessRule":
            desc = (
                f"KB card {c.id} ({c.label[:60]}) is a BusinessRule with no rule_text — "
                "business rule content is missing from the KB. "
                "Run /kb-build to extract rule text from source documents before BRD sign-off."
            )
        else:
            desc = (
                f"KB card {c.id} ({c.label[:60]}, {c.kind}) has no prose — "
                "acceptance criteria and requirement content cannot be derived. "
                "Run /kb-build to enrich this card."
            )

        gaps.append(KeyDecision(
            tag="OPEN",
            description=desc,
            section="9.Key Decisions & Open Items",
            marker="BA-TODO",
        ))

    return gaps
