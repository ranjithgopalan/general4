"""
GraphProvider — the knowledge-graph read/walk surface behind ``/re/graph``.

Hydrates the ACTIVE-version property graph (``kb_nodes`` / ``kb_edges``) into an in-memory
NetworkX ``MultiDiGraph`` ONCE (cached per kb_version) and answers overview / seeds / neighbours /
impact / node-detail from it. This is the v1 GraphProvider seam from docs/09 §2 — Neo4j swaps in
behind the same interface in v2 with no route change.

Fixture fallback: when no Postgres pool is up or there is no ACTIVE version yet (the common dev
state — no KB built), it loads ``app/fixtures/graph.sample.json`` so the explorer renders
end-to-end offline. The DTO contract is identical either way (``source`` = 'db' | 'fixture').

Graph lane improvements (all fixes from the implementation plan):
  * Inverted index built at hydration — O(token_hits) per query vs O(n) linear scan
  * Word-boundary-aware tokenizer — no substring false-positives ("peg" ≠ "pega")
  * Short-term allowlist — preserves domain acronyms ≤2 chars (AU, WF, ID …)
  * Configurable seed cap — GRAPH_SEED_CANDIDATES (default 40, aligned with lane candidates)
  * Fixed prefix bonus — fires per query-token vs per label-word, not on the full raw query
  * Prose excerpt fallback — first 200 chars of kb_cards.prose used when summary is absent
  * expand_seeds() — BFS neighbour expansion via structural edge labels with distance decay
"""

from __future__ import annotations

import asyncio
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import networkx as nx

from app.config.graph_layers import GRAPH_LAYERS, family_label, family_of, layer_of
from app.config.settings import get_settings
from app.dao import graph_dao
from app.models.graph import (
    EvidenceItem,
    GraphEdge,
    GraphNode,
    GraphView,
    KindStat,
    LayerBand,
    NodeDetail,
    SeedHit,
    SeedResult,
    Subgraph,
)
from app.utils.logging import log

_DEFAULT_FIXTURE_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "graph.sample.json"


# ── Graph lane helpers (module-level, stateless) ───────────────────────────────────────────────


def _short_terms() -> frozenset[str]:
    """Upper-cased short-term allowlist from settings (cached lazily by callers)."""
    raw = get_settings().GRAPH_SHORT_TERM_ALLOWLIST
    return frozenset(t.strip().upper() for t in raw.split(",") if t.strip())


def _tokenize(text: str, allowlist: frozenset[str]) -> list[str]:
    """Word-boundary tokenizer that preserves domain short terms.

    Splits on any non-word character (``\\W+``), then keeps tokens that are either
    ≥3 characters OR whose uppercase form is in *allowlist*.  This eliminates the
    substring false-positive problem of the original ``t in hay`` check.
    """
    result: list[str] = []
    for tok in re.split(r"\W+", text.lower()):
        if not tok:
            continue
        if len(tok) >= 3 or tok.upper() in allowlist:
            result.append(tok)
    return result


def _build_index(
    graph: nx.MultiDiGraph, allowlist: frozenset[str]
) -> dict[str, set[str]]:
    """Build an inverted index ``{token → set[node_id]}`` from the in-memory graph.

    The search surface per node is ``id + label + summary`` — identical to the original
    ``seeds()`` hay string.  Because we tokenize with ``_tokenize`` (word-boundary split),
    index lookup is inherently word-boundary-correct with no per-query regex needed.
    """
    index: dict[str, set[str]] = {}
    for nid, attrs in graph.nodes(data=True):
        surface = f"{nid} {attrs['label']} {attrs.get('summary') or ''}".lower()
        for tok in _tokenize(surface, allowlist):
            index.setdefault(tok, set()).add(nid)
    return index


def _pool_or_none():
    try:
        from app.dao.postgres import get_pool
        return get_pool()
    except Exception:
        return None


