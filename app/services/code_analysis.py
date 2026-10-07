"""Serve-time code analysis (docs/26 L3) — modernization priority + dead-code, computed on demand.

Reuses the L3 logic (reachability + coupling) over the LIVE ACTIVE graph (`kb_nodes`/`kb_edges`), so the
`/re/analysis` surface is always current with the served KB — no rebuild, no report file. This is a
REPORT endpoint (derived metrics), deliberately separate from the cite-or-abstain chatbot: the chatbot
grounds facts-from-source and correctly abstains on computed metrics; analysis lives here instead.
"""

from __future__ import annotations

import collections

from app.config import get_settings
from app.dao import graph_dao
from app.dao.postgres import get_pool
from app.models.graph import CodeAnalysis, CodeAnalysisItem

_CODE_PREFIX = ("CMP-JAUTO-", "API-JAUTO-")
_REACH = {"CALLS", "IMPLEMENTS", "DEPENDS_ON"}
_DEPRECATED = ("PEGA", "MAINFRAME", "FUJITSU", "WOD", "AUTOWOD", "FREIA", "HULFT")


async def analyze(top: int = 40) -> CodeAnalysis:
    """Compute modernization ranking + dead-code candidates from the ACTIVE graph."""
    pool = get_pool()
    version = await graph_dao.active_version(pool, get_settings().GEAR_ID)
    if not version:
        return CodeAnalysis(kb_version="", code_nodes=0, entrypoints=0, reachable=0,
                            dead_code=[], modernization=[])
    nodes = await graph_dao.fetch_nodes(pool, version)
    edges = await graph_dao.fetch_edges(pool, version)
    node_by_id = {n["id"]: n for n in nodes}
    code_ids = {n["id"] for n in nodes if (n.get("id") or "").startswith(_CODE_PREFIX)}

    in_deg, out_deg, out_adj, dep_dep = _degrees(edges, code_ids, node_by_id)
    entry = {i for i in code_ids if (node_by_id[i].get("metadata") or {}).get("entrypoint_kind")}
    reachable = _bfs(entry, out_adj, code_ids)
    dead = sorted(i for i in code_ids if in_deg[i] == 0 and out_deg[i] == 0 and i not in entry)

    def score(i: str) -> float:
        return round((in_deg[i] + out_deg[i]) * (1.5 if i in dep_dep else 1.0), 2)

    ranked = sorted(code_ids, key=lambda i: -score(i))[:top]
    return CodeAnalysis(
        kb_version=version, code_nodes=len(code_ids), entrypoints=len(entry), reachable=len(reachable),
        dead_code=[_item(node_by_id[i], in_deg, out_deg, score, dep_dep) for i in dead[:100]],
        modernization=[_item(node_by_id[i], in_deg, out_deg, score, dep_dep) for i in ranked],
    )


def _degrees(edges: list, code_ids: set[str], node_by_id: dict) -> tuple:
    """(in_deg, out_deg, out_adj over reach-edges, {code nodes depending on a deprecated system})."""
    in_deg: collections.Counter = collections.Counter()
    out_deg: collections.Counter = collections.Counter()
    out_adj: dict[str, set[str]] = collections.defaultdict(set)
    dep_dep: set[str] = set()
    for e in edges:
        a, b, lbl = e.get("from_id"), e.get("to_id"), e.get("label")
        if a in code_ids:
            out_deg[a] += 1
            if lbl in _REACH and b in code_ids:
                out_adj[a].add(b)
            tgt = (node_by_id.get(b, {}).get("label") or "").upper()
            if any(d in tgt or d in (b or "") for d in _DEPRECATED):
                dep_dep.add(a)
        if b in code_ids:
            in_deg[b] += 1
    return in_deg, out_deg, out_adj, dep_dep


def _bfs(entry: set[str], out_adj: dict[str, set[str]], code_ids: set[str]) -> set[str]:
    seen = set(entry)
    frontier = list(entry)
    while frontier:
        nxt: list[str] = []
        for nid in frontier:
            for t in out_adj.get(nid, ()):
                if t in code_ids and t not in seen:
                    seen.add(t)
                    nxt.append(t)
        frontier = nxt
    return seen


def _item(n: dict, in_deg, out_deg, score, dep_dep) -> CodeAnalysisItem:
    nid = n["id"]
    return CodeAnalysisItem(
        id=nid, label=n.get("label") or nid, kind=n.get("kind") or "",
        fan_in=in_deg[nid], fan_out=out_deg[nid], score=score(nid),
        deprecated_dep=nid in dep_dep, source_locus=n.get("source_locus"),
    )
