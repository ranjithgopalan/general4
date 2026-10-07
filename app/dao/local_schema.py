"""Replicate the platform's Postgres tables in a local database (PG_CONNECTION_MODE=local).

The RDS schema ``form_rationalization_anh`` is provisioned by migrations that live outside this
repository (``run.py db-apply``, migrations 0003-0026). This module recreates, from the SQL this
code base actually runs, the subset the knowledge-graph surface and telemetry need:

    fe_kb_versions   one row per KB build; exactly one ACTIVE per gear_id (graph_dao.active_version)
    fe_kb_nodes      graph nodes            (graph_dao.fetch_nodes*, kb/pg_sink.publish)
    fe_kb_edges      typed edges            (graph_dao.fetch_edges, graph_repository CTEs, pg_sink)
    fe_kb_cards      card bodies for drill  (graph_dao.fetch_card, pg_sink)
    fe_kb_evidence   verbatim evidence      (graph_dao.fetch_evidence, pg_sink)
    fe_telemetry     telemetry sink         (services/telemetry_pg_sink.py)

The ``fe_state_*`` control-plane tables are created by ``PostgresStore.ensure_schema()`` on first use,
so they are not repeated here. Everything is ``IF NOT EXISTS`` and safe to re-run.

    set PYTHONPATH=.
    python -m app.dao.local_schema                 # uses PG_LOCAL_URL + PG_SCHEMA from app/.env / config.json
    python -m app.dao.local_schema --url ... --schema form_rationalization_anh
"""
from __future__ import annotations

import argparse
import logging
import sys

logger = logging.getLogger(__name__)

DDL = """
CREATE SCHEMA IF NOT EXISTS {schema};

CREATE TABLE IF NOT EXISTS {schema}.fe_kb_versions (
    kb_version        text PRIMARY KEY,
    gear_id           text NOT NULL,
    status            text NOT NULL DEFAULT 'STAGING' CHECK (status IN ('STAGING', 'ACTIVE', 'SUPERSEDED')),
    build_date        timestamptz NOT NULL DEFAULT now(),
    coverage_stats    jsonb NOT NULL DEFAULT '{{}}'::jsonb,
    graph_node_count  integer NOT NULL DEFAULT 0,
    graph_edge_count  integer NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS fe_kb_versions_gear ON {schema}.fe_kb_versions (gear_id, status, build_date DESC);

CREATE TABLE IF NOT EXISTS {schema}.fe_kb_nodes (
    id            text NOT NULL,
    kb_version    text NOT NULL REFERENCES {schema}.fe_kb_versions (kb_version) ON DELETE CASCADE,
    kind          text,
    label         text,
    category      text,
    source_locus  text,
    confidence    double precision,
    metadata      jsonb NOT NULL DEFAULT '{{}}'::jsonb,
    PRIMARY KEY (kb_version, id)
);
CREATE INDEX IF NOT EXISTS fe_kb_nodes_category ON {schema}.fe_kb_nodes (kb_version, category);

CREATE TABLE IF NOT EXISTS {schema}.fe_kb_edges (
    kb_edge_id    bigserial PRIMARY KEY,
    kb_version    text NOT NULL REFERENCES {schema}.fe_kb_versions (kb_version) ON DELETE CASCADE,
    from_id       text NOT NULL,
    to_id         text NOT NULL,
    label         text NOT NULL,
    tag           text NOT NULL DEFAULT 'stated' CHECK (tag IN ('stated', 'inferred')),
    confidence    double precision,
    metadata      jsonb NOT NULL DEFAULT '{{}}'::jsonb,
    UNIQUE (kb_version, label, from_id, to_id)
);
CREATE INDEX IF NOT EXISTS fe_kb_edges_from ON {schema}.fe_kb_edges (kb_version, from_id);
CREATE INDEX IF NOT EXISTS fe_kb_edges_to   ON {schema}.fe_kb_edges (kb_version, to_id);

CREATE TABLE IF NOT EXISTS {schema}.fe_kb_cards (
    id            text NOT NULL,
    kb_version    text NOT NULL REFERENCES {schema}.fe_kb_versions (kb_version) ON DELETE CASCADE,
    kind          text,
    label         text,
    category      text,
    source_locus  text,
    confidence    double precision,
    text_en       text,
    metadata      jsonb NOT NULL DEFAULT '{{}}'::jsonb,
    PRIMARY KEY (kb_version, id)
);

CREATE TABLE IF NOT EXISTS {schema}.fe_kb_evidence (
    id              bigserial PRIMARY KEY,
    card_id         text NOT NULL,
    kb_version      text NOT NULL REFERENCES {schema}.fe_kb_versions (kb_version) ON DELETE CASCADE,
    source_locus    text,
    snippet         text,
    anchor_verdict  text NOT NULL DEFAULT 'PENDING'
                    CHECK (anchor_verdict IN ('VERIFIED', 'NOT_VERBATIM', 'WRONG_CONTEXT', 'FABRICATED', 'PENDING')),
    authority_tier  text CHECK (authority_tier IN ('A', 'B', 'C', 'D'))
);
CREATE INDEX IF NOT EXISTS fe_kb_evidence_card ON {schema}.fe_kb_evidence (kb_version, card_id);

CREATE TABLE IF NOT EXISTS {schema}.fe_telemetry (
    id                     bigserial PRIMARY KEY,
    created_at             timestamptz NOT NULL DEFAULT now(),
    event_type             text,
    latency_ms             double precision,
    tokens_in              integer,
    tokens_out             integer,
    model_tier             text,
    kb_version             text,
    metadata               jsonb,
    correlation_id         text,
    workflow_run_id        text,
    agent_run_id           text,
    stage                  text,
    workspace_id           text,
    module_id              text,
    feature_id             text,
    rule_id                text,
    artifact_id            text,
    prompt_version         text,
    agent_version          text,
    validation_status      text,
    evaluation_status      text,
    human_approval_status  text,
    retry_count            integer
);
"""

TABLES = ("fe_kb_versions", "fe_kb_nodes", "fe_kb_edges", "fe_kb_cards", "fe_kb_evidence", "fe_telemetry")


def apply(url: str, schema: str) -> list[str]:
    """Create the schema and tables; return the tables now present."""
    import psycopg  # noqa: PLC0415

    schema = "".join(c for c in schema if c.isalnum() or c == "_")
    with psycopg.connect(url, connect_timeout=10) as conn:
        with conn.cursor() as cur:
            cur.execute(DDL.format(schema=schema))
            cur.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = %s ORDER BY table_name",
                (schema,),
            )
            present = [r[0] for r in cur.fetchall()]
        conn.commit()
    return present


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", help="Postgres URL (default: PG_LOCAL_URL when PG_CONNECTION_MODE=local)")
    ap.add_argument("--schema", help="target schema (default: PG_SCHEMA)")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    from app.config import get_settings  # noqa: PLC0415

    s = get_settings()
    url = args.url or (s.PG_LOCAL_URL if s.pg_local else "")
    if not url:
        print("No URL: pass --url or set PG_CONNECTION_MODE=local and PG_LOCAL_URL in app/.env", file=sys.stderr)
        return 1
    schema = args.schema or s.PG_SCHEMA
    present = apply(url, schema)
    missing = [t for t in TABLES if t not in present]
    print(f"schema {schema}: {len(present)} tables present -> {', '.join(present)}")
    if missing:
        print("MISSING:", ", ".join(missing), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
