"""Build the RED code graph for a project.

    # from the red_file_analyses table (the normal path)
    python -m app.agentic_platform.fe_core.red_graph.build --project proj_0001

    # from a RED JSON export, for a project whose analyses are not in this database
    python -m app.agentic_platform.fe_core.red_graph.build --project proj_0059 --json doc/red_analysis.json

    # just derive and report, write nothing
    python -m app.agentic_platform.fe_core.red_graph.build --project proj_0059 --json doc/red_analysis.json --dry-run

Connection: RED_DB_URL (falls back to FE_DB_URL); schema RED_DB_SCHEMA (default lmod).
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

from app.agentic_platform.fe_core.red_graph.dao import RedGraphDao
from app.agentic_platform.fe_core.red_graph.derive import DerivedGraph, derive_from_rows

logger = logging.getLogger(__name__)


def dao_from_settings() -> RedGraphDao:
    from app.agentic_platform.fe_core.config import get_settings  # noqa: PLC0415

    s = get_settings()
    url = red_graph_db_url()
    if not url:
        raise RuntimeError("Set PG_CONNECTION_MODE=local + PG_LOCAL_URL (app/.env), or RED_DB_URL / FE_DB_URL, "
                           "to the Postgres that holds red_file_analyses / graph_nodes")
    return RedGraphDao(url, s.red_db_schema)


def red_graph_db_url() -> str:
    """RED_DB_URL, else PG_LOCAL_URL when PG_CONNECTION_MODE=local, else FE_DB_URL. Empty when none is set."""
    from app.agentic_platform.fe_core.config import get_settings  # noqa: PLC0415

    s = get_settings()
    if s.red_db_url:
        return s.red_db_url
    try:
        from app.config import get_settings as agent_settings  # noqa: PLC0415

        a = agent_settings()
        if a.pg_local:
            return a.PG_LOCAL_URL
    except Exception:  # noqa: BLE001 - agent settings are optional for the vendored app
        pass
    return s.fe_db_url or ""


def derive_from_table(dao: RedGraphDao, project_id: str) -> DerivedGraph:
    rows = dao.fetch_file_analyses(project_id)
    if not rows:
        raise LookupError(f"no red_file_analyses rows for project {project_id!r}")
    return derive_from_rows(rows)


def derive_from_json(path: str | Path) -> DerivedGraph:
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    files = data.get("result", {}).get("files") if isinstance(data, dict) else None
    if files is None and isinstance(data, dict):
        files = data.get("files")
    if not files:
        raise LookupError(f"{path}: no result.files[] in this JSON")
    return derive_from_rows(files)


def build(project_id: str, *, json_path: str | None = None, dao: RedGraphDao | None = None,
          dry_run: bool = False) -> dict[str, Any]:
    dao = dao or dao_from_settings()
    graph = derive_from_json(json_path) if json_path else derive_from_table(dao, project_id)
    if dry_run:
        return {"project_id": project_id, "dry_run": True, **graph.summary()}
    return dao.upsert_graph(project_id, graph)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", required=True, help="project_id, e.g. proj_0001")
    ap.add_argument("--json", help="RED JSON export to read instead of red_file_analyses")
    ap.add_argument("--dry-run", action="store_true", help="derive and report only")
    ap.add_argument("--db-url", help="override RED_DB_URL")
    ap.add_argument("--schema", help="override RED_DB_SCHEMA")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    dao = None
    if args.db_url or args.schema:
        base = None
        try:
            base = dao_from_settings()
        except RuntimeError:
            if not args.db_url:
                raise
        dao = RedGraphDao(args.db_url or base.url, args.schema or (base.schema if base else "lmod"))
    stats = build(args.project, json_path=args.json, dao=dao, dry_run=args.dry_run)
    print(json.dumps(stats, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
