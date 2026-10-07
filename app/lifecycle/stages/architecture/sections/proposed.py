"""Deterministic PROPOSED SRD content for net-new / light changes (docs/29 hybrid).

When the extractive builders find no existing component cards (a net-new change matches little
existing architecture), seed clearly-marked ``proposed`` components derived from the requirement +
the FSD functional requirements — so §4 Component Design is not blank. Marked
``source_type="proposed"``; no fabricated KB ids/loci (``PROP-CMP-*``); LOB-neutral (content comes
from the requirement/FSD); no LLM; reproducible. Integrations / data-model deliberately stay honest
empty-states rather than fabricate connections that a field-add may not have.
"""

from __future__ import annotations

from app.lifecycle.stages.architecture.match.context import ArchitectureContext
from app.lifecycle.stages.architecture.schema import SystemComponent

# Change classes that describe not-yet-built work → a proposal is appropriate. "Existing" excluded.
_NET_NEW_CLASSES = {"New", "Enhancement", "Derived"}


def needs_proposals(change_class: str) -> bool:
    return (change_class or "").strip().title() in _NET_NEW_CLASSES


def build_proposed_components(ctx: ArchitectureContext) -> list[SystemComponent]:
    """Seed §4 with PROPOSED components when the extractive builder found none.

    A UI-tier and a service-tier component are proposed for the change (the two layers any
    screen/field change touches), described from the requirement's own wording — clearly marked
    ``proposed`` so a reader (and the grounding gate) can tell a proposal from a grounded card.
    """
    frs = list(ctx.fsd_doc.functional_requirements) if ctx.fsd_doc else []
    focus = (frs[0].title if frs else "") or (ctx.requirement or "the requested change").strip()
    focus = focus[:80]
    return [
        SystemComponent(
            id="PROP-CMP-001",
            label=f"UI component — {focus}",
            kind="Component",
            responsibility="Front-end change to capture and display the new element on the affected screen.",
            source_type="proposed",
        ),
        SystemComponent(
            id="PROP-CMP-002",
            label=f"Service component — {focus}",
            kind="Component",
            responsibility="Back-end change to persist the new element and carry it through the flow to downstream consumers.",
            source_type="proposed",
        ),
    ]
