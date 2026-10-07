"""Deterministic PROPOSED-content seeders for net-new / light changes (docs/29 — hybrid fill).

The extractive builders in ``deterministic.py`` emit rows only for KB cards the ANALYSIS stage
matched. For a net-new / enhancement change the KB matches little, so §3 Functional Requirements
and §10 Acceptance Criteria would render empty. These seeders fill that gap with **proposed**
entries derived deterministically from the accepted analysis (its as-is→to-be ``modifications`` and
``scope``) and the requirement text.

Moat-safe by construction:
  * every row is tagged ``source_type="proposed"`` so a reader — and the grounding gate — can tell
    a proposal from a KB-grounded fact;
  * no KB id / source-locus is fabricated (proposed ids use a ``PROP-`` prefix; AC rows carry an
    empty ``kb_id`` so they create no GROUNDS traceability link);
  * content is taken verbatim from the requirement / scope / modifications — no invented specifics,
    no LLM, and **no hardcoded domain terms** (LOB-neutral), so it is fully reproducible.
"""

from __future__ import annotations

from app.lifecycle.stages.fsd.match.context import FSDContext
from app.lifecycle.stages.fsd.schema import ACRow, FunctionalRequirement

# Change classes that describe work not yet grounded in the KB → a proposal is appropriate.
# "Existing" is excluded: the KB already covers it, so there is nothing to propose.
_NET_NEW_CLASSES = {"New", "Enhancement", "Derived"}


def needs_proposals(change_class: str) -> bool:
    """True when the change class describes not-yet-built work (so empty §3/§10 should be seeded)."""
    return (change_class or "").strip().title() in _NET_NEW_CLASSES


def _priority_for(change_class: str) -> str:
    return "High" if (change_class or "").strip().title() == "New" else "Medium"


def build_proposed_functional_requirements(context: FSDContext) -> list[FunctionalRequirement]:
    """Seed §3 with PROPOSED FRs when the extractive builder found none.

    Evidence priority (richest first): analysis ``modifications`` (before→after deltas) → ``scope``
    new/enhancement areas → the requirement itself (always yields at least one FR, since the change
    request IS a functional need). Every row is ``source_type="proposed"``.
    """
    prio = _priority_for(context.change_class)
    out: list[FunctionalRequirement] = []
    seen: set[str] = set()

    def _add(title: str, as_is: str | None, to_be: str, source_locus: str | None = None) -> None:
        key = (title or "").strip().lower()
        if not key or key in seen:
            return
        seen.add(key)
        out.append(FunctionalRequirement(
            id=f"PROP-FR-{len(out) + 1:03d}",
            title=title.strip(),
            as_is=(as_is or None),
            to_be=to_be,
            # source_locus carries the KB card ID the FR derives from (SCR-*, ENT-*, etc.).
            # graph_service.py leg 3.5 extracts this to resolve the owning system via KB graph,
            # avoiding the all-pairs fallback that connects every system to every PROP rule.
            source_locus=source_locus,
            source_type="proposed",
            priority=prio,
            stub_marker=None,  # a proposal is not a TODO — it does not inflate stub_count
        ))

    # 1. From analysis modifications (typical for Enhancement) — each has an as-is → to-be delta.
    #    m.id is the KB card being modified → use it as the source locus so the trace graph can
    #    resolve which system owns this FR (e.g. SCR-JAUTO-AU-RN-013 → Angular UI).
    for m in context.modifications:
        _add(m.label, m.before, m.after or f"Change {m.label} to satisfy: {context.requirement}",
             source_locus=m.id)

    # 2. From scope (new + enhancement areas the analysis identified).
    #    it.id is a KB card ID (SCR-*, ENT-*, SYS-*, etc.) — use it as the source locus.
    for it in list(context.scope.new) + list(context.scope.enhancement):
        _add(it.label, None, it.note or f"Provide {it.label} to satisfy: {context.requirement}",
             source_locus=it.id)

    # 3. Always at least one FR straight from the requirement.
    if not out:
        _add(
            "Requirement capability",
            None,
            f"Provide the capability described in the requirement: {context.requirement}",
        )
    return out


def build_proposed_acceptance_criteria(frs: list[FunctionalRequirement]) -> list[ACRow]:
    """One AC-3 row per PROPOSED FR, with an empty ``kb_id`` (creates no GROUNDS link)."""
    return [
        ACRow(
            fsd_section="3.Functional Requirements",
            kb_id="",
            kb_label=fr.title,
            source_locus=None,
            link_type="IMPLEMENTS",
        )
        for fr in frs if fr.source_type == "proposed"
    ]
