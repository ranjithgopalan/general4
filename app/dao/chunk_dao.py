"""
Read-only DAO for hybrid retrieval over ``fe_kb_chunks`` / ``fe_kb_cards`` (docs/09 §3.5, docs/20 §8).

Two ranked lanes — dense (pgvector cosine, HNSW) and BM25 (``ts_rank_cd`` over the ``bm25_text``
tsvector GIN) — plus a card-metadata fetch. Everything is kb_version-scoped and **read-only**
(RE owns all writes, docs/09 §8). Chunks are deduped to their best-scoring chunk per card, so the
lanes return card-level candidates ready for RRF fusion (tables: ``fe_kb_chunks`` / ``fe_kb_cards``).
"""

from __future__ import annotations

from typing import Any

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool


def _vector_literal(vec: list[float]) -> str:
    """pgvector text form ``[0.1,0.2,...]``.

    Bound as text + an explicit ``::vector`` cast so the query works whether or not the vector
    type has been registered on the pooled connection (register is best-effort in ``dao/postgres``).
    """
    return "[" + ",".join(f"{x:.8f}" for x in vec) + "]"


async def dense_search(
    pool: AsyncConnectionPool, kb_version: str, query_vec: list[float], limit: int
) -> list[dict[str, Any]]:
    """Top cards by cosine similarity (HNSW), deduped to the best chunk per card.

    The inner ``ORDER BY ... LIMIT`` uses the HNSW index; the outer aggregate collapses chunks to
    their best (min-distance) card. Returns ``[{card_id, score}]`` with ``score = 1 - distance``.
    """
    vec = _vector_literal(query_vec)
    sql = """
        SELECT card_id, MIN(dist) AS dist
        FROM (
            SELECT card_id, embedding <=> %s::vector AS dist
            FROM fe_kb_chunks
            WHERE kb_version = %s AND embedding IS NOT NULL
            ORDER BY embedding <=> %s::vector
            LIMIT %s
        ) t
        GROUP BY card_id
        ORDER BY dist ASC
    """
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(sql, (vec, kb_version, vec, limit))
            rows = await cur.fetchall()
    return [{"card_id": r["card_id"], "score": 1.0 - float(r["dist"])} for r in rows]


async def bm25_search(
    pool: AsyncConnectionPool, kb_version: str, query: str, limit: int
) -> list[dict[str, Any]]:
    """Top cards by BM25-like full-text rank (``ts_rank_cd`` over the ``bm25_text`` GIN index)."""
    sql = """
        SELECT card_id, MAX(ts_rank_cd(bm25_text, q)) AS score
        FROM fe_kb_chunks, plainto_tsquery('english', %s) q
        WHERE kb_version = %s AND bm25_text @@ q
        GROUP BY card_id
        ORDER BY score DESC
        LIMIT %s
    """
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(sql, (query, kb_version, limit))
            rows = await cur.fetchall()
    return [{"card_id": r["card_id"], "score": float(r["score"])} for r in rows]


async def fetch_card_meta(
    pool: AsyncConnectionPool, kb_version: str, ids: list[str]
) -> list[dict[str, Any]]:
    """Card ``kind``/``label``/``category``/``source_locus`` for a set of ids.

    Backs the persona ``includeKinds`` filter and the citation loci. Returns ``[]`` for no ids.
    """
    if not ids:
        return []
    sql = """
        SELECT id, kind, label, category, source_locus, review_state
        FROM fe_kb_cards
        WHERE kb_version = %s AND id = ANY(%s)
    """
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(sql, (kb_version, list(ids)))
            return await cur.fetchall()
