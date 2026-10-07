"""WP3: Postgres publish for G1 KB-extract output.

Reads the canonical KB tree written by writer.py from kb_dir and inserts one
STAGING kb_version row plus all nodes / edges / cards / evidence into the
form_rationalization_anh schema's fe_kb_* tables.

Idempotent: deletes any existing rows for the same kb_version before inserting.

Connection: reuses the same fe_db_url as PostgresStore (synchronous psycopg3
pool), targeting the form_rationalization_anh schema where the KB tables live.

Guard: the caller should check settings.uses_database() and only call publish()
when True. publish() will raise if the pool cannot be established.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Schema that owns the fe_kb_* tables (configurable via env for multi-tenant).
_KB_SCHEMA = os.environ.get("FE_KB_DB_SCHEMA", "form_rationalization_anh")

# Lazy-initialised module-level pool (one per process, shared across calls).
_pool: Any = None


def _get_pool(db_url: str):
    global _pool
    if _pool is not None:
        return _pool
    from psycopg.rows import dict_row  # noqa: PLC0415
    from psycopg_pool import ConnectionPool  # noqa: PLC0415

    _pool = ConnectionPool(
        db_url,
        min_size=1,
        max_size=4,
        timeout=15,
        kwargs={"row_factory": dict_row, "connect_timeout": 10},
        open=True,
    )
    return _pool


def _t(table: str) -> str:
    return f"{_KB_SCHEMA}.{table}"


# ---------------------------------------------------------------------------
# Helpers — read KB tree artefacts
# ---------------------------------------------------------------------------

def _read_cards_jsonl(kb_dir: Path) -> list[dict]:
    p = kb_dir / "cards.jsonl"
    if not p.exists():
        return []
    rows = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return rows


def _read_graph_json(kb_dir: Path) -> dict:
    p = kb_dir / "knowledge" / "ontology" / "graph.json"
    if not p.exists():
        return {"nodes": [], "edges": []}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {"nodes": [], "edges": []}


def _read_evidence_jsonl(kb_dir: Path) -> list[dict]:
    p = kb_dir / "evidence" / "evidence-map.jsonl"
    if not p.exists():
        return []
    rows = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return rows


def _build_coverage_stats(cards: list[dict], nodes: list[dict], edges: list[dict],
                           evidence: list[dict]) -> dict:
    kind_counts: dict[str, int] = {}
    for c in cards:
        k = c.get("kind", "unknown")
        kind_counts[k] = kind_counts.get(k, 0) + 1
    return {
        "cards": len(cards),
        "nodes": len(nodes),
        "edges": len(edges),
        "evidence": len(evidence),
        "by_kind": kind_counts,
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def publish(kb_dir: Path, app_id: str, module: str, kb_version: str, *,
            gear_id: str | None = None, db_url: str | None = None) -> dict:
    """Insert a STAGING KB version + graph data into fe_kb_* tables.

    The kb_version string is the caller's key (e.g. "{app_id}-re-v1"). This
    function is idempotent: it deletes any previous rows for kb_version and
    re-inserts, all within one transaction.

    Returns a summary dict {kb_version, nodes, edges, cards, evidence}.
    """
    if db_url is None:
        from app.agentic_platform.fe_core.config import get_settings  # noqa: PLC0415
        db_url = get_settings().fe_db_url
    if not db_url:
        raise RuntimeError("pg_sink.publish: fe_db_url is not configured")

    kb_dir = Path(kb_dir)
    cards = _read_cards_jsonl(kb_dir)
    graph = _read_graph_json(kb_dir)
    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])
    evidence = _read_evidence_jsonl(kb_dir)

    stats = _build_coverage_stats(cards, nodes, edges, evidence)
    build_date = datetime.now(timezone.utc)

    pool = _get_pool(db_url)
    with pool.connection() as conn:
        with conn.cursor() as cur:
            # ── Delete existing rows for this kb_version (idempotent) ───────
            for tbl in ("fe_kb_evidence", "fe_kb_edges", "fe_kb_cards",
                         "fe_kb_nodes", "fe_kb_versions"):
                cur.execute(f"DELETE FROM {_t(tbl)} WHERE kb_version = %s", (kb_version,))

            # ── fe_kb_versions ───────────────────────────────────────────────
            cur.execute(
                f"INSERT INTO {_t('fe_kb_versions')} "
                f"(kb_version, gear_id, status, build_date, coverage_stats, "
                f"graph_node_count, graph_edge_count) "
                f"VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s)",
                (
                    kb_version,
                    gear_id or app_id,
                    "STAGING",
                    build_date,
                    json.dumps(stats),
                    len(nodes),
                    len(edges),
                ),
            )

            # ── fe_kb_nodes ──────────────────────────────────────────────────
            node_rows = []
            for node in nodes:
                nid = node.get("id", "")
                if not nid:
                    continue
                node_rows.append((
                    nid,
                    kb_version,
                    node.get("kind", ""),
                    node.get("label", ""),
                    node.get("category") or module,
                    node.get("page", "") or None,  # source_locus
                    json.dumps({"page": node.get("page", "")}),
                ))
            if node_rows:
                cur.executemany(
                    f"INSERT INTO {_t('fe_kb_nodes')} "
                    f"(id, kb_version, kind, label, category, source_locus, metadata) "
                    f"VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)",
                    node_rows,
                )

            # ── fe_kb_cards ──────────────────────────────────────────────────
            card_rows = []
            for card in cards:
                cid = card.get("id", "")
                if not cid:
                    continue
                meta = card.get("meta", {})
                page = card.get("page", "")
                text_en_parts = [card.get("label", ""), card.get("summary", "")]
                # Include logic / acceptance_criteria / description in text_en
                for key in ("description", "logic", "acceptance_criteria"):
                    v = meta.get(key, "")
                    if v:
                        text_en_parts.append(str(v))
                text_en = "\n\n".join(p for p in text_en_parts if p)
                card_rows.append((
                    cid,
                    kb_version,
                    card.get("kind", ""),
                    card.get("label", ""),
                    meta.get("business_domain", page) or module,
                    page or cid,    # source_locus
                    float(meta.get("confidence", 1.0)),
                    text_en,
                    json.dumps(meta),
                ))
            if card_rows:
                cur.executemany(
                    f"INSERT INTO {_t('fe_kb_cards')} "
                    f"(id, kb_version, kind, label, category, source_locus, "
                    f"confidence, text_en, metadata) "
                    f"VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)",
                    card_rows,
                )

            # ── fe_kb_edges ──────────────────────────────────────────────────
            edge_rows = []
            for edge in edges:
                from_id = edge.get("source", "") or edge.get("from", "")
                to_id = edge.get("target", "") or edge.get("to", "")
                label = edge.get("label", "")
                if not from_id or not to_id or not label:
                    continue
                tag = edge.get("tag", "") or "stated"
                # tag constraint: stated | inferred only
                if tag not in ("stated", "inferred"):
                    tag = "stated"
                edge_rows.append((
                    kb_version,
                    from_id,
                    to_id,
                    label,
                    tag,
                    json.dumps({}),
                ))
            if edge_rows:
                cur.executemany(
                    f"INSERT INTO {_t('fe_kb_edges')} "
                    f"(kb_version, from_id, to_id, label, tag, metadata) "
                    f"VALUES (%s, %s, %s, %s, %s, %s::jsonb) "
                    f"ON CONFLICT (kb_version, label, from_id, to_id) DO NOTHING",
                    edge_rows,
                )

            # ── fe_kb_evidence ────────────────────────────────────────────────
            ev_rows = []
            for ev in evidence:
                card_id = ev.get("card_id", "")
                snippet = ev.get("snippet", "")
                if not card_id or not snippet:
                    continue
                locus = ev.get("locus", {})
                source_locus = json.dumps(locus) if locus else ev.get("source", "")
                anchor_verdict = ev.get("anchor_verdict", "PENDING")
                # Map internal anchor_verdict to DB constraint values
                if anchor_verdict in ("anchored",):
                    anchor_verdict = "VERIFIED"
                elif anchor_verdict in ("red_only",):
                    anchor_verdict = "PENDING"
                elif anchor_verdict not in ("VERIFIED", "NOT_VERBATIM", "WRONG_CONTEXT",
                                            "FABRICATED", "PENDING"):
                    anchor_verdict = "PENDING"
                authority_tier = ev.get("authority_tier", None)
                if authority_tier not in ("A", "B", "C", "D", None):
                    authority_tier = None
                ev_rows.append((
                    card_id,
                    kb_version,
                    source_locus or card_id,
                    snippet[:2000],
                    anchor_verdict,
                    authority_tier,
                ))
            if ev_rows:
                cur.executemany(
                    f"INSERT INTO {_t('fe_kb_evidence')} "
                    f"(card_id, kb_version, source_locus, snippet, "
                    f"anchor_verdict, authority_tier) "
                    f"VALUES (%s, %s, %s, %s, %s, %s)",
                    ev_rows,
                )

        conn.commit()

    logger.info(
        "pg_sink.publish: kb_version=%s nodes=%d edges=%d cards=%d evidence=%d",
        kb_version, len(nodes), len(edges), len(cards), len(evidence),
    )
    return {
        "kb_version": kb_version,
        "nodes": len(nodes),
        "edges": len(edges),
        "cards": len(cards),
        "evidence": len(evidence),
    }


def promote(kb_version: str, gear_id: str, *, db_url: str | None = None) -> None:
    """Promote kb_version to ACTIVE, superseding any previous ACTIVE version.

    Called from the G1 approval endpoint (re_review.py) when FE_KB_AUTO_PROMOTE
    is true or the operator manually approves.
    """
    if db_url is None:
        from app.agentic_platform.fe_core.config import get_settings  # noqa: PLC0415
        db_url = get_settings().fe_db_url
    if not db_url:
        raise RuntimeError("pg_sink.promote: fe_db_url is not configured")

    pool = _get_pool(db_url)
    with pool.connection() as conn:
        with conn.cursor() as cur:
            # Supersede current ACTIVE version for this gear_id
            cur.execute(
                f"UPDATE {_t('fe_kb_versions')} SET status='SUPERSEDED' "
                f"WHERE gear_id = %s AND status = 'ACTIVE' AND kb_version <> %s",
                (gear_id, kb_version),
            )
            # Promote the new version
            cur.execute(
                f"UPDATE {_t('fe_kb_versions')} SET status='ACTIVE' "
                f"WHERE kb_version = %s",
                (kb_version,),
            )
        conn.commit()
    logger.info("pg_sink.promote: %s → ACTIVE (gear_id=%s)", kb_version, gear_id)


def update_owner_epic(kb_version: str, card_to_epic: dict[str, str], *,
                       db_url: str | None = None) -> None:
    """Set metadata.owner_epic on fe_kb_nodes after EPIC map is computed."""
    if not card_to_epic:
        return
    if db_url is None:
        from app.agentic_platform.fe_core.config import get_settings  # noqa: PLC0415
        db_url = get_settings().fe_db_url
    if not db_url:
        return

    pool = _get_pool(db_url)
    with pool.connection() as conn:
        with conn.cursor() as cur:
            for card_id, epic in card_to_epic.items():
                cur.execute(
                    f"UPDATE {_t('fe_kb_nodes')} "
                    f"SET metadata = metadata || %s::jsonb "
                    f"WHERE id = %s AND kb_version = %s",
                    (json.dumps({"owner_epic": epic}), card_id, kb_version),
                )
        conn.commit()
    logger.info("pg_sink.update_owner_epic: %d cards updated", len(card_to_epic))
