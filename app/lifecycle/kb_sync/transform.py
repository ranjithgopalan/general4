"""Deterministic artifact → KB-delta transform (docs/23 §3).

FE artifacts are ALREADY grounded, KB-shaped objects (FR-*/BR-*/SCR-*/CMP-*/INT-* ids + source loci),
so the forward→reverse transform is a pure, deterministic mapping — no LLM, no re-extraction. Each
delta card/node carries ``workspace_id`` provenance so the change-log can trace it back.

Mapping (docs/23 §3):
    FSD.functional_requirements  -> FR-*  cards (+ node)
    FSD.business_rules           -> BR-*  cards (+ node, GOVERNED_BY edges to applies_to)
    FSD.screen_specs             -> SCR-* cards (+ node, GOVERNED_BY edges to cited BR-*)
    SRD.component_design         -> CMP-* cards (+ node)
    SRD.integration_design       -> INT-* cards (+ node, FEEDS_INTO from_component -> to_component)
Stories / Dev are traceability-only (not first-class cards) by default.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# KB id family -> graph node "kind" (Neo4j label). Mirrors the RE build's node kinds.
_KIND_FOR = {
    "FR": "FunctionalReq", "BR": "BusinessRule", "SCR": "Screen",
    "CMP": "Component", "INT": "Integration", "API": "ApiOp", "ENT": "Entity",
}


@dataclass
class KbDelta:
    """A KB delta produced from one workspace's artifacts (cards + graph nodes/edges)."""

    cards: list[dict] = field(default_factory=list)
    nodes: list[dict] = field(default_factory=list)
    edges: list[dict] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        return {"cards": len(self.cards), "nodes": len(self.nodes), "edges": len(self.edges)}


def _family(kb_id: str) -> str:
    """Leading id family token (FR/BR/SCR/CMP/INT/...) from a KB id like 'FR-JAUTO-001'."""
    return (kb_id or "").split("-", 1)[0].upper()


def _category(kb_id: str) -> str:
    return "japan-auto-auw" if "-AUW-" in (kb_id or "") or (kb_id or "").endswith("-AUW") else "japan-auto-au"


def _provenance(workspace_id: str, kb_version: str) -> dict:
    """Forward-engineering provenance stamp — carried in `metadata` so it survives the loader (kb_version
    alone can't distinguish synced from as-is content: a refresh re-stamps EVERY node to the new version)."""
    return {"origin": "forward", "workspace_id": workspace_id, "synced_in": kb_version}


def _card(kb_id: str, label: str, prose: str, locus: str | None, workspace_id: str, kb_version: str) -> dict:
    """One KB card in the loader's cards.jsonl shape, tagged with forward-sync provenance (in metadata)."""
    fam = _family(kb_id)
    return {
        "id": kb_id,
        "kind": _KIND_FOR.get(fam, "Entity"),
        "label": label or kb_id,
        "prose": prose or "",
        "source_locus": locus or f"workspace:{workspace_id}",
        "category": _category(kb_id),
        "confidence": 0.85,
        "metadata": _provenance(workspace_id, kb_version),
    }


def _node(kb_id: str, label: str, locus: str | None, workspace_id: str, kb_version: str) -> dict:
    fam = _family(kb_id)
    return {
        "id": kb_id, "label": label or kb_id, "kind": _KIND_FOR.get(fam, "Entity"),
        "source_locus": locus or "", "confidence": 0.85, "category": _category(kb_id),
        "metadata": _provenance(workspace_id, kb_version),
    }


def _edge(src: str, tgt: str, label: str, locus: str | None, workspace_id: str, kb_version: str) -> dict:
    return {"from": src, "to": tgt, "label": label, "source_locus": locus or "", "confidence": 0.85,
            "metadata": _provenance(workspace_id, kb_version)}


def _add(delta: KbDelta, kb_id: str, label: str, prose: str, locus: str | None,
         workspace_id: str, kb_version: str) -> None:
    if not kb_id:
        return
    delta.cards.append(_card(kb_id, label, prose, locus, workspace_id, kb_version))
    delta.nodes.append(_node(kb_id, label, locus, workspace_id, kb_version))


def _from_fsd(fsd: Any, delta: KbDelta, workspace_id: str, kb_version: str) -> None:
    for fr in getattr(fsd, "functional_requirements", []) or []:
        _add(delta, fr.id, fr.title, (fr.to_be or fr.as_is or ""), fr.source_locus, workspace_id, kb_version)
    for br in getattr(fsd, "business_rules", []) or []:
        _add(delta, br.id, br.title, getattr(br, "rule_text", ""), br.source_locus, workspace_id, kb_version)
        for tgt in getattr(br, "applies_to", []) or []:
            delta.edges.append(_edge(br.id, tgt, "GOVERNED_BY", br.source_locus, workspace_id, kb_version))
    for scr in getattr(fsd, "screen_specs", []) or []:
        _add(delta, scr.id, scr.title, getattr(scr, "purpose", ""), scr.source_locus, workspace_id, kb_version)
        for br_id in getattr(scr, "business_rules", []) or []:
            delta.edges.append(_edge(scr.id, br_id, "GOVERNED_BY", scr.source_locus, workspace_id, kb_version))


def _from_srd(srd: Any, delta: KbDelta, workspace_id: str, kb_version: str) -> None:
    for cmp_ in getattr(srd, "component_design", []) or []:
        _add(delta, cmp_.id, cmp_.label, getattr(cmp_, "responsibility", ""), cmp_.source_locus,
             workspace_id, kb_version)
    for intg in getattr(srd, "integration_design", []) or []:
        _add(delta, intg.id, intg.label, getattr(intg, "protocol", "") or "", intg.source_locus,
             workspace_id, kb_version)
        src, tgt = getattr(intg, "from_component", ""), getattr(intg, "to_component", "")
        if src and tgt:
            delta.edges.append(_edge(src, tgt, "FEEDS_INTO", intg.source_locus, workspace_id, kb_version))


def build_delta(*, workspace_id: str, kb_version: str, fsd: Any | None, srd: Any | None) -> KbDelta:
    """Transform a workspace's accepted FSD + SRD artifacts into a deterministic KB delta."""
    delta = KbDelta()
    if fsd is not None:
        _from_fsd(fsd, delta, workspace_id, kb_version)
    if srd is not None:
        _from_srd(srd, delta, workspace_id, kb_version)
    return delta
