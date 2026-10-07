"""
Hybrid retrieval (docs/20 §8) — graph + dense + BM25 lanes fused by Reciprocal Rank Fusion.

Adopts the UW/lmod pattern: run the lanes (concurrently for the DB lanes), fuse by reciprocal
rank (k=60), then scope the fused candidates to the persona's allowed kinds and the requested
category. Degrades gracefully:

  * no DB pool  → graph lane only (dev / fixture)
  * no embedder → graph + BM25 (dense lane skipped)

The persona filter and cite-or-abstain hold in every mode — the moat does not depend on the LLM
or the reranker (those land in P2/P4). ``retrieve`` returns card-level ``Candidate`` objects
ordered by fused score; ``kb_query`` turns them into citations.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from app.config.graph_layers import family_of
from app.config.settings import Settings, get_settings
from app.dao import chunk_dao, graph_dao
from app.services.embedder import embed_query
from app.services.graph_provider import GraphProvider, get_graph_provider
from app.services.query_expand import expand_query
from app.services.reranker import Reranker, get_reranker
from app.services.telemetry import get_telemetry_service
from app.utils.logging import log


@dataclass
class Candidate:
    """One fused, persona-allowed retrieval hit (card level)."""

    id: str
    kind: str
    label: str
    source_locus: str | None
    score: float
    review_state: str | None = None  # Gaps 4+5: 'needs-ja-sme' or 'DISPUTED' from kb_cards


@dataclass
class RetrievalResult:
    """Ordered candidates + the version/source they came from ('db' | 'fixture')."""

    candidates: list[Candidate]
    kb_version: str
    source: str


def reciprocal_rank_fusion(rankings: Iterable[list[str]], k: int) -> dict[str, float]:
    """RRF: ``score(id) = Σ 1/(k + rank)`` across lanes (rank 1-based). Pure + deterministic."""
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, cid in enumerate(ranking, start=1):
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank)
    return scores


# Intent domains (from the intake classifier) → the KB families they most likely need. Used to
# boost intent-relevant candidates in the fused ranking (Phase 1 — intent-aware retrieval).
_DOMAIN_FAMILIES: dict[str, frozenset[str]] = {
    "code": frozenset({"CMP", "API"}),
    "architecture": frozenset({"SYS", "CMP", "INT", "REL"}),
    "systems": frozenset({"SYS", "INT", "API"}),
    "process": frozenset({"PROC"}),
    "workflow": frozenset({"WF", "SEQ", "STM"}),
    "rules": frozenset({"BR", "FR"}),
    "screens": frozenset({"SCR"}),
    "data": frozenset({"ENT", "TERM", "DOM"}),
    "support": frozenset(),  # no dedicated family
}


def _apply_domain_boost(fused: dict[str, float], domains: list[str], boost: float) -> None:
    """Lift fused scores of candidates whose ID-family matches the classified question domains.

    Multiplicative (``×(1+boost)``) so it nudges intent-relevant families up without swamping a
    strong lexical/vector hit. In-place; no-op when boost<=0 or no domains map to a family."""
    if boost <= 0 or not domains:
        return
    target: set[str] = set().union(*(_DOMAIN_FAMILIES.get(d, frozenset()) for d in domains))
    if not target:
        return
    for cid in fused:
        if family_of(cid) in target:  # family_of derives from the id prefix — no kind lookup needed
            fused[cid] *= (1.0 + boost)


def _pool_or_none():
    """The Postgres pool if initialized, else None (dev without a DB → graph/fixture only)."""
    try:
        from app.dao.postgres import get_pool

        return get_pool()
    except Exception:
        return None


class HybridRetriever:
    """Graph + dense + BM25 → RRF, scoped to a persona's allowed kinds and a category."""

    def __init__(
        self,
        provider: GraphProvider | None = None,
        settings: Settings | None = None,
        reranker: Reranker | None = None,
    ) -> None:
        self._provider = provider or get_graph_provider()
        self._settings = settings or get_settings()
        self._reranker = reranker or get_reranker()

    async def retrieve(
        self,
        *,
        query: str,
        persona_kinds: Iterable[str],
        category: str | None = None,
        top_k: int | None = None,
        hybrid: bool | None = None,
        kb_version_override: str | None = None,  # ← Workspace-pinned KB version
        all_kinds: bool = False,  # chatbot assistant: bypass the persona kind filter (read every kind)
        domains: list[str] | None = None,  # intake domains → intent-aware family boost (Phase 1)
    ) -> RetrievalResult:
        t0 = time.perf_counter()
        s = self._settings
        top_k = top_k or s.RETRIEVAL_TOP_K
        hybrid = s.RETRIEVAL_HYBRID_DEFAULT if hybrid is None else hybrid
        pool = _pool_or_none()
        version, source = await self._resolve_version(pool, override=kb_version_override)
        rankings, seed_meta = await self._lanes(
            query=query, category=category, version=version, pool=pool, hybrid=hybrid
        )
        fused = reciprocal_rank_fusion(rankings, s.RETRIEVAL_RRF_K)
        _apply_domain_boost(fused, domains or [], s.RETRIEVAL_DOMAIN_BOOST)  # intent-aware
        ordered = sorted(fused, key=lambda cid: (-fused[cid], cid))  # B2: id tiebreak → stable ties
        ordered = await self._maybe_rerank(query, ordered)
        meta = await self._card_meta(pool, version, ordered, source, seed_meta)
        candidates = self._select(ordered, fused, meta, set(persona_kinds), category, top_k, all_kinds)
        if source != "db":
            await self._fill_loci(candidates)

        # ── Observability ─────────────────────────────────────────────────────────
        latency_ms = (time.perf_counter() - t0) * 1000
        # Lane names match the insertion order in _lanes(): graph always first, then bm25+dense.
        _lane_names = ["graph", "bm25", "dense"]
        lane_counts = {_lane_names[i]: len(r) for i, r in enumerate(rankings)}
        top_score = max(fused.values()) if fused else 0.0
        log.info(
            f"[retrieval] version={version} source={source} lanes={lane_counts} "
            f"fused={len(fused)} selected={len(candidates)} "
            f"top_score={top_score:.4f} {latency_ms:.0f}ms"
        )
        try:
            svc = get_telemetry_service()
            svc.emit_retrieval(
                kb_version=version,
                lane_counts=lane_counts,
                fused_count=len(fused),
                final_count=len(candidates),
                latency_ms=latency_ms,
                top_score=top_score,
            )
            for lane_name, lane_ids in zip(_lane_names, rankings):
                svc.emit_retrieval_lane(
                    lane=lane_name,
                    count=len(lane_ids),
                    candidate_ids=lane_ids,
                    kb_version=version,
                )
        except Exception:  # pragma: no cover — telemetry must never break retrieval
            pass

        return RetrievalResult(candidates=candidates, kb_version=version, source=source)

    async def _fill_loci(self, candidates: list[Candidate]) -> None:
        """Fixture mode: backfill each citation's ``source_locus`` from the graph node drill.

        In DB mode the locus already comes from ``kb_cards`` (via ``fetch_card_meta``); the fixture
        graph carries it on the node, so we fetch it only for the (few) selected candidates.
        """
        detail_fn = getattr(self._provider, "node_detail", None)
        if detail_fn is None:
            return
        for cand in candidates:
            if cand.source_locus is not None:
                continue
            try:
                detail = await detail_fn(cand.id)
            except Exception:  # noqa: BLE001 — locus backfill is best-effort
                continue
            node = getattr(detail, "node", None)
            if node is not None:
                cand.source_locus = node.source_locus

    async def _resolve_version(self, pool: Any, override: str | None = None) -> tuple[str, str]:
        """(kb_version, source) — pinned override > ACTIVE from DB > fixture fallback."""
        if override:
            return override, "db"

        if pool is None:
            return "fixture", "fixture"

        try:
            version = await graph_dao.active_version(pool, self._settings.GEAR_ID)
            if version:
                return version, "db"
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                f"[retrieval] Failed to fetch ACTIVE KB version from database: {exc}\n"
                f"Ensure: 1) Database is running, 2) KB has been built with /kb-build, "
                f"3) kb_versions table has an ACTIVE row"
            ) from exc

        return "fixture", "fixture"

    async def _lanes(
        self, *, query: str, category: str | None, version: str, pool: Any, hybrid: bool
    ) -> tuple[list[list[str]], dict[str, tuple[str, str]]]:
        """Run the lanes → per-lane ranked id lists + graph seed metadata (for fixture mode).

        Graph lane pipeline:
          1. Domain vocab expansion (graph lane only — BM25/dense receive original query)
          2. Seed search via inverted index
          3. Optional BFS neighbour expansion (GRAPH_EXPANSION_ENABLED)
        """
        s = self._settings

        # Step 1 — expand query with domain vocab aliases for the graph lane only
        graph_query = expand_query(query, s.GRAPH_DOMAIN_VOCAB)

        # Step 2 — seed search
        seeds = await self._provider.seeds(
            query=graph_query,
            category=category,
            limit=s.GRAPH_SEED_CANDIDATES,
        )
        seed_meta: dict[str, tuple[str, str]] = {h.id: (h.kind, h.label) for h in seeds.seeds}

        # Step 3 — optional BFS expansion via structural edges
        if s.GRAPH_EXPANSION_ENABLED and seeds.seeds:
            edge_labels = {
                lbl.strip()
                for lbl in s.GRAPH_EXPANSION_EDGE_LABELS.split(",")
                if lbl.strip()
            }
            expanded = await self._provider.expand_seeds(
                seed_ids=[h.id for h in seeds.seeds],
                depth=s.GRAPH_EXPANSION_DEPTH,
                edge_labels=edge_labels,
            )
            # Populate seed_meta with neighbour kind/label for fixture-mode card metadata
            for hit in expanded:
                seed_meta.setdefault(hit.id, (hit.kind, hit.label))
            graph_ranking = [h.id for h in expanded]
        else:
            graph_ranking = [h.id for h in seeds.seeds]

        rankings: list[list[str]] = [graph_ranking]
        if pool is not None and hybrid:
            # BM25 and dense always use the original (unexpanded) query
            bm25, dense = await self._db_lanes(query, version, pool)
            if bm25:
                rankings.append(bm25)
            if dense:
                rankings.append(dense)
        return rankings, seed_meta

    async def _db_lanes(self, query: str, version: str, pool: Any) -> tuple[list[str], list[str]]:
        """BM25 + dense lanes, run concurrently; each degrades to [] on its own failure."""
        cand = self._settings.RETRIEVAL_LANE_CANDIDATES

        async def _bm25() -> list[str]:
            try:
                rows = await chunk_dao.bm25_search(pool, version, query, cand)
                return [r["card_id"] for r in rows]
            except Exception as exc:  # noqa: BLE001
                log.warning(f"[retrieval] BM25 lane failed: {exc}")
                return []

        async def _dense() -> list[str]:
            vec = await embed_query(query)
            if not vec:
                return []
            try:
                rows = await chunk_dao.dense_search(pool, version, vec, cand)
                return [r["card_id"] for r in rows]
            except Exception as exc:  # noqa: BLE001
                log.warning(f"[retrieval] dense lane failed: {exc}")
                return []

        return await asyncio.gather(_bm25(), _dense())

    async def _card_meta(
        self, pool: Any, version: str, ids: list[str], source: str, seed_meta: dict[str, tuple[str, str]]
    ) -> dict[str, dict[str, Any]]:
        """Card metadata for the fused ids — from ``kb_cards`` (db) or the graph seeds (fixture).

        Falls back to graph-node metadata (seed_meta) when kb_cards has no rows for this version
        — the common state when the RE pipeline has populated kb_nodes but not yet kb_cards.
        """
        if source == "db" and pool is not None and ids:
            try:
                rows = await chunk_dao.fetch_card_meta(pool, version, ids)
            except Exception as exc:  # noqa: BLE001
                log.warning(f"[retrieval] fetch_card_meta failed ({exc}); falling back to graph node metadata")
                rows = []
            if rows:
                return {r["id"]: r for r in rows}
            log.info("[retrieval] kb_cards empty for this version — using graph node metadata as fallback")
        return {
            cid: {"id": cid, "kind": kind, "label": label, "category": None, "source_locus": None}
            for cid, (kind, label) in seed_meta.items()
            if cid in set(ids)
        }

    async def _maybe_rerank(self, query: str, ordered: list[str]) -> list[str]:
        """Optional cross-encoder rerank of the fused order (config-gated; passthrough when off)."""
        if not self._reranker.enabled or len(ordered) < 2:
            return ordered
        cap = max(self._settings.RETRIEVAL_LANE_CANDIDATES, self._settings.RETRIEVAL_TOP_K)
        head = ordered[:cap]
        cards = await self.content(head)
        items = [(cid, cards.get(cid, {}).get("prose") or cards.get(cid, {}).get("label") or cid) for cid in head]
        reranked = await self._reranker.rerank(query, items)
        seen = set(reranked)
        return reranked + [cid for cid in ordered if cid not in seen]

    async def content(self, ids: list[str]) -> dict[str, dict[str, Any]]:
        """Fetch card bodies (prose/text_en + label/kind/locus) for synthesis context, via the graph drill."""
        out: dict[str, dict[str, Any]] = {}
        detail_fn = getattr(self._provider, "node_detail", None)
        if detail_fn is None:
            return out
        for cid in ids:
            try:
                detail = await detail_fn(cid)
            except Exception:  # noqa: BLE001 — a missing body just yields a thinner context
                detail = None
            node = getattr(detail, "node", None)
            if node is not None:
                out[cid] = {
                    "kind": node.kind,
                    "label": node.label,
                    "prose": getattr(detail, "prose", None),
                    "text_en": getattr(detail, "text_en", None),
                    "source_locus": node.source_locus,
                }
        return out

    @staticmethod
    def _select(
        ordered: list[str],
        fused: dict[str, float],
        meta: dict[str, dict[str, Any]],
        persona_kinds: set[str],
        category: str | None,
        top_k: int,
        all_kinds: bool = False,
    ) -> list[Candidate]:
        """Persona ``includeKinds`` + category filter, in fused-score order, capped at ``top_k``.

        ``all_kinds=True`` (chatbot assistant) bypasses the persona kind filter so every family is
        eligible — the assistant can answer any question; persona still governs FE writes elsewhere."""
        out: list[Candidate] = []
        for cid in ordered:
            m = meta.get(cid)
            if not m:
                continue
            # Normalize the stored kind to its short ID-family ("System" -> "SYS", or the id prefix)
            # so it compares against the persona's includeKinds. The RE build stores full node-labels
            # in kb_cards.kind; family_of() bridges both conventions (the persona filter was silently
            # dropping every card without this).
            fam = family_of(cid, m.get("kind"))
            if not all_kinds and fam not in persona_kinds:
                continue
            if category and m.get("category") and m["category"] != category:
                continue
            out.append(
                Candidate(
                    id=cid,
                    kind=fam,
                    label=m["label"],
                    source_locus=m.get("source_locus"),
                    score=round(fused[cid], 6),
                    review_state=m.get("review_state"),  # Gaps 4+5
                )
            )
            if len(out) >= top_k:
                break
        return out


@lru_cache
def get_hybrid_retriever() -> HybridRetriever:
    """Process-wide singleton HybridRetriever (cached)."""
    return HybridRetriever()
