"""Reasoned Architecture section builders — apply LLM enrichment onto deterministic shells.

All builders return a safe deterministic fallback when ``enriched`` is None (model=None / test mode).
Soft word cap for system_context (300w) uses the same Import 1 pattern as BRD.
Cyclomatic ≤ 21 per function.

Cross-repo:
  Genlite — IMAD business-impact framing for system_context fallback
  BRD     — _soft_cap_prose / _soft_cap_list reused verbatim
  S3      — coverage gate stubs fed from check_arch_coverage
"""

from __future__ import annotations

from typing import Any

from app.lifecycle.stages.architecture.match.context import ArchitectureContext
from app.lifecycle.stages.architecture.schema import (
    IntegrationPoint,
    NFRItem,
    SequenceDiagram,
    SystemComponent,
)
from app.lifecycle.stages.fsd.schema import NFR, Stub

# ── Soft word-cap helpers (Import 1 — identical to BRD reasoned.py) ──────────

_TRUNCATION_SUFFIX = " [...] See Open Items for remaining detail."


def _soft_cap_prose(text: str, max_words: int) -> tuple[str, bool]:
    """Trim ``text`` to ``max_words`` at the last sentence boundary."""
    if not text:
        return text, False
    words = text.split()
    if len(words) <= max_words:
        return text, False
    window = words[:max_words]
    trimmed = " ".join(window)
    last_period = max(trimmed.rfind("."), trimmed.rfind("!"), trimmed.rfind("?"))
    if last_period > len(trimmed) // 2:
        trimmed = trimmed[: last_period + 1]
    return trimmed + _TRUNCATION_SUFFIX, True


# ── Reasoned section builders ─────────────────────────────────────────────────

def build_system_context(enriched: dict[str, Any] | None, ctx: ArchitectureContext) -> str:
    """Architecture executive narrative — LLM prose or IMAD-style deterministic fallback."""
    if enriched and enriched.get("system_context"):
        return str(enriched["system_context"])

    # Deterministic fallback (IMAD business-impact framing adapted for architecture)
    parts: list[str] = []
    if ctx.requirement:
        parts.append(f"Architecture analysis for: {ctx.requirement}.")

    if ctx.sys_matched:
        sys_names = ", ".join(c.label or c.id for c in ctx.sys_matched[:5])
        parts.append(f"Key systems in scope: {sys_names}.")

    int_total = len(ctx.int_matched) + len(ctx.api_matched)
    if int_total:
        parts.append(f"{int_total} integration point(s) identified.")

    if ctx.change_class:
        parts.append(f"Change classification: {ctx.change_class}.")

    if not parts:
        return "[ARCH-TODO: Architect to provide system context narrative]"

    return " ".join(parts)


def enrich_component_design(
    components: list[SystemComponent],
    enriched: dict[str, Any] | None,
    allowed_ids: set[str],
) -> list[SystemComponent]:
    """Apply LLM-proposed responsibility text onto deterministic SystemComponent rows.

    Whitelist-filtered: only updates components whose IDs are in allowed_ids.
    Enriched dict key: 'components' → list of {'id': ..., 'responsibility': ..., 'interfaces': [...]}
    """
    if not enriched or not enriched.get("components"):
        return components

    enrichment_by_id: dict[str, dict] = {}
    for item in enriched.get("components", []):
        if isinstance(item, dict) and item.get("id") in allowed_ids:
            enrichment_by_id[item["id"]] = item

    result: list[SystemComponent] = []
    for comp in components:
        patch = enrichment_by_id.get(comp.id)
        if not patch:
            result.append(comp)
            continue
        new_resp = patch.get("responsibility") or comp.responsibility
        new_ifaces = patch.get("interfaces") or comp.interfaces
        result.append(comp.model_copy(update={
            "responsibility": str(new_resp),
            "interfaces": [str(i) for i in new_ifaces if isinstance(i, str)],
            "source_type": "kb_inferred",
        }))
    return result


def enrich_integration_design(
    integrations: list[IntegrationPoint],
    enriched: dict[str, Any] | None,
    allowed_ids: set[str],
) -> list[IntegrationPoint]:
    """Apply LLM-proposed protocol / direction onto deterministic IntegrationPoint rows."""
    if not enriched or not enriched.get("integrations"):
        return integrations

    enrichment_by_id: dict[str, dict] = {}
    for item in enriched.get("integrations", []):
        if isinstance(item, dict) and item.get("id") in allowed_ids:
            enrichment_by_id[item["id"]] = item

    result: list[IntegrationPoint] = []
    for intg in integrations:
        patch = enrichment_by_id.get(intg.id)
        if not patch:
            result.append(intg)
            continue
        result.append(intg.model_copy(update={
            "protocol": patch.get("protocol") or intg.protocol,
            "direction": patch.get("direction") or intg.direction,
            "from_component": patch.get("from_component") or intg.from_component,
            "to_component": patch.get("to_component") or intg.to_component,
        }))
    return result


