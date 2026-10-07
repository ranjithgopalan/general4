"""Copy JsonFileStore state (state.json) into PostgresStore tables. Idempotent.

    python src/api/scripts/migrate_state_json.py [--state-dir src/workspaces/_state] [--dry-run]

Reads FE_DB_URL / FE_DB_SCHEMA from the normal settings chain (env > .env >
config.json). Existing rows with the same ids are overwritten with the JSON
copy (put/save semantics), so re-running after a partial run is safe.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
for rel in ("src/api/packages/core", "src/api", "src/worker"):
    p = str(ROOT / rel)
    if p not in sys.path:
        sys.path.insert(0, p)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state-dir", default=None, help="folder holding state.json (default <workspace_root>/_state)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    from fe_core.config import get_settings
    from fe_core.store import JsonFileStore
    from fe_core.store_pg import PostgresStore

    settings = get_settings()
    state_dir = Path(args.state_dir) if args.state_dir else settings.fe_workspace_root / "_state"
    src = JsonFileStore(state_dir)
    counts = src.counts()
    print(f"source {state_dir/'state.json'}: {counts}")
    if args.dry_run:
        return 0
    if not settings.fe_db_url:
        print("FE_DB_URL is not set", file=sys.stderr)
        return 2
    dst = PostgresStore(settings.fe_db_url, settings.fe_db_schema)

    n = 0
    for ws in src.list_workspaces():
        dst.put_workspace(ws); n += 1
    for run in src.list_runs():
        dst.save_run(run); n += 1
    for art in src.list_artifacts():
        dst.put_artifact(art); n += 1
    # approvals: re-insert by id (append-only table; skip if present)
    import psycopg
    from fe_core.store_pg import _dump
    with psycopg.connect(settings.fe_db_url) as conn, conn.cursor() as cur:
        for art in src.list_artifacts():
            for ap_ in src.approvals_for(art.id):
                cur.execute(
                    f"INSERT INTO {dst.schema}.fe_state_approval (approval_id, artifact_id, decided_at, doc) "
                    f"VALUES (%s,%s,%s,%s::jsonb) ON CONFLICT (approval_id) DO NOTHING",
                    (ap_.approval_id, ap_.artifact_id, ap_.decided_at, _dump(ap_)))
                n += 1
        for ev in src.pending_outbox(limit=100000):
            cur.execute(
                f"INSERT INTO {dst.schema}.fe_state_outbox (event_id, idempotency_key, status, created_at, next_attempt_at, doc) "
                f"VALUES (%s,%s,%s,%s,%s,%s::jsonb) ON CONFLICT (idempotency_key) DO NOTHING",
                (ev.event_id, ev.idempotency_key, ev.status.value, ev.created_at, ev.next_attempt_at, _dump(ev)))
            n += 1
        conn.commit()
    print(f"migrated {n} record(s) into {settings.fe_db_schema}.fe_state_* ; postgres now: {dst.counts()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