def _walk(
    graph: nx.MultiDiGraph, start: str, direction: str, depth: int
) -> tuple[set[str], set[tuple[str, str, Any]]]:
    """BFS from ``start`` following out/in/both edges up to ``depth`` hops."""
    seen: set[str] = {start}
    frontier: set[str] = {start}
    edge_keys: set[tuple[str, str, Any]] = set()
    for _ in range(max(1, depth)):
        nxt: set[str] = set()
        for node in frontier:
            if direction in ("out", "both"):
                for u, v, k in graph.out_edges(node, keys=True):
                    edge_keys.add((u, v, k))
                    if v not in seen:
                        seen.add(v)
                        nxt.add(v)
            if direction in ("in", "both"):
                for u, v, k in graph.in_edges(node, keys=True):
                    edge_keys.add((u, v, k))
                    if u not in seen:
                        seen.add(u)
                        nxt.add(u)
        frontier = nxt
        if not frontier:
            break
    return seen, edge_keys


class GraphProvider:
    """In-memory NetworkX graph provider with a fixture fallback (v1; Neo4j-swappable in v2)."""

    def __init__(self) -> None:
        self._cache: dict[str, nx.MultiDiGraph] = {}
        self._index_cache: dict[str, dict[str, set[str]]] = {}
        self._source: dict[str, str] = {}
        self._fixture_raw: dict | None = None
        self._lock = asyncio.Lock()
        self._last_known_version: str | None = None  # ← Track ACTIVE version

    # ── hydration ─────────────────────────────────────────────────────────────

    def _load_fixture(self) -> dict:
        if self._fixture_raw is None:
            configured = get_settings().GRAPH_FIXTURE_PATH
            path = Path(configured) if configured else _DEFAULT_FIXTURE_PATH
            self._fixture_raw = json.loads(path.read_text(encoding="utf-8"))
        return self._fixture_raw

    async def _resolve(self) -> tuple[str, bool]:
        """(kb_version, use_fixture) — ACTIVE version from the DB > fixture fallback."""
        pool = _pool_or_none()
        if pool is None:
            return "fixture", True

        try:
            version = await graph_dao.active_version(pool, get_settings().GEAR_ID)
            if version:
                return version, False
        except Exception as exc:
            raise RuntimeError(
                f"[graph] Failed to fetch ACTIVE KB version from database: {exc}\n"
                f"Ensure: 1) Database is running, 2) KB has been built with /kb-build, "
                f"3) kb_versions table has an ACTIVE row"
            ) from exc

        return "fixture", True

    @staticmethod
    def _norm_node(row: dict[str, Any]) -> dict[str, Any]:
        meta = row.get("metadata") or {}
        fam = family_of(row["id"], row.get("kind"))
        # Prefer metadata.summary; fall back to a prose excerpt when summary is absent
        summary = meta.get("summary") or row.get("prose_excerpt") or None
        return {
            "label": row.get("label") or row["id"],
            "kind": fam,
            "kind_label": family_label(fam),
            "layer": layer_of(fam),
            "category": row.get("category"),
            "confidence": row.get("confidence"),
            "source_locus": row.get("source_locus"),
            "summary": summary,
            "origin": meta.get("origin") or "reverse",
            "workspace_id": meta.get("workspace_id"),
            "synced_in": meta.get("synced_in"),
            "_meta": meta,
        }

    @staticmethod
    def _norm_edge(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "from_id": row.get("from_id") or row.get("from") or row.get("source"),
            "to_id": row.get("to_id") or row.get("to") or row.get("target"),
            "label": row.get("label") or row.get("predicate") or "REFERENCES",
            "tag": row.get("tag"),
            "confidence": row.get("confidence"),
            "origin": (row.get("metadata") or {}).get("origin") or "reverse",
            "id": row.get("id") or row.get("kb_edge_id"),
        }

    async def _graph(self) -> tuple[nx.MultiDiGraph, str, str]:
        """Hydrate (once, cached) the full graph + inverted index for the ACTIVE version."""
        version, use_fixture = await self._resolve()

        # ── Auto-invalidate cache if ACTIVE version changed in database ──────────────────
        if self._last_known_version is not None and version != self._last_known_version:
            log.info(
                f"[graph] ACTIVE KB version changed in database: "
                f"{self._last_known_version} → {version}. Invalidating cache."
            )
            self.invalidate()
        self._last_known_version = version
        # ──────────────────────────────────────────────────────────────────────────────────

        if version in self._cache:
            return self._cache[version], version, self._source[version]

        async with self._lock:
            if version in self._cache:
                return self._cache[version], version, self._source[version]

            if use_fixture:
                raw = self._load_fixture()
                node_rows = raw.get("nodes", [])
                edge_rows = raw.get("edges", []) or raw.get("links", [])
                source = "fixture"
            else:
                pool = _pool_or_none()
                # fetch_nodes_with_summary backfills a prose excerpt for the index
                node_rows = await graph_dao.fetch_nodes_with_summary(pool, version)
                edge_rows = await graph_dao.fetch_edges(pool, version)
                source = "db"

            graph = nx.MultiDiGraph()
            for row in node_rows:
                graph.add_node(row["id"], **self._norm_node(row))
            dropped = 0
            for i, row in enumerate(edge_rows):
                e = self._norm_edge(row)
                if e["from_id"] not in graph or e["to_id"] not in graph:
                    dropped += 1
                    continue
                graph.add_edge(
                    e["from_id"],
                    e["to_id"],
                    key=e["id"] or i,
                    label=e["label"],
                    tag=e["tag"],
                    confidence=e["confidence"],
                    id=e["id"],
                )

            # Build the inverted index alongside the graph
            allowlist = _short_terms()
            index = _build_index(graph, allowlist)

            log.info(
                f"[graph] hydrated version={version} source={source} "
                f"nodes={graph.number_of_nodes()} edges={graph.number_of_edges()} "
                f"dangling_dropped={dropped} index_tokens={len(index)}"
            )
            self._cache[version] = graph
            self._index_cache[version] = index
            self._source[version] = source
            return graph, version, source

    def invalidate(self) -> None:
        """Drop the hydration cache (call after a new version is promoted to ACTIVE)."""
        self._cache.clear()
        self._index_cache.clear()
        self._source.clear()

    # ── DTO helpers ───────────────────────────────────────────────────────────

    @staticmethod
    def _node_dto(graph: nx.MultiDiGraph, nid: str) -> GraphNode:
        a = graph.nodes[nid]
        return GraphNode(
            id=nid,
            label=a["label"],
            kind=a["kind"],
            kind_label=a["kind_label"],
            layer=a["layer"],
            category=a.get("category"),
            confidence=a.get("confidence"),
            source_locus=a.get("source_locus"),
            summary=a.get("summary"),
            origin=a.get("origin") or "reverse",
            workspace_id=a.get("workspace_id"),
            synced_in=a.get("synced_in"),
        )

    @staticmethod
    def _edge_dto(graph: nx.MultiDiGraph, u: str, v: str, k: Any) -> GraphEdge:
        d = graph.edges[u, v, k]
        return GraphEdge(
            id=d.get("id"), source=u, target=v, label=d["label"],
            tag=d.get("tag"), confidence=d.get("confidence"), origin=d.get("origin") or "reverse",
        )

    # ── public surface ────────────────────────────────────────────────────────

    async def overview(self, category: str | None = None, limit: int = 400) -> GraphView:
        graph, version, source = await self._graph()
        node_ids = [n for n, a in graph.nodes(data=True) if not category or a.get("category") == category]
        truncated = len(node_ids) > limit
        kept = set(node_ids[:limit])
        edges = [self._edge_dto(graph, u, v, k) for u, v, k in graph.edges(keys=True) if u in kept and v in kept]
        counts: dict[str, int] = {}
        for n in kept:
            fam = graph.nodes[n]["kind"]
            counts[fam] = counts.get(fam, 0) + 1
        kinds = [
            KindStat(kind=fam, kind_label=family_label(fam), count=c)
            for fam, c in sorted(counts.items(), key=lambda kv: -kv[1])
        ]
        return GraphView(
            kb_version=version, category=category,
            nodes=[self._node_dto(graph, n) for n in kept],
            edges=edges, layers=[LayerBand(**b) for b in GRAPH_LAYERS],
            kinds=kinds, truncated=truncated, source=source,
        )

    async def seeds(
        self,
        query: str,
        category: str | None = None,
        limit: int | None = None,
    ) -> SeedResult:
        """Return seed nodes for *query* using the inverted index.

        Improvements over the original implementation:
        - Index lookup replaces O(n) linear scan → O(token_hits)
        - Word-boundary tokenization eliminates substring false-positives
        - Short domain terms (AU, WF …) preserved by the allowlist
        - Configurable limit (defaults to GRAPH_SEED_CANDIDATES)
        - Prefix bonus fires per query-token vs per label-word (not on the raw query string)
        """
        graph, version, _ = await self._graph()
        s = get_settings()
        limit = limit if limit is not None else s.GRAPH_SEED_CANDIDATES
        allowlist = _short_terms()

        # Lazy index build in case the graph was pre-populated externally (e.g. tests)
        if version not in self._index_cache:
            self._index_cache[version] = _build_index(graph, allowlist)
        index = self._index_cache[version]

        query_toks = _tokenize(query, allowlist)
        if not query_toks:
            return SeedResult(query=query, seeds=[])

        hits_per_tok = {tok: len(index.get(tok, set())) for tok in query_toks}
        log.debug(f"[graph.seeds] query_toks={query_toks} hits={hits_per_tok}")

        # Score candidates from the index (each token hit → +1)
        candidate_scores: dict[str, int] = {}
        for tok in query_toks:
            for nid in index.get(tok, set()):
                if category and graph.nodes[nid].get("category") != category:
                    continue
                candidate_scores[nid] = candidate_scores.get(nid, 0) + 1

        if not candidate_scores:
            log.info(f"[graph.seeds] zero hits — query_toks={query_toks} index_size={len(index)}")

        # Prefix bonus: +1 per node where any query token is a prefix of any label word
        for nid in list(candidate_scores):
            label_words = re.split(r"\W+", graph.nodes[nid].get("label", "").lower())
            for tok in query_toks:
                if any(w.startswith(tok) for w in label_words if w):
                    candidate_scores[nid] += 1
                    break  # one bonus per node per query

        scored = sorted(candidate_scores.items(), key=lambda x: -x[1])
        seeds = [
            SeedHit(
                id=nid,
                label=graph.nodes[nid]["label"],
                kind=graph.nodes[nid]["kind"],
                score=float(sc),
            )
            for nid, sc in scored[:limit]
        ]
        return SeedResult(query=query, seeds=seeds)

    _CODE_STOP = frozenset({
        "the", "a", "an", "to", "of", "for", "and", "or", "in", "on", "with", "add", "new",
        "screen", "page", "tab", "modal", "field", "flow", "section", "code", "info",
    })

    async def get_nodes_by_kind(self, kind: str) -> list[dict[str, Any]]:
        """All graph nodes of a given kind. Accepts a family code (``SCR``) or a full label
        (``Screen``/``Entity``) — normalized to the family. Additive helper (used by the technical
        intent tracer); returns light dicts ``{id, label, kind, source_locus, confidence}``."""
        graph, _, _ = await self._graph()
        want = {kind, (kind or "").upper()}
        try:
            want.add(family_of("", kind))
        except Exception:  # noqa: BLE001 — family_of is best-effort for normalization
            pass
        want = {w for w in want if w}
        kl = (kind or "").lower()
        out: list[dict[str, Any]] = []
        for nid, a in graph.nodes(data=True):
            if a.get("kind") in want or (a.get("kind_label") or "").lower() == kl:
                out.append({
                    "id": nid, "label": a.get("label", nid), "kind": a.get("kind"),
                    "source_locus": a.get("source_locus") or "", "confidence": a.get("confidence"),
                })
        return out

    async def get_node(self, node_id: str) -> dict[str, Any] | None:
        """A single graph node's attributes (id + label/kind/source_locus/summary/…), or None if absent.
        Additive helper. NOTE: graph nodes model entities + edges, NOT table column schemas, so callers
        expecting ``fields``/``field_mappings`` get an empty list (see the schema-check caveat)."""
        graph, _, _ = await self._graph()
        if node_id not in graph:
            return None
        attrs = dict(graph.nodes[node_id])
        attrs["id"] = node_id
        return attrs

    async def get_edges(self, node_id: str, edge_type: str = "") -> list[dict[str, Any]]:
        """Forward neighbours of ``node_id`` across ``edge_type`` (edge label; empty = any). Additive."""
        return await self._neighbors(node_id, edge_type, reverse=False)

    async def get_edges_reverse(self, node_id: str, edge_type: str = "") -> list[dict[str, Any]]:
        """Reverse (incoming) neighbours of ``node_id`` across ``edge_type`` (empty = any). Additive."""
        return await self._neighbors(node_id, edge_type, reverse=True)

    async def _neighbors(self, node_id: str, edge_type: str, *, reverse: bool) -> list[dict[str, Any]]:
        graph, _, _ = await self._graph()
        if node_id not in graph:
            return []
        edges = graph.in_edges(node_id, keys=True) if reverse else graph.out_edges(node_id, keys=True)
        out: list[dict[str, Any]] = []
        for u, v, k in edges:
            other = u if reverse else v
            lbl = graph.edges[u, v, k].get("label", "")
            if edge_type and lbl != edge_type:
                continue
            a = graph.nodes[other]
            out.append({
                "id": other, "label": a.get("label", other), "kind": a.get("kind"),
                "source_locus": a.get("source_locus") or "", "confidence": a.get("confidence"),
            })
        return out

    async def code_cards_for(self, terms: list[str], limit: int = 6) -> list[dict[str, Any]]:
        """Find CODE cards (Component/ApiOp carrying a real ``file:line`` locus) whose label or
        source-file path best matches *terms* — used to point the Developer at the ACTUAL file to
        change when a screen/entity is not yet graph-linked to its implementing code.

        Returns dicts ``{id, label, kind, source_locus, score}`` ranked by term-overlap. In-memory
        scan over the loaded graph (no DB round-trip). Read-only; never raises on a bad node.
        """
        graph, _, _ = await self._graph()
        words = {
            w for t in terms for w in re.findall(r"[a-z0-9]+", (t or "").lower())
            if len(w) >= 3 and w not in self._CODE_STOP
        }
        if not words:
            return []
        scored: list[tuple[int, dict[str, Any]]] = []
        for nid, a in graph.nodes(data=True):
            # Graph stores kind as the short family code (CMP/API); accept full labels too.
            if (a.get("kind") or "") not in ("CMP", "API", "Component", "ApiOp"):
                continue
            locus = a.get("source_locus") or ""
            if ":" not in locus:  # must carry a source-file locus
                continue
            hay = (str(a.get("label") or "") + " " + locus).lower()
            score = sum(1 for w in words if w in hay)
            if score:
                scored.append((score, {"id": nid, "label": a.get("label") or nid,
                                       "kind": a.get("kind"), "source_locus": locus, "score": score}))
        scored.sort(key=lambda x: -x[0])
        return [d for _s, d in scored[:limit]]

    async def expand_seeds(
        self,
        seed_ids: list[str],
        depth: int = 2,
        edge_labels: set[str] | None = None,
    ) -> list[SeedHit]:
        """BFS neighbour expansion from *seed_ids* following structural edges.

        Scores decay by distance: seeds → 1.0, 1-hop → 0.5, 2-hop → 0.25.
        Only edges whose ``label`` is in *edge_labels* are followed; set to ``None``
        to follow all edges (useful for testing). Nodes are never revisited — the
        first (highest) score a node receives is kept.

        Returns a list of ``SeedHit`` ordered by score descending (seeds first).
        Category filtering is intentionally NOT applied here — structural neighbours
        may cross category boundaries (e.g. shared PEGA/WOD system nodes).
        The persona kind filter in ``retrieval._select`` is the final gate.
        """
        graph, _, _ = await self._graph()
        scores: dict[str, float] = {}

        # Seeds at distance 0
        for sid in seed_ids:
            if sid in graph:
                scores[sid] = 1.0

        frontier = {sid for sid in seed_ids if sid in graph}
        for hop in range(1, depth + 1):
            decay = 0.5 ** hop
            next_frontier: set[str] = set()
            for node in frontier:
                neighbours: set[str] = set()
                for _, v, k in graph.out_edges(node, keys=True):
                    lbl = graph.edges[node, v, k].get("label", "")
                    if edge_labels is None or lbl in edge_labels:
                        neighbours.add(v)
                for u, _, k in graph.in_edges(node, keys=True):
                    lbl = graph.edges[u, node, k].get("label", "")
                    if edge_labels is None or lbl in edge_labels:
                        neighbours.add(u)
                for nbr in neighbours:
                    if nbr not in scores:
                        scores[nbr] = decay
                        next_frontier.add(nbr)
            frontier = next_frontier
            if not frontier:
                break

        return [
            SeedHit(
                id=nid,
                label=graph.nodes[nid].get("label", nid) if nid in graph else nid,
                kind=graph.nodes[nid].get("kind", "") if nid in graph else "",
                score=sc,
            )
            for nid, sc in sorted(scores.items(), key=lambda x: -x[1])
        ]

    async def neighbors(self, node_id: str, direction: str = "both", depth: int = 1) -> Subgraph:
        graph, _, _ = await self._graph()
        if node_id not in graph:
            return Subgraph(nodes=[], edges=[], root=node_id)
        direction = direction if direction in ("in", "out", "both") else "both"
        seen, ekeys = _walk(graph, node_id, direction, depth)
        return Subgraph(
            nodes=[self._node_dto(graph, n) for n in seen],
            edges=[self._edge_dto(graph, u, v, k) for (u, v, k) in ekeys],
            root=node_id,
        )

    async def impact(self, node_id: str, direction: str = "downstream") -> Subgraph:
        graph, _, _ = await self._graph()
        if node_id not in graph:
            return Subgraph(nodes=[], edges=[], root=node_id)
        walk_dir = "out" if direction == "downstream" else "in"
        seen, ekeys = _walk(graph, node_id, walk_dir, depth=50)
        return Subgraph(
            nodes=[self._node_dto(graph, n) for n in seen],
            edges=[self._edge_dto(graph, u, v, k) for (u, v, k) in ekeys],
            root=node_id,
        )

    async def node_detail(self, node_id: str) -> NodeDetail | None:
        graph, version, source = await self._graph()
        if node_id not in graph:
            return None
        a = graph.nodes[node_id]
        node = self._node_dto(graph, node_id)
        incoming = [self._edge_dto(graph, u, v, k) for u, v, k in graph.in_edges(node_id, keys=True)]
        outgoing = [self._edge_dto(graph, u, v, k) for u, v, k in graph.out_edges(node_id, keys=True)]

        if source == "fixture":
            meta = dict(a.get("_meta") or {})
            prose = meta.pop("prose", None) or a.get("summary")
            text_en = meta.pop("text_en", None)
            evidence = [EvidenceItem(**ev) for ev in meta.pop("evidence", [])]
            meta.pop("summary", None)
            metadata = meta
        else:
            pool = _pool_or_none()
            card = (await graph_dao.fetch_card(pool, version, node_id)) or {}
            prose = card.get("prose") or a.get("summary")
            text_en = card.get("text_en")
            metadata = card.get("metadata") or {}
            rows = await graph_dao.fetch_evidence(pool, version, node_id)
            evidence = [
                EvidenceItem(
                    source_locus=r["source_locus"],
                    snippet=r.get("snippet"),
                    anchor_verdict=r.get("anchor_verdict") or "PENDING",
                    authority_tier=r.get("authority_tier"),
                )
                for r in rows
            ]

        return NodeDetail(
            node=node, prose=prose, text_en=text_en,
            metadata=metadata, incoming=incoming, outgoing=outgoing, evidence=evidence,
        )


@lru_cache
def get_graph_provider() -> GraphProvider:
    """Process-wide singleton GraphProvider (cached)."""
    return GraphProvider()
