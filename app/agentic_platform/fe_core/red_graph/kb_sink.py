"""Replicate a project's RED code graph into the platform's knowledge-graph tables.

Source: lmod's ``graph_nodes`` / ``graph_edges`` (schema RED_DB_SCHEMA), as written by
``red_graph.build``. Target: ``fe_kb_versions`` / ``fe_kb_nodes`` / ``fe_kb_edges`` / ``fe_kb_cards``
(schema PG_SCHEMA, the same tables the ``/re/graph`` explorer API and the Angular explorer read).

Mapping (ids follow the platform's family prefixes, see app/config/graph_layers.py):

    ASPPage  node      -> CMP-<PROJ>-NNNN   kind Component   category = module   source_locus = path
    Table    node      -> ENT-<PROJ>-TNNN   kind Entity
    USES       edge    -> DEPENDS_ON        (include / redirect / link)
    CALLS      edge    -> TRIGGERS          (form post / navigation)
    REFERENCES edge    -> REFERENCES        (page -> table)
    tag = stated when the name resolved unambiguously (confidence >= 0.9), else inferred.

One kb_version per project ("<project>-red-v1"); ``--activate`` makes it the ACTIVE version for
GEAR_ID so the explorer serves it (one ACTIVE per gear id, previous ones become SUPERSEDED).

    set PYTHONPATH=.
    python -m app.agentic_platform.fe_core.red_graph.kb_sink --project proj_0059 --activate
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from datetime import datetime, timezone
from typing import Any

from app.agentic_platform.fe_core.red_graph.dao import EXTRACTOR_ID, RedGraphDao
from app.agentic_platform.fe_core.red_graph.derive import LABEL_PAGE, LABEL_TABLE, REL_CALLS, REL_REFERENCES, REL_USES

logger = logging.getLogger(__name__)

EDGE_LABELS = {REL_USES: "DEPENDS_ON", REL_CALLS: "TRIGGERS", REL_REFERENCES: "REFERENCES"}
SUMMARY_LEN = 200


def _proj_code(project_id: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", project_id.upper().replace("PROJ_", "P")) or "PROJ"


def _sanitise(schema: str) -> str:
    return "".join(c for c in schema if c.isalnum() or c == "_") or "public"


def replicate(source: RedGraphDao, project_id: str, *, target_url: str, target_schema: str,
              kb_version: str, gear_id: str, activate: bool = False) -> dict[str, Any]:
    import psycopg  # noqa: PLC0415
    from psycopg.rows import dict_row  # noqa: PLC0415

    pages = source.nodes(project_id, labels=(LABEL_PAGE,), with_purpose=True)
    tables = source.nodes(project_id, labels=(LABEL_TABLE,))
    if not pages:
        raise LookupError(f"no {LABEL_PAGE} nodes for project {project_id!r}; run red_graph.build first")
    edges = source.edges(project_id, labels=(LABEL_PAGE, LABEL_TABLE), rel_types=tuple(EDGE_LABELS))
    edges = [e for e in edges if e.get("extractor_id") == EXTRACTOR_ID]

    code = _proj_code(project_id)
    kb_id: dict[int, str] = {}
    node_rows: list[tuple] = []
    card_rows: list[tuple] = []
    for i, n in enumerate(sorted(pages, key=lambda r: (r["name"], r["path"] or "")), 1):
        p = n.get("properties") or {}
        nid = f"CMP-{code}-{i:04d}"
        kb_id[n["id"]] = nid
        label = p.get("file_name") or n["name"]
        purpose = (p.get("purpose") or "").strip()
        meta = {
            "summary": purpose[:SUMMARY_LEN], "category": p.get("module"), "module": p.get("module"),
            "layer": p.get("layer"), "risk_level": p.get("risk_level"), "loc": p.get("loc"),
            "complexity_score": p.get("complexity_score"), "business_domain": p.get("business_domain"),
            "database_tables": p.get("database_tables") or [], "includes": p.get("includes") or [],
            "lmod_node_id": n["id"], "project_id": project_id, "origin": "reverse",
        }
        node_rows.append((nid, kb_version, "Component", label, p.get("module"), n["path"], 1.0, json.dumps(meta, ensure_ascii=False)))
        text = "\n\n".join(s for s in (label, purpose, f"Module: {p.get('module')}. Layer: {p.get('layer')}. Risk: {p.get('risk_level')}.") if s)
        card_rows.append((nid, kb_version, "Component", label, p.get("module"), n["path"], 1.0, text, json.dumps(meta, ensure_ascii=False)))
    for i, t in enumerate(sorted(tables, key=lambda r: r["name"]), 1):
        nid = f"ENT-{code}-T{i:04d}"
        kb_id[t["id"]] = nid
        node_rows.append((nid, kb_version, "Entity", t["name"], None, None, 1.0,
                          json.dumps({"summary": f"Database table {t['name']}", "lmod_node_id": t["id"], "project_id": project_id, "origin": "reverse"})))

    edge_rows: list[tuple] = []
    seen: set[tuple[str, str, str]] = set()
    for e in edges:
        s, t = kb_id.get(e["source_id"]), kb_id.get(e["target_id"])
        if not s or not t:
            continue
        label = EDGE_LABELS[e["rel_type"]]
        key = (label, s, t)
        if key in seen:
            continue
        seen.add(key)
        conf = float(e["confidence"]) if e.get("confidence") is not None else 1.0
        edge_rows.append((kb_version, s, t, label, "stated" if conf >= 0.9 else "inferred", conf,
                          json.dumps({"evidence": e.get("source_locator") or "", "lmod_edge_id": e["id"], "origin": "reverse"}, ensure_ascii=False)))

    sch = _sanitise(target_schema)
    stats = {"pages": len(pages), "tables": len(tables), "edges": len(edge_rows),
             "by_label": {lbl: sum(1 for r in edge_rows if r[3] == lbl) for lbl in EDGE_LABELS.values()}}
    with psycopg.connect(target_url, connect_timeout=10, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            for tbl in ("fe_kb_evidence", "fe_kb_edges", "fe_kb_cards", "fe_kb_nodes", "fe_kb_versions"):
                cur.execute(f"DELETE FROM {sch}.{tbl} WHERE kb_version = %s", (kb_version,))
            cur.execute(
                f"INSERT INTO {sch}.fe_kb_versions (kb_version, gear_id, status, build_date, coverage_stats, graph_node_count, graph_edge_count) "
                f"VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s)",
                (kb_version, gear_id, "STAGING", datetime.now(timezone.utc), json.dumps(stats), len(node_rows), len(edge_rows)),
            )
            cur.executemany(
                f"INSERT INTO {sch}.fe_kb_nodes (id, kb_version, kind, label, category, source_locus, confidence, metadata) "
                f"VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb)", node_rows)
            cur.executemany(
                f"INSERT INTO {sch}.fe_kb_cards (id, kb_version, kind, label, category, source_locus, confidence, text_en, metadata) "
                f"VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)", card_rows)
            if edge_rows:
                cur.executemany(
                    f"INSERT INTO {sch}.fe_kb_edges (kb_version, from_id, to_id, label, tag, confidence, metadata) "
                    f"VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb) ON CONFLICT (kb_version, label, from_id, to_id) DO NOTHING",
                    edge_rows)
            if activate:
                cur.execute(f"UPDATE {sch}.fe_kb_versions SET status = 'SUPERSEDED' WHERE gear_id = %s AND status = 'ACTIVE' AND kb_version <> %s",
                            (gear_id, kb_version))
                cur.execute(f"UPDATE {sch}.fe_kb_versions SET status = 'ACTIVE' WHERE kb_version = %s", (kb_version,))
        conn.commit()
    stats.update({"kb_version": kb_version, "gear_id": gear_id, "status": "ACTIVE" if activate else "STAGING",
                  "schema": sch, "sample_ids": [r[0] for r in node_rows[:3]]})
    logger.info("red_graph.kb_sink %s", stats)
    return stats


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", required=True)
    ap.add_argument("--kb-version", help="default <project>-red-v1")
    ap.add_argument("--gear-id", help="default GEAR_ID from settings")
    ap.add_argument("--activate", action="store_true", help="make this the ACTIVE version for the gear id")
    ap.add_argument("--target-url", help="default PG_LOCAL_URL (local mode) or FE_DB_URL")
    ap.add_argument("--target-schema", help="default PG_SCHEMA")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    from app.agentic_platform.fe_core.config import get_settings as fe_settings  # noqa: PLC0415
    from app.agentic_platform.fe_core.red_graph.build import dao_from_settings  # noqa: PLC0415
    from app.config import get_settings as agent_settings  # noqa: PLC0415

    a, f = agent_settings(), fe_settings()
    target_url = args.target_url or (a.PG_LOCAL_URL if a.pg_local else "") or f.fe_db_url or ""
    if not target_url:
        print("No target URL: set PG_CONNECTION_MODE=local + PG_LOCAL_URL (app/.env) or FE_DB_URL", file=sys.stderr)
        return 1
    stats = replicate(dao_from_settings(), args.project, target_url=target_url,
                      target_schema=args.target_schema or a.PG_SCHEMA,
                      kb_version=args.kb_version or f"{args.project}-red-v1",
                      gear_id=args.gear_id or a.GEAR_ID, activate=args.activate)
    print(json.dumps(stats, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
