"""
``/re/graph`` — RE Console Knowledge-Graph Explorer API (docs/18 §Graph).

Read-only, deterministic. Backed by ``GraphProvider`` (NetworkX over the ACTIVE version's
``kb_nodes`` / ``kb_edges``, with a dev fixture fallback). Endpoints:

- ``GET /re/graph/overview``      seed view (nodes + edges + layer bands + kind legend)
- ``GET /re/graph/seeds``         lexical node search (find_seeds)
- ``GET /re/graph/neighbors``     N-hop neighbourhood of a node (drill / expand)
- ``GET /re/graph/impact/{id}``   downstream/upstream impact walk
- ``GET /re/graph/node/{id}``     3-tier node drill (card body + evidence + adjacency)

Auth: mounted behind the standard middleware chain (Okta JWT + entitlement + API-key). No
write paths — RE owns graph writes (docs/09 §8). Response schemas come from the return-type
annotations; DI + query params use ``Annotated`` (FastAPI best practice).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.config import get_settings
from app.dao import graph_dao
from app.dao.postgres import get_pool
from app.models.graph import CodeAnalysis, GraphDiff, GraphDiffNode, GraphView, NodeDetail, SeedResult, Subgraph
from app.services import code_analysis
from app.services.graph_provider import GraphProvider, get_graph_provider

router = APIRouter(prefix="/re/graph", tags=["re-graph"])
_settings = get_settings()

# Reusable Annotated aliases (DI + shared query params).
ProviderDep = Annotated[GraphProvider, Depends(get_graph_provider)]
CategoryQuery = Annotated[str | None, Query(description="japan-auto-au | japan-auto-auw (omit = all)")]


@router.get("/overview", summary="Seed graph for the explorer")
async def overview(
    provider: ProviderDep,
    category: CategoryQuery = None,
    limit: Annotated[int, Query(ge=1, le=5000, description="Max nodes returned")] = _settings.GRAPH_MAX_NODES,
) -> GraphView:
    return await provider.overview(category=category, limit=limit)


@router.get("/seeds", summary="Lexical node search (find_seeds)")
async def seeds(
    provider: ProviderDep,
    q: Annotated[str, Query(min_length=1, description="Free-text query")],
    category: CategoryQuery = None,
) -> SeedResult:
    return await provider.seeds(query=q, category=category)


@router.get("/neighbors", summary="N-hop neighbourhood (drill / expand)")
async def neighbors(
    provider: ProviderDep,
    id: Annotated[str, Query(description="Node id, e.g. SYS-JAUTO-002")],
    direction: Annotated[str, Query(pattern="^(in|out|both)$")] = "both",
    depth: Annotated[int, Query(ge=1, le=6)] = 1,
) -> Subgraph:
    return await provider.neighbors(node_id=id, direction=direction, depth=depth)


@router.get("/impact/{node_id}", summary="Impact walk (what breaks if this changes?)")
async def impact(
    node_id: str,
    provider: ProviderDep,
    direction: Annotated[str, Query(pattern="^(downstream|upstream)$")] = "downstream",
) -> Subgraph:
    return await provider.impact(node_id=node_id, direction=direction)


@router.get("/node/{node_id}", summary="3-tier node drill (card + evidence)")
async def node_detail(node_id: str, provider: ProviderDep) -> NodeDetail:
    detail = await provider.node_detail(node_id=node_id)
    if detail is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Node not found: {node_id}")
    return detail


@router.get("/analysis", summary="L3 code analysis — modernization priority + dead-code (live, docs/26)")
async def code_analysis_report(
    top: Annotated[int, Query(ge=1, le=200, description="top-N modernization targets")] = 40,
) -> CodeAnalysis:
    """Derived code metrics (coupling, reachability) computed on the ACTIVE graph. A REPORT surface —
    separate from the cite-or-abstain chatbot, which correctly abstains on computed metrics."""
    return await code_analysis.analyze(top=top)


@router.get("/diff", summary="Version diff (added / modified / removed nodes)")
async def graph_diff(
    base: Annotated[str, Query(description="baseline kb_version (e.g. the as-is build)")],
    target: Annotated[str, Query(description="target kb_version (e.g. the FE-synced build)")],
) -> GraphDiff:
    """docs/25 Phase 3 #12 — diff two builds by node id-set + content signature.

    ``added`` = ids only in target; ``removed`` = ids only in base; ``modified`` = same id, different
    signature (enhancement UPSERT in place). Read-only; no promote-path impact."""
    pool = get_pool()
    base_sig = await graph_dao.fetch_node_signatures(pool, base)
    target_sig = await graph_dao.fetch_node_signatures(pool, target)
    if not base_sig and not target_sig:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no nodes for base={base!r} or target={target!r}")

    def _node(nid: str, sig_map: dict, change: str) -> GraphDiffNode:
        m = sig_map[nid]
        return GraphDiffNode(id=nid, kind=m["kind"], label=m["label"], change=change)

    added = [_node(i, target_sig, "added") for i in sorted(target_sig) if i not in base_sig]
    removed = [_node(i, base_sig, "removed") for i in sorted(base_sig) if i not in target_sig]
    modified = [
        _node(i, target_sig, "modified")
        for i in sorted(target_sig)
        if i in base_sig and target_sig[i]["sig"] != base_sig[i]["sig"]
    ]
    return GraphDiff(
        base=base, target=target, added=added, removed=removed, modified=modified,
        summary={"added": len(added), "removed": len(removed), "modified": len(modified)},
    )
