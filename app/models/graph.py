"""
Pydantic DTOs for the ``/re/graph`` knowledge-graph explorer contract (docs/18 §Graph).

The graph is a THIN INDEX: nodes/edges carry display fields + the ``source_locus`` join key.
The 3-tier drill fetches the rest lazily — ``NodeDetail`` adds the card body (tier 2) and the
verbatim evidence + re_anchor verdict (tier 3). ``layer`` is DERIVED from the id family at query
time (docs/09 §3.4) — never stored.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class GraphNode(BaseModel):
    """A node as the explorer renders it (tier-1 index — full body lives in ``kb_cards``)."""

    id: str
    label: str
    kind: str = Field(
        description="ID family code: ENT|REL|PROC|WF|SEQ|STM|SCR|BR|FR|SYS|INT|ROLE|TERM|DOM|ALIAS|EXT|API|CMP"
    )
    kind_label: str = Field(default="", description="Display expansion, e.g. 'Entity'")
    layer: str = Field(
        default="", description="Derived swimlane band key (intent|governance|behavior|surface|systems|code)"
    )
    category: str | None = Field(default=None, description="japan-auto-au | japan-auto-auw")
    confidence: float | None = None
    source_locus: str | None = Field(
        default=None, description="Drill join key: 'file §section char:start-end' or S3 URI"
    )
    summary: str | None = Field(default=None, description="Short display snippet (hover)")
    # Sync provenance (docs/25): reverse = as-is RE build; forward = synced from an FE workspace refresh.
    origin: str = Field(default="reverse", description="reverse (as-is RE) | forward (FE-synced)")
    workspace_id: str | None = Field(default=None, description="Source workspace id when origin=forward")
    synced_in: str | None = Field(default=None, description="kb_version this node was first synced in")


class GraphEdge(BaseModel):
    """A typed relationship. ``source``/``target`` are d3-native (mapped from kb_edges from_id/to_id)."""

    id: str | None = None
    source: str
    target: str
    label: str = Field(
        description="Edge type: TRIGGERS|CALLS|FEEDS_INTO|VALIDATES|GOVERNED_BY|ROLE_IN|SCREEN_OF|IMPLEMENTS|DEPENDS_ON|PRODUCES|OVERRIDES|REFERENCES"
    )
    tag: str | None = Field(default=None, description="stated (cited) | inferred (LLM-derived under grounding)")
    confidence: float | None = None
    origin: str = Field(default="reverse", description="reverse (as-is RE) | forward (FE-synced)")


class LayerBand(BaseModel):
    """One architecture swimlane band for the layered layout."""

    key: str
    title: str
    order: int
    kinds: list[str]
    split: bool = Field(default=True, description="Laned by category (AU left / AUW right)")
    shared: bool = Field(default=False, description="Full-width cross-cutting infrastructure band")


class KindStat(BaseModel):
    """Per-kind count for legend + filter chips."""

    kind: str
    kind_label: str
    count: int


class GraphView(BaseModel):
    """The seed/overview payload the explorer loads first."""

    kb_version: str
    category: str | None = None
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    layers: list[LayerBand]
    kinds: list[KindStat]
    truncated: bool = Field(default=False, description="True when node count exceeded the limit")
    source: str = Field(default="db", description="'db' (ACTIVE version) | 'fixture' (dev seed, no live KB)")


class Subgraph(BaseModel):
    """A neighbourhood / impact walk result."""

    nodes: list[GraphNode]
    edges: list[GraphEdge]
    root: str | None = None


class SeedHit(BaseModel):
    id: str
    label: str
    kind: str
    score: float


class SeedResult(BaseModel):
    query: str
    seeds: list[SeedHit]


class EvidenceItem(BaseModel):
    """Tier-3 drill: the verbatim source span + its re_anchor verdict."""

    source_locus: str
    snippet: str | None = None
    anchor_verdict: str = "PENDING"
    authority_tier: str | None = None


class NodeDetail(BaseModel):
    """Tier-2 + tier-3 drill: full card body, adjacency, and evidence for one node."""

    node: GraphNode
    prose: str | None = None
    text_en: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    incoming: list[GraphEdge] = Field(default_factory=list)
    outgoing: list[GraphEdge] = Field(default_factory=list)
    evidence: list[EvidenceItem] = Field(default_factory=list)


class CodeAnalysisItem(BaseModel):
    """One code node in the L3 report (docs/26) — coupling + modernization score, computed live."""

    id: str
    label: str = ""
    kind: str = ""
    fan_in: int = 0
    fan_out: int = 0
    score: float = 0.0
    deprecated_dep: bool = False
    source_locus: str | None = None


class CodeAnalysis(BaseModel):
    """Serve-time code analysis over the ACTIVE graph — modernization ranking + dead-code candidates."""

    kb_version: str
    code_nodes: int = 0
    entrypoints: int = 0
    reachable: int = 0
    modernization: list[CodeAnalysisItem] = Field(default_factory=list)
    dead_code: list[CodeAnalysisItem] = Field(default_factory=list)


class GraphDiffNode(BaseModel):
    """One node in a version diff (docs/25 Phase 3 #12)."""

    id: str
    kind: str = ""
    label: str = ""
    change: str = Field(description="added | removed | modified")


class GraphDiff(BaseModel):
    """Version diff between two KB builds — id-set + content-signature (docs/25 Phase 3 #12).

    ``modified`` = same id, different signature (enhancement UPSERT in place). New screens = new
    ids = ``added``; a dropped node = ``removed``. UI diff mode colours added/modified/removed."""

    base: str
    target: str
    added: list[GraphDiffNode] = Field(default_factory=list)
    removed: list[GraphDiffNode] = Field(default_factory=list)
    modified: list[GraphDiffNode] = Field(default_factory=list)
    summary: dict[str, int] = Field(default_factory=dict)
