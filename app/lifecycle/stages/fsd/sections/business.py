"""Business view of the FSD — SAME sections as the technical FSD, plain-language content (no ids).

Human-facing only: turns the technical ``FSDDocument`` into a ``BusinessFsdView`` (KB ids, source
loci, edge labels, code, and IT jargon stripped/relabelled; insurance terms kept) for the FSD .docx
and the UI. The stored ``FSDDocument`` stays technical so downstream BRD/Stories + AC-3 traceability
keep their KB-id grounding. Reuses the shared transforms in ``analysis.sections.business``.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.lifecycle.stages.analysis.sections.business import (
    BizRow,
    BizScope,
    business_scrub,
    clean_name,
    kind_term,
)
from app.lifecycle.stages.fsd.schema import FSDDocument


class BusinessFsdView(BaseModel):
    """Plain-language mirror of FSDDocument — same 12 sections, no ids / loci / edges / IT jargon."""

    requirement: str | None = None
    change_class: str = ""
    context_summary: str = ""
    business_case: str = ""                                       # merged BRD: why this change matters
    persona_needs: list[BizRow] = Field(default_factory=list)     # merged BRD: persona → need
    key_decisions: list[BizRow] = Field(default_factory=list)     # merged BRD: decisions & open items
    scope: BizScope = Field(default_factory=BizScope)
    functional_requirements: list[BizRow] = Field(default_factory=list)
    process_flows: list[BizRow] = Field(default_factory=list)
    screen_specs: list[BizRow] = Field(default_factory=list)
    business_rules: list[BizRow] = Field(default_factory=list)
    data_model: list[BizRow] = Field(default_factory=list)
    integration_points: list[BizRow] = Field(default_factory=list)
    non_functional_reqs: list[BizRow] = Field(default_factory=list)
    acceptance_criteria: list[BizRow] = Field(default_factory=list)
    entry_criteria: list[BizRow] = Field(default_factory=list)  # PRD (docs/24 §E)
    exit_criteria: list[BizRow] = Field(default_factory=list)    # PRD (docs/24 §E)
    open_items: list[str] = Field(default_factory=list)
    references: list[str] = Field(default_factory=list)
    generated_at: str | None = None


def _row(name: str, kind: str, detail: str | None) -> BizRow:
    """A row that drops the detail when it just repeats the name (e.g. screen cards)."""
    if detail and detail.strip().lower() == (name or "").strip().lower():
        detail = None
    return BizRow(name=name, kind=kind, detail=detail)


def _scope_rows(items: list) -> list[BizRow]:
    # Scope is a categorised list of affected areas — names only (the technical note carries edges).
    return [_row(clean_name(it.label, it.kind or ""), kind_term(it.kind), None) for it in items]


def build_business_fsd(fsd: FSDDocument) -> BusinessFsdView:  # noqa: C901 — flat per-section mapping
    """Derive the plain-language, id-free business FSD view from the technical FSDDocument."""
    # 3. Functional requirements — as-is → to-be, scrubbed.
    frs: list[BizRow] = []
    for fr in fsd.functional_requirements:
        as_is = business_scrub(fr.as_is)
        to_be = business_scrub(fr.to_be) or (None if fr.to_be else "To be defined during design")
        parts = []
        if as_is:
            parts.append(f"As-is: {as_is}")
        if to_be:
            parts.append(f"To-be: {to_be}")
        detail = "  ".join(parts) or None
        # A proposed FR (net-new / light change) is a suggestion, not a KB-grounded fact — label it.
        if fr.source_type == "proposed":
            detail = f"Proposed — {detail}" if detail else "Proposed (to be confirmed during design)"
        frs.append(_row(clean_name(fr.title, "FR"), "requirement", detail))

    # 4. Process flows.
    procs = [
        _row(clean_name(p.title, p.kind), kind_term(p.kind), business_scrub(" ".join(p.steps)) if p.steps else None)
        for p in fsd.process_flows
    ]

    # 5. Screen specs — screen name lives in the purpose when the title is an id.
    screens: list[BizRow] = []
    for s in fsd.screen_specs:
        name = clean_name(s.title, "SCR")
        purpose = business_scrub(s.purpose)
        if name == kind_term("SCR").title() and purpose:  # title was an id → use the extracted name
            name, purpose = purpose, None
        screens.append(_row(name, "screen", purpose))

    # 6. Business rules.
    rules = [_row(clean_name(r.title or r.id, "BR"), "business rule", business_scrub(r.rule_text)) for r in fsd.business_rules]

    # 7. Data model.
    data = [_row(clean_name(d.label, "ENT"), kind_term(d.kind), business_scrub(d.note) or d.impact) for d in fsd.data_model]

    # 8. Integration points — pass kind="" so jargon is relabelled (PEGA/ESB/SOAP → plain terms).
    integ = [_row(clean_name(i.label, ""), kind_term(i.kind), business_scrub(i.note)) for i in fsd.integration_points]

    # 9. NFRs (category + plain description).
    nfrs = [_row(n.category or "Requirement", "", business_scrub(n.description)) for n in fsd.non_functional_reqs]

    # 10. Acceptance criteria — one business-worded line per grounded item (id/link traceability stays in the artifact).
    acs: list[BizRow] = []
    seen_ac: set[str] = set()
    for r in fsd.acceptance_criteria:
        nm = clean_name(r.kb_label, "")
        if nm and nm.lower() not in seen_ac:
            seen_ac.add(nm.lower())
            acs.append(_row(nm, "", "Delivered and verified for this requirement."))

    # PRD (docs/24 §E) — entry/exit criteria as business-worded rows with a met ✓/○ marker.
    entry_criteria = [
        _row(("✓ " if c.met else "○ ") + business_scrub(c.text), "", None)
        for c in (fsd.entry_criteria or []) if c.text
    ]
    exit_criteria = [
        _row(("✓ " if c.met else "○ ") + business_scrub(c.text), "", None)
        for c in (fsd.exit_criteria or []) if c.text
    ]

    # 11. Open items.
    open_items = [o for o in (business_scrub(s.description) for s in fsd.open_items) if o]

    # 12. References — business names, no ids/loci.
    refs: list[str] = []
    seen_ref: set[str] = set()
    for r in fsd.references:
        nm = clean_name(r.label, r.kind)
        if nm and nm.lower() not in seen_ref:
            seen_ref.add(nm.lower())
            refs.append(nm)

    context_summary = business_scrub(fsd.context_summary) or (
        f"This {fsd.change_class or 'change'} affects {len(frs)} requirement area(s) across the business."
    )

    # Merged-BRD sections — business framing folded into the FSD view.
    business_case = business_scrub(fsd.business_case) or ""
    persona_needs = [
        _row(p.persona, "persona", business_scrub(p.need) or business_scrub(p.use_case))
        for p in fsd.persona_needs if p.persona
    ]
    key_decisions = [
        _row(kd.tag or "OPEN", "", business_scrub(kd.description))
        for kd in fsd.key_decisions if kd.description
    ]

    return BusinessFsdView(
        requirement=business_scrub(fsd.requirement),
        change_class=fsd.change_class,
        context_summary=context_summary,
        business_case=business_case,
        persona_needs=persona_needs,
        key_decisions=key_decisions,
        scope=BizScope(
            new=_scope_rows(fsd.scope.new),
            enhancement=_scope_rows(fsd.scope.enhancement),
            existing=_scope_rows(fsd.scope.existing),
        ),
        functional_requirements=frs,
        process_flows=procs,
        screen_specs=screens,
        business_rules=rules,
        data_model=data,
        integration_points=integ,
        non_functional_reqs=nfrs,
        acceptance_criteria=acs,
        entry_criteria=entry_criteria,
        exit_criteria=exit_criteria,
        open_items=open_items,
        references=refs,
        generated_at=fsd.generated_at,
    )
