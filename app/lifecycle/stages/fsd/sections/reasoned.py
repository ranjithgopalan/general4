"""LLM-derived (reasoned) FSD sections — each whitelist-filtered to real ids; deterministic fallbacks.

The ReAct agent proposes context_summary, to_be fields for FRs, NFRs, and open_items.
These builders apply the agent's enrichment onto the deterministic base and strip any id
references outside the allowed whitelist (grounding gate). Deterministic fallbacks ensure
the FSD is always complete even when the agent is absent or returns malformed output.
"""

from __future__ import annotations

import re as _re
from typing import Any

from app.lifecycle.stages.fsd.match.context import FSDContext
from app.lifecycle.stages.fsd.schema import (
    NFR,
    NFR_CATEGORIES,
    PRIORITY_LEVELS,
    SOURCE_TYPES,
    FunctionalRequirement,
    Stub,
)


def _s(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _priority(raw: Any) -> str:
    p = str(raw or "").strip().capitalize()
    return p if p in PRIORITY_LEVELS else "Medium"


def _source_type(raw: Any) -> str:
    t = str(raw or "").strip()
    return t if t in SOURCE_TYPES else "stub"


def _category(raw: Any) -> str:
    c = str(raw or "").strip()
    # Accept any reasonable variant, fall back to first category
    for cat in NFR_CATEGORIES:
        if c.lower() == cat.lower():
            return cat
    return NFR_CATEGORIES[0]


def build_context_summary(enriched: dict[str, Any] | None, context: FSDContext) -> str:
    """LLM: 3-6 sentence project background — fallback to deterministic summary."""
    if enriched and _s(enriched.get("context_summary")):
        return str(enriched["context_summary"]).strip()
    ids = ", ".join(f"[{c.id}]" for c in context.matched[:5]) or "no existing KB cards"
    return (
        f"This change affects the AIG Connect Japan Auto insurance system. "
        f"The requirement is: {context.requirement}. "
        f"The change is classified as {context.change_class} — {context.classification_rationale}. "
        f"The following KB cards are directly in scope: {ids}."
    )


def apply_fr_enrichment(
    frs: list[FunctionalRequirement],
    enriched: dict[str, Any] | None,
    allowed_ids: set[str],
) -> list[FunctionalRequirement]:
    """Apply to_be + priority updates from the agent onto deterministic FRs; gate on allowed_ids."""
    if not enriched or not isinstance(enriched.get("functional_requirements"), list):
        return frs

    updates: dict[str, dict[str, Any]] = {}
    for entry in enriched["functional_requirements"][:30]:
        if not isinstance(entry, dict):
            continue
        eid = str(entry.get("id") or "").strip()
        if not eid:
            continue
        # Only apply to_be for stubs (id starts with "stub-") or existing allowed ids
        if not eid.startswith("stub-") and eid not in allowed_ids:
            continue
        updates[eid] = {
            "to_be": _s(entry.get("to_be")),
            "priority": _priority(entry.get("priority")),
            "source_type": _source_type(entry.get("source_type")),
            "stub_marker": _s(entry.get("stub_marker")),
        }

    result: list[FunctionalRequirement] = []
    for fr in frs:
        if fr.id in updates:
            upd = updates.pop(fr.id)
            result.append(fr.model_copy(update={k: v for k, v in upd.items() if v is not None}))
        else:
            result.append(fr)

    # Append net-new stub entries from agent (stub-* ids not already in deterministic list)
    for stub_id, upd in updates.items():
        if stub_id.startswith("stub-"):
            result.append(FunctionalRequirement(
                id=stub_id,
                title=upd.get("to_be") or stub_id,
                as_is=None,
                to_be=None,
                source_type="stub",
                priority=upd.get("priority") or "Medium",
                stub_marker="BA-TODO",
            ))
    return result


def build_nfrs(enriched: dict[str, Any] | None) -> list[NFR]:
    """LLM: non-functional requirements — max 6; deterministic fallback = empty list."""
    out: list[NFR] = []
    for i, entry in enumerate(((enriched or {}).get("non_functional_reqs") or [])[:6]):
        if not isinstance(entry, dict):
            continue
        desc = _s(entry.get("description"))
        if not desc:
            continue
        out.append(NFR(
            id=entry.get("id") or f"NFR-{i + 1:03d}",
            category=_category(entry.get("category")),
            description=desc,
            rationale=_s(entry.get("rationale")),
            stub_marker=_s(entry.get("stub_marker")),
        ))
    return out


def build_fsd_open_items(enriched: dict[str, Any] | None, gap_stubs: list[Stub]) -> list[Stub]:
    """Merge agent-proposed open_items with analysis-gap stubs; dedup by description prefix."""
    seen_desc: set[str] = {s.description[:40] for s in gap_stubs}
    extra: list[Stub] = []
    for i, entry in enumerate(((enriched or {}).get("open_items") or [])[:15]):
        if not isinstance(entry, dict):
            continue
        desc = _s(entry.get("description"))
        if not desc or desc[:40] in seen_desc:
            continue
        seen_desc.add(desc[:40])
        marker = str(entry.get("marker") or "BA-TODO").strip()
        if marker not in ("BA-TODO", "DEV-TODO"):
            marker = "BA-TODO"
        extra.append(Stub(
            id=entry.get("id") or f"stub-{i + 1}",
            section=_s(entry.get("section")) or "11.Open Items",
            description=desc,
            marker=marker,
            priority=_priority(entry.get("priority")),
        ))
    return gap_stubs + extra


_NUM_PAT = _re.compile(r"\b\d+(?:\.\d+)?%?\b")


def validate_fr_facts(
    frs: list[FunctionalRequirement],
    card_bodies: dict[str, dict[str, Any]],
) -> list[Stub]:
    """Import 2 (IMAD soft-validation): for each LLM-written ``to_be``, verify that any
    numbers / percentages it contains appear in the source card prose.

    Numbers invented by the LLM that cannot be found in the card body are flagged as
    DEV-TODO stubs so the BA can confirm them against the source document.
    Returns an empty list when card_bodies is empty (fixture/dev mode).
    """
    stubs: list[Stub] = []
    for fr in frs:
        # Skip stubs and proposed rows — a proposal is not grounded in a source card, so its
        # numbers cannot (and should not) be re-anchored against KB prose.
        if not fr.to_be or fr.source_type in ("stub", "proposed") or fr.stub_marker:
            continue
        card = card_bodies.get(fr.id, {})
        prose = str(card.get("prose") or card.get("text_en") or "")
        if not prose:
            continue  # no source prose available — skip; can't verify
        nums_in_to_be = set(_NUM_PAT.findall(fr.to_be))
        nums_in_prose = set(_NUM_PAT.findall(prose))
        invented = nums_in_to_be - nums_in_prose
        if invented:
            stubs.append(Stub(
                id=f"dev-check-{fr.id}",
                section="3.Functional Requirements",
                description=(
                    f"FR {fr.id}: to_be contains {sorted(invented)} "
                    "not found in KB source prose — verify these values against the source document"
                ),
                marker="DEV-TODO",
                priority="High",
            ))
    return stubs


def fallback_context_summary(context: FSDContext) -> str:
    """Used by the grounding gate if the LLM summary cites blocked ids."""
    ids = ", ".join(f"[{c.id}]" for c in context.matched[:5]) or "no KB cards"
    return (
        f"This {context.change_class} change addresses: {context.requirement}. "
        f"KB cards in scope: {ids}."
    )