def build_toBe_diagram(
    enriched: dict[str, Any] | None,
    ctx: ArchitectureContext,
    asIs_diagram: str,
) -> str:
    """Return TO-BE Mermaid DSL — LLM diff from AS-IS; fallback = AS-IS with ARCH-TODO note."""
    if enriched and enriched.get("toBe_diagram"):
        return str(enriched["toBe_diagram"])
    # Deterministic fallback: the TO-BE baseline is the current architecture with an ARCH-TODO
    # annotation so the Architect knows to annotate the deltas. Empty when there's no AS-IS.
    if asIs_diagram:
        return asIs_diagram + "\n%% ARCH-TODO: Architect to annotate TO-BE changes from AS-IS"
    return ""


def enrich_sequence_diagrams(
    seq_diagrams: list[dict],
    enriched: dict[str, Any] | None,
    allowed_ids: set[str],
) -> list[SequenceDiagram]:
    """Convert pre-built stub dicts → SequenceDiagram objects; apply LLM DSL if provided."""
    enriched_seqs: dict[str, str] = {}
    if enriched and enriched.get("sequence_diagrams"):
        for item in enriched.get("sequence_diagrams", []):
            if isinstance(item, dict) and item.get("story_id") and item.get("mermaid_dsl"):
                enriched_seqs[item["story_id"]] = str(item["mermaid_dsl"])

    result: list[SequenceDiagram] = []
    for d in seq_diagrams:
        story_id = d.get("story_id", "")
        mermaid_dsl = enriched_seqs.get(story_id) or d.get("mermaid_dsl", "")
        source_type = "llm_reasoned" if story_id in enriched_seqs else "stub"
        result.append(SequenceDiagram(
            story_id=story_id,
            title=d.get("title", f"Sequence: {story_id}"),
            mermaid_dsl=mermaid_dsl,
            participants=d.get("participants", []),
            source_type=source_type,
        ))
    return result


def build_nfr_items(
    enriched: dict[str, Any] | None,
    ctx: ArchitectureContext,
) -> list[NFRItem]:
    """Build architecture NFR items from FSD NFRs + agent-proposed arch NFRs.

    Priority: FSD non_functional_reqs → LLM arch additions → stubs.
    """
    items: list[NFRItem] = []
    seen_cats: set[str] = set()

    # 1. Pass FSD NFRs through as confirmed architectural NFRs
    if ctx.fsd_doc and ctx.fsd_doc.non_functional_reqs:
        for nfr in ctx.fsd_doc.non_functional_reqs:
            items.append(NFRItem(
                id=nfr.id or f"nfr-fsd-{len(items)+1}",
                category=nfr.category,
                requirement=nfr.description,
                rationale=nfr.rationale,
                source_type="fsd_derived",
            ))
            seen_cats.add(nfr.category)

    # 2. LLM-proposed arch NFRs (only new categories not already covered by FSD)
    if enriched and enriched.get("non_functional_reqs"):
        for item in enriched.get("non_functional_reqs", []):
            if not isinstance(item, dict):
                continue
            cat = str(item.get("category", ""))
            if cat in seen_cats:
                continue
            seen_cats.add(cat)
            items.append(NFRItem(
                id=f"nfr-arch-{len(items)+1}",
                category=cat,
                requirement=str(item.get("requirement", "")),
                rationale=item.get("rationale"),
                metric=item.get("metric"),
                source_type="llm_reasoned",
            ))

    if not items:
        items.append(NFRItem(
            id="nfr-stub-1",
            category="Performance",
            requirement="[ARCH-TODO: Architect to define performance requirements]",
            source_type="stub",
        ))
    return items


def build_open_items(
    enriched: dict[str, Any] | None,
    ctx: ArchitectureContext,
    coverage_stubs: list[Stub] | None = None,
) -> list[Stub]:
    """Merge coverage gate stubs + agent-proposed ARCH-TODOs + inherited gaps."""
    stubs: list[Stub] = list(coverage_stubs or [])
    seen_ids: set[str] = {s.id for s in stubs}

    # Inherited gaps from prior stages (truncated to top 5)
    for i, gap in enumerate(ctx.gaps[:5]):
        stub_id = f"arch-gap-{i+1}"
        if stub_id not in seen_ids:
            stubs.append(Stub(
                id=stub_id,
                section="open_items",
                description=gap,
                marker="BA-TODO",
                priority="Medium",
            ))
            seen_ids.add(stub_id)

    # Agent-proposed open items
    if enriched and enriched.get("open_items"):
        for item in enriched.get("open_items", []):
            if not isinstance(item, dict):
                continue
            stub_id = item.get("id") or f"arch-agent-{len(stubs)+1}"
            if stub_id in seen_ids:
                continue
            seen_ids.add(stub_id)
            stubs.append(Stub(
                id=str(stub_id),
                section="open_items",
                description=str(item.get("description", "")),
                marker=str(item.get("marker", "BA-TODO")),
                priority=str(item.get("priority", "Medium")),
            ))
    return stubs
