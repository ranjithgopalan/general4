"""Chunk storage and hybrid retrieval.

Semantic (pgvector HNSW cosine) and lexical (Postgres tsvector) searches are
fused with Reciprocal Rank Fusion. They fail differently and that is the point:
vector search finds a paraphrase but misses a rare identifier, and BM25 finds
`sp_GetApplicantRisk` but misses "how do we score an applicant". Fusing them
covers both without tuning a weight.

Two numbers are inherited from the reference implementation rather than invented:

    rrf_k = 60              standard RRF constant
    min_relevance = 0.008   the working threshold

The threshold matters more than it looks. RRF with k=60 maxes out at 2/60 ≈
0.0333 for a document ranked first in both lists, so the reference's original
0.05 was above the maximum achievable score and rejected every result. 0.008
corresponds to roughly rank 100 in both lists, which is genuine noise.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field

from app.agentic_platform.fe_core.rag.chunker import Chunk

logger = logging.getLogger(__name__)

RRF_K = 60
TOP_K_SEMANTIC = 20
TOP_K_BM25 = 20
TOP_K_FINAL = 8
MIN_RELEVANCE = 0.008
PER_TYPE_QUOTA = 3

#: Cosine-distance ceiling for a semantic hit to count as relevant.
#:
#: This exists because the RRF floor above cannot detect irrelevance. RRF scores a
#: rank, not a similarity: the top result of a search always scores 1/(k+0), so a
#: corpus of one unrelated chunk answers every question with the same 0.0167 as a
#: perfect match would. Tested directly -- "what colour is the login button?"
#: against a corpus containing only performance requirements returned that
#: requirement, above the floor, with full confidence.
#:
#: Calibrated against Titan v2, not guessed. Measured over on-topic and off-topic
#: questions against the same corpus:
#:
#:     on-topic   0.516 .. 0.774
#:     off-topic  0.897 .. 0.960
#:
#: The classes separate with a 0.123 gap, so 0.83 sits inside it with margin on
#: both sides. An earlier guess of 0.55 was inside the on-topic range and refused
#: legitimate questions -- "can we change the database schema?" measures 0.730
#: against a corpus that answers it explicitly.
#:
#: Titan v2 distances cluster high because the vectors are normalised and the
#: space is dense; the absolute value matters far less than the separation, which
#: is why this is measured per embedding model rather than carried over.
MAX_SEMANTIC_DISTANCE = 0.83


@dataclass
class RetrievedChunk:
    id: str
    content: str
    artifact_type: str
    source_path: str
    chunk_index: int
    score: float = 0.0
    #: Cosine distance from the query, when this came from semantic search.
    distance: float | None = None
    artifact_id: str | None = None
    epic_id: str | None = None
    stage_key: str | None = None
    metadata: dict = field(default_factory=dict)

    def citation(self) -> dict:
        return {
            "chunk_id": self.id,
            "artifact_type": self.artifact_type,
            "artifact_id": self.artifact_id,
            "source_path": self.source_path,
            "chunk_index": self.chunk_index,
            "stage_key": self.stage_key,
            "epic_id": self.epic_id,
            "score": round(self.score, 5),
        }


class ChunkStore:
    """Postgres-backed chunk storage. Requires FE_DB_URL."""

    def __init__(self, url: str, schema: str = "fe"):
        self.url = url
        self.schema = schema

    def _connect(self):
        from app.agentic_platform.fe_core.db.schema_admin import _connect

        return _connect(self.url)

    # -- writing ----------------------------------------------------------
    def ensure_project(self, project_id: str, name: str | None = None) -> None:
        """Make sure `fe.project` has this row before chunks reference it.

        Workspaces and artefacts still live in the JSON store, so nothing else
        writes `fe.project` yet -- which meant every chunk insert failed its
        foreign key. Satisfying the key here rather than dropping it keeps the
        guarantee that matters: a chunk cannot exist without a project, so it can
        never be retrievable by every project at once.
        """
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(f"SET search_path TO {self.schema}, public")
                cur.execute(
                    "INSERT INTO fe_project (kb_application_id, name) VALUES (%s, %s) "
                    "ON CONFLICT (kb_application_id) DO NOTHING",
                    (project_id, name or project_id),
                )

    def replace_chunks(
        self,
        *,
        project_id: str,
        source_path: str,
        chunks: list[Chunk],
        artifact_id: str | None = None,
        artifact_type: str,
        workspace_id: str | None = None,
        tier: str | None = None,
        epic_id: str | None = None,
        stage_key: str | None = None,
    ) -> int:
        """Replace every chunk for `source_path`, then insert the new set.

        Delete-then-insert rather than upsert: a re-chunk after an edit usually
        produces a *different number* of chunks, so upserting by index would leave
        the tail of the previous version behind, and those orphans would keep
        being retrieved as if current.
        """
        if not chunks:
            return 0
        # Cheap and idempotent; the alternative is a foreign-key error at the
        # first insert of every new project.
        self.ensure_project(project_id)
        rows = [
            (
                project_id, workspace_id, tier, epic_id, artifact_id,
                artifact_type, stage_key, source_path, c.index, c.strategy,
                c.content, c.token_estimate, json.dumps(c.metadata),
            )
            for c in chunks
        ]
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(f"SET search_path TO {self.schema}, public")
                cur.execute("DELETE FROM fe_kb_chunk WHERE source_path = %s",
                            (source_path,))
                cur.executemany(
                    """
                    INSERT INTO fe_kb_chunk (
                        project_id, workspace_id, tier, epic_id, artifact_id,
                        artifact_type, stage_key, source_path, chunk_index,
                        strategy, content, token_estimate, metadata
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    """,
                    rows,
                )
        logger.info("Stored %d chunk(s) for %s (%s)", len(rows), source_path,
                    artifact_type)
        return len(rows)

    def pending_embeddings(self, project_id: str | None = None,
                           limit: int = 200) -> list[tuple[str, str]]:
        """(id, content) for chunks with no embedding yet."""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(f"SET search_path TO {self.schema}, public")
                if project_id:
                    cur.execute(
                        "SELECT id::text, content FROM fe_kb_chunk "
                        "WHERE embedding IS NULL AND project_id = %s "
                        "ORDER BY created_at LIMIT %s",
                        (project_id, limit))
                else:
                    cur.execute(
                        "SELECT id::text, content FROM fe_kb_chunk "
                        "WHERE embedding IS NULL ORDER BY created_at LIMIT %s",
                        (limit,))
                return [(r[0], r[1]) for r in cur.fetchall()]

    def store_embeddings(self, pairs: list[tuple[str, list[float]]]) -> int:
        if not pairs:
            return 0
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(f"SET search_path TO {self.schema}, public")
                cur.executemany(
                    "UPDATE fe_kb_chunk SET embedding = %s::vector WHERE id = %s::uuid",
                    [(json.dumps(vec), cid) for cid, vec in pairs],
                )
        return len(pairs)

    def counts(self, project_id: str) -> dict:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(f"SET search_path TO {self.schema}, public")
                cur.execute(
                    "SELECT count(*), count(embedding), "
                    "count(DISTINCT artifact_type), count(DISTINCT source_path) "
                    "FROM fe_kb_chunk WHERE project_id = %s", (project_id,))
                total, embedded, types, sources = cur.fetchone()
                cur.execute(
                    "SELECT artifact_type, count(*) FROM fe_kb_chunk "
                    "WHERE project_id = %s GROUP BY 1 ORDER BY 2 DESC",
                    (project_id,))
                by_type = {r[0]: r[1] for r in cur.fetchall()}
        return {
            "chunks": total, "embedded": embedded,
            "awaiting_embedding": total - embedded,
            "artifact_types": types, "sources": sources, "by_type": by_type,
        }

    def artifact_types(self, project_id: str) -> list[str]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(f"SET search_path TO {self.schema}, public")
                cur.execute(
                    "SELECT DISTINCT artifact_type FROM fe_kb_chunk "
                    "WHERE project_id = %s ORDER BY 1", (project_id,))
                return [r[0] for r in cur.fetchall()]

    def list_modules(self, project_id: str) -> list[str]:
        """Return distinct module names from rule catalogue chunks.

        Returns empty list when:
        - No rule_catalogue chunks exist (detection failed or unstructured doc)
        - Triggers automatic fallback to vector search
        """
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(f"SET search_path TO {self.schema}, public")
                cur.execute(
                    """
                    SELECT DISTINCT metadata->>'module'
                    FROM fe_kb_chunk
                    WHERE project_id = %s
                      AND strategy = 'rule_catalogue'
                      AND metadata->>'module' IS NOT NULL
                    ORDER BY 1
                    """,
                    (project_id,)
                )
                return [row[0] for row in cur.fetchall()]

    def get_module_rules(self, project_id: str, module: str) -> list[RetrievedChunk]:
        """Direct lookup of ALL rules for a module. No vector search.

        Returns rules sorted by confidence descending, then rule name.
        """
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(f"SET search_path TO {self.schema}, public")
                cur.execute(
                    """
                    SELECT id::text, content, artifact_type, source_path,
                           chunk_index, artifact_id, epic_id, stage_key, metadata
                    FROM fe_kb_chunk
                    WHERE project_id = %s
                      AND strategy = 'rule_catalogue'
                      AND metadata->>'module' = %s
                    ORDER BY
                        (metadata->>'confidence')::int DESC,
                        metadata->>'rule_name'
                    """,
                    (project_id, module)
                )
                rows = cur.fetchall()
                return [_row(r) for r in rows]

    # -- reading ----------------------------------------------------------
    def _scope_sql(self, project_id: str, workspace_ids: list[str] | None,
                   artifact_types: list[str] | None) -> tuple[str, list]:
        clauses = ["project_id = %s"]
        params: list = [project_id]
        if workspace_ids:
            # A Mini Workspace reads its own chunks plus the Global tier's, and
            # never a sibling EPIC's -- the same rule as artefacts (FR-P4).
            clauses.append("(workspace_id = ANY(%s) OR workspace_id IS NULL)")
            params.append(list(workspace_ids))
        if artifact_types:
            clauses.append("artifact_type = ANY(%s)")
            params.append(list(artifact_types))
        return " AND ".join(clauses), params

    def semantic_search(self, project_id: str, vector: list[float], limit: int,
                        workspace_ids: list[str] | None = None,
                        artifact_types: list[str] | None = None
                        ) -> list[RetrievedChunk]:
        where, params = self._scope_sql(project_id, workspace_ids, artifact_types)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(f"SET search_path TO {self.schema}, public")
                cur.execute(
                    f"""
                    SELECT id::text, content, artifact_type, source_path,
                           chunk_index, artifact_id, epic_id, stage_key, metadata,
                           embedding <=> %s::vector AS distance
                    FROM fe_kb_chunk
                    WHERE {where} AND embedding IS NOT NULL
                    ORDER BY distance
                    LIMIT %s
                    """,
                    (json.dumps(vector), *params, limit),
                )
                rows = cur.fetchall()
        out = []
        for r in rows:
            chunk = _row(r)
            chunk.distance = float(r[9])
            out.append(chunk)
        return out

    def bm25_search(self, project_id: str, query: str, limit: int,
                    workspace_ids: list[str] | None = None,
                    artifact_types: list[str] | None = None
                    ) -> list[RetrievedChunk]:
        where, params = self._scope_sql(project_id, workspace_ids, artifact_types)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(f"SET search_path TO {self.schema}, public")
                # websearch_to_tsquery, not plainto_: it tolerates quotes and
                # OR/-negation from a person typing a real question, and never
                # raises on punctuation the way to_tsquery does.
                sql = f"""
                    SELECT id::text, content, artifact_type, source_path,
                           chunk_index, artifact_id, epic_id, stage_key, metadata
                    FROM fe_kb_chunk
                    WHERE {where}
                      AND content_tsv @@ websearch_to_tsquery('english', %s)
                    ORDER BY ts_rank_cd(content_tsv,
                                        websearch_to_tsquery('english', %s)) DESC
                    LIMIT %s
                    """
                cur.execute(sql, (*params, query, query, limit))
                rows = cur.fetchall()
                if not rows:
                    # websearch_to_tsquery ANDs every term, so a sentence-length
                    # question ("Product Requirements Document grounded in ...")
                    # matches nothing unless one chunk holds every word. Retry
                    # OR-ing the content words: ts_rank_cd still puts the chunks
                    # matching most of them first.
                    loose = _or_query(query)
                    if loose and loose != query:
                        cur.execute(sql, (*params, loose, loose, limit))
                        rows = cur.fetchall()
                return [_row(r) for r in rows]


_QUERY_STOPWORDS = frozenset("""
a an and are as at be by for from grounded has have in into is it its of on or
that the this to with document documents requirements requirement product
""".split())


def _or_query(query: str) -> str:
    """Content words of `query` joined with OR, in websearch_to_tsquery syntax."""
    words = []
    for w in re.findall(r"[A-Za-z][A-Za-z0-9_\-]{2,}", query):
        lw = w.lower()
        if lw in _QUERY_STOPWORDS or lw in words:
            continue
        words.append(lw)
    return " OR ".join(words)


def _row(r) -> RetrievedChunk:
    return RetrievedChunk(
        id=r[0], content=r[1], artifact_type=r[2], source_path=r[3],
        chunk_index=r[4], artifact_id=r[5], epic_id=r[6], stage_key=r[7],
        metadata=r[8] or {},
    )


def reciprocal_rank_fusion(*rankings: list[RetrievedChunk],
                           k: int = RRF_K) -> dict[str, float]:
    """Each list contributes 1/(k + rank) per id. Rank is 0-based."""
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, item in enumerate(ranking):
            scores[item.id] = scores.get(item.id, 0.0) + 1.0 / (k + rank)
    return scores


def quota_balance(items: list[RetrievedChunk],
                  per_type: int = PER_TYPE_QUOTA) -> list[RetrievedChunk]:
    """Cap how many chunks one artefact type may contribute.

    Without this a long document dominates: an SDD with 60 chunks outranks a
    two-chunk NFR on volume alone, and the answer then never sees the threshold
    it was asked about.

    With a single artefact type there is nothing to balance against, so the cap
    is not applied: before this, a corpus that was only the RED report returned
    exactly three chunks to every stage, whatever it asked.
    """
    if len({i.artifact_type for i in items}) <= 1:
        return items
    seen: dict[str, int] = {}
    out: list[RetrievedChunk] = []
    for item in items:
        used = seen.get(item.artifact_type, 0)
        if used < per_type:
            out.append(item)
            seen[item.artifact_type] = used + 1
    return out


@dataclass
class RetrievalResult:
    chunks: list[RetrievedChunk]
    top_score: float
    refused: bool
    reason: str | None
    searched_types: list[str]
    semantic_hits: int
    bm25_hits: int
    elapsed_ms: int


def retrieve(store: ChunkStore, project_id: str, question: str, *,
             workspace_ids: list[str] | None = None,
             artifact_types: list[str] | None = None,
             top_k: int = TOP_K_FINAL,
             embedder=None) -> RetrievalResult:
    """Hybrid retrieval, fused and quota-balanced.

    Returns `refused=True` when nothing clears the relevance floor. A refusal is
    a result, not an error: answering from an unrelated chunk is worse than saying
    the corpus does not cover the question.
    """
    started = time.perf_counter()
    embed_fn = embedder or _default_embedder

    semantic: list[RetrievedChunk] = []
    try:
        vector = embed_fn([question])[0]
        semantic = store.semantic_search(project_id, vector, TOP_K_SEMANTIC,
                                         workspace_ids, artifact_types)
    except Exception as exc:  # noqa: BLE001
        # Lexical search still works without embeddings, so a Bedrock outage
        # degrades the answer rather than removing it.
        logger.warning("Semantic search unavailable (%s); BM25 only", exc)

    lexical = store.bm25_search(project_id, question, TOP_K_BM25,
                                workspace_ids, artifact_types)

    elapsed = int((time.perf_counter() - started) * 1000)

    # Reject vector matches that are merely the nearest of a small corpus. A
    # lexical hit needs no such check: the words are literally present.
    near = [c for c in semantic
            if c.distance is not None and c.distance <= MAX_SEMANTIC_DISTANCE]
    if semantic and not near and not lexical:
        best = min(c.distance for c in semantic if c.distance is not None)
        return RetrievalResult(
            [], 0.0, True,
            f"the closest excerpt sits {best:.3f} cosine distance away, beyond the "
            f"{MAX_SEMANTIC_DISTANCE} relevance limit, and no keyword matched "
            f"either -- this corpus does not cover the question",
            artifact_types or [], len(semantic), len(lexical), elapsed)
    semantic = near

    scores = reciprocal_rank_fusion(semantic, lexical)
    if not scores:
        return RetrievalResult(
            [], 0.0, True,
            "nothing matched closely enough, semantically or lexically",
            artifact_types or [], len(semantic), len(lexical), elapsed)

    by_id = {c.id: c for c in [*semantic, *lexical]}
    ordered = sorted(scores, key=lambda i: scores[i], reverse=True)
    ranked: list[RetrievedChunk] = []
    for chunk_id in ordered:
        chunk = by_id.get(chunk_id)
        if chunk is None:
            continue
        chunk.score = scores[chunk_id]
        ranked.append(chunk)

    top = ranked[0].score if ranked else 0.0
    if top < MIN_RELEVANCE:
        return RetrievalResult(
            [], top, True,
            f"best match scored {top:.4f}, below the {MIN_RELEVANCE} floor",
            artifact_types or [], len(semantic), len(lexical), elapsed)

    if artifact_types is None or len(artifact_types) > 1:
        ranked = quota_balance(ranked)
    ranked = ranked[:top_k]

    logger.info(
        "Retrieved %d chunk(s) for %r: semantic=%d bm25=%d top=%.4f in %dms",
        len(ranked), question[:60], len(semantic), len(lexical), top, elapsed,
    )
    return RetrievalResult(ranked, top, False, None, artifact_types or [],
                           len(semantic), len(lexical), elapsed)


def retrieve_many(store: ChunkStore, project_id: str, questions: list[str], *,
                  workspace_ids: list[str] | None = None,
                  artifact_types: list[str] | None = None,
                  per_query: int = TOP_K_FINAL,
                  total: int = 24,
                  embedder=None) -> RetrievalResult:
    """`retrieve` for several questions, merged.

    A stage that reads a large document needs more than one question: the RED
    for this project is ~20,000 chunks and a single stage-title query returned
    three near-random ones. Each question is retrieved on its own (so the
    relevance floor still applies per question), then the results are merged
    by best score, de-duplicated by chunk id and capped at `total`. Refused
    only when every question refused.
    """
    started = time.perf_counter()
    merged: dict[str, RetrievedChunk] = {}
    reasons: list[str] = []
    types: set[str] = set()
    sem = bm = 0
    top = 0.0
    for q in [q for q in questions if q and q.strip()]:
        r = retrieve(store, project_id, q, workspace_ids=workspace_ids,
                     artifact_types=artifact_types, top_k=per_query,
                     embedder=embedder)
        sem += r.semantic_hits
        bm += r.bm25_hits
        types.update(r.searched_types)
        if r.refused:
            reasons.append(f"{q[:50]!r}: {r.reason}")
            continue
        top = max(top, r.top_score)
        for c in r.chunks:
            prev = merged.get(c.id)
            if prev is None or c.score > prev.score:
                merged[c.id] = c
    elapsed = int((time.perf_counter() - started) * 1000)
    if not merged:
        return RetrievalResult([], 0.0, True, "; ".join(reasons) or "no questions",
                               sorted(types), sem, bm, elapsed)
    ranked = sorted(merged.values(), key=lambda c: c.score, reverse=True)
    if artifact_types is None or len(artifact_types) > 1:
        # Share `total` across the types present rather than the per-question
        # quota, which would leave most of the budget unused.
        n_types = max(1, len({c.artifact_type for c in ranked}))
        ranked = quota_balance(ranked, per_type=max(PER_TYPE_QUOTA, total // n_types))
    ranked = ranked[:total]
    logger.info(
        "Retrieved %d chunk(s) for %d question(s): semantic=%d bm25=%d top=%.4f in %dms",
        len(ranked), len(questions), sem, bm, top, elapsed,
    )
    return RetrievalResult(ranked, top, False, None, sorted(types), sem, bm, elapsed)


def _default_embedder(texts: list[str]) -> list[list[float]]:
    from app.agentic_platform.fe_core.rag.bedrock import embed

    return embed(texts)
