"""The dependency graph in the platform's own KB tables (``fe_kb_versions`` / ``fe_kb_nodes`` /
``fe_kb_edges`` / ``fe_kb_cards``), the tables every deployment has.

Two halves:

``publish_derived``  write a :class:`~.derive.DerivedGraph` (from a RED file-analysis JSON or from
                     ``red_file_analyses``) as one kb_version. Node ids follow the platform's family
                     prefixes (app/config/graph_layers.py) so the ``/re/graph`` explorer renders them:

                         ASPPage -> CMP-<CODE>-NNNN   kind Component   category = module
                         Table   -> ENT-<CODE>-TNNNN  kind Entity
                         USES -> DEPENDS_ON, CALLS -> TRIGGERS, page->table -> REFERENCES
                         tag = stated when the name resolved unambiguously, else inferred

``KbGraphDao``       read it back: nodes, edges, module matrix, pair drill-down, node detail,
                     search, and hop-by-hop traversal / shortest path (one query per level).

The global library keeps one such version per Gear ID: ``_library-<gear>-deps-v1`` under
``gear_id = _library-<gear>`` (the same gear id the card library uses), status STAGING so it never
competes with the card build for the gear's ACTIVE slot.

Connection: ``kb_db_url()`` — FE_DB_URL, else PG_LOCAL_URL when PG_CONNECTION_MODE=local, else the
Vault-built URL the store uses. Schema: FE_KB_DB_SCHEMA (default form_rationalization_anh).
"""
from __future__ import annotations

import json
import logging
import re
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Iterable, Sequence

from app.agentic_platform.fe_core.red_graph.derive import REL_CALLS, REL_REFERENCES, REL_USES, DerivedGraph

logger = logging.getLogger(__name__)

EDGE_LABELS = {REL_USES: "DEPENDS_ON", REL_CALLS: "TRIGGERS", REL_REFERENCES: "REFERENCES"}
PAGE_KIND = "Component"
TABLE_KIND = "Entity"
PAGE_EDGE_LABELS = ("DEPENDS_ON", "TRIGGERS")
SUMMARY_LEN = 200
MAX_DEPTH = 8

LIBRARY_GEAR_PREFIX = "_library"
DEPS_VERSION_RE = re.compile(r"-deps-v(\d+)$")
CARDS_VERSION_RE = re.compile(r"-re-v(\d+)$")


def library_gear_id(gear: str | None) -> str:
    """The card library's gear id for a Gear ID: ``_library-1429``."""
    g = (gear or "").strip()
    return f"{LIBRARY_GEAR_PREFIX}-{g}" if g else LIBRARY_GEAR_PREFIX


def library_deps_gear_id(gear: str | None) -> str:
    """The dependency graph's own gear id (``_library-1429-deps``), so its ACTIVE version never
    competes with the card build's ACTIVE slot for the same Gear ID."""
    return f"{library_gear_id(gear)}-deps"


def library_deps_prefix(gear: str | None) -> str:
    return f"{library_gear_id(gear)}-deps-v"


def library_deps_version(gear: str | None, number: int = 1) -> str:
    """``_library-<gear>-deps-v<N>``: one row per upload; N counts up per Gear ID."""
    return f"{library_deps_prefix(gear)}{int(number)}"


def version_number(kb_version: str, pattern: re.Pattern[str] = DEPS_VERSION_RE) -> int | None:
    m = pattern.search(kb_version or "")
    return int(m.group(1)) if m else None


def _like(prefix: str) -> str:
    """LIKE pattern for a literal prefix (``_`` is a wildcard in LIKE, so escape it)."""
    return prefix.replace("\\", "\\\\").replace("_", r"\_").replace("%", r"\%") + "%"


def code_for(label: str) -> str:
    """Short upper-case token for node ids: 'proj_0059' -> 'P0059', '1429' -> 'L1429'."""
    s = re.sub(r"[^A-Z0-9]", "", (label or "").upper().replace("PROJ_", "P"))
    if not s:
        return "LIB"
    return s if s[0].isalpha() else f"L{s}"


def _schema(name: str) -> str:
    return "".join(c for c in (name or "") if c.isalnum() or c == "_") or "public"


# ----------------------------------------------------------------------------- connection
def kb_db_url() -> str:
    """The URL of the Postgres that holds the fe_kb_* tables, or "" when none is configured."""
    from app.agentic_platform.fe_core.config import get_settings  # noqa: PLC0415

    s = get_settings()
    if s.fe_db_url:
        return s.fe_db_url
    try:
        from app.config import get_settings as agent_settings  # noqa: PLC0415

        a = agent_settings()
        if a.pg_local:
            return a.PG_LOCAL_URL
    except Exception:  # noqa: BLE001 - the agent settings are optional for the vendored app
        pass
    try:
        from app.agentic_platform.fe_core.store import _fe_db_url_from_vault  # noqa: PLC0415

        return _fe_db_url_from_vault() or ""
    except Exception as exc:  # noqa: BLE001
        logger.debug("kb_db_url: Vault-built URL unavailable: %s", exc)
        return ""


def kb_schema() -> str:
    from app.agentic_platform.fe_core.kb.pg_sink import _KB_SCHEMA  # noqa: PLC0415

    return _schema(_KB_SCHEMA)


# ----------------------------------------------------------------------------- publish
def rows_from_derived(graph: DerivedGraph, *, kb_version: str, code: str, source: str) -> dict[str, Any]:
    """Turn a DerivedGraph into fe_kb_* row tuples. Pure; no database."""
    node_rows: list[tuple] = []
    card_rows: list[tuple] = []
    page_id: dict[str, str] = {}
    pages = sorted(graph.nodes.values(), key=lambda n: (n.name.lower(), n.path))
    for i, n in enumerate(pages, 1):
        nid = f"CMP-{code}-{i:04d}"
        page_id[n.key] = nid
        r = n.record
        label = r.file_name or n.name
        meta = {
            "summary": r.purpose[:SUMMARY_LEN], "category": n.module, "module": n.module,
            "file_name": r.file_name, "path": r.file_path, "layer": r.layer, "risk_level": r.risk_level,
            "loc": r.loc, "complexity_score": r.complexity_score, "cyclomatic_complexity": r.cyclomatic_complexity,
            "business_domain": r.business_domain, "business_rule_count": r.business_rule_count,
            "security_counts": r.security_counts, "modernization_approach": r.modernization_approach,
            "estimated_effort": r.estimated_effort, "database_tables": r.database_tables, "includes": n.includes,
            "red_analysis_id": r.analysis_id, "source": source, "origin": "reverse",
        }
        meta = {k: v for k, v in meta.items() if v is not None}
        node_rows.append((nid, kb_version, PAGE_KIND, label, n.module, r.file_path, 1.0, json.dumps(meta, ensure_ascii=False)))
        text = "\n\n".join(s for s in (label, r.purpose, f"Module: {n.module}. Layer: {r.layer}. Risk: {r.risk_level}.") if s)
        card_rows.append((nid, kb_version, PAGE_KIND, label, n.module, r.file_path, 1.0, text, json.dumps(meta, ensure_ascii=False)))

    table_id: dict[str, str] = {}
    for i, t in enumerate(sorted({t for n in pages for t in n.record.database_tables}), 1):
        tid = f"ENT-{code}-T{i:04d}"
        table_id[t] = tid
        node_rows.append((tid, kb_version, TABLE_KIND, t, None, None, 1.0,
                          json.dumps({"summary": f"Database table {t}", "source": source, "origin": "reverse"})))

    edge_rows: list[tuple] = []
    seen: set[tuple[str, str, str]] = set()

    def add(label: str, s: str, t: str, conf: float, evidence: str, ambiguous: bool) -> None:
        key = (label, s, t)
        if key in seen:
            return
        seen.add(key)
        edge_rows.append((kb_version, s, t, label, "stated" if conf >= 0.9 else "inferred", conf,
                          json.dumps({"evidence": evidence[:300], "ambiguous": ambiguous, "origin": "reverse"}, ensure_ascii=False)))

    for e in graph.edges:
        add(EDGE_LABELS[e.rel_type], page_id[e.source_key], page_id[e.target_key], e.confidence, e.evidence, e.ambiguous)
    for n in pages:
        for t in n.record.database_tables:
            add("REFERENCES", page_id[n.key], table_id[t], 1.0, t, False)

    stats = {"pages": len(pages), "tables": len(table_id), "edges": len(edge_rows),
             "by_label": {lbl: sum(1 for r in edge_rows if r[3] == lbl) for lbl in EDGE_LABELS.values()},
             "ambiguous_edges": sum(1 for e in graph.edges if e.ambiguous),
             "shared_includes": len(graph.includes), "modules": len({n.module for n in pages})}
    return {"nodes": node_rows, "cards": card_rows, "edges": edge_rows, "stats": stats}


def write_kb(*, url: str, schema: str, kb_version: str, gear_id: str, node_rows: Sequence[tuple],
             card_rows: Sequence[tuple], edge_rows: Sequence[tuple], stats: dict[str, Any],
             activate: bool = False) -> None:
    """Replace one kb_version in the fe_kb_* tables, in one transaction."""
    import psycopg  # noqa: PLC0415

    sch = _schema(schema)
    with psycopg.connect(url, connect_timeout=10) as conn, conn.cursor() as cur:
        for tbl in ("fe_kb_evidence", "fe_kb_edges", "fe_kb_cards", "fe_kb_nodes", "fe_kb_versions"):
            cur.execute(f"DELETE FROM {sch}.{tbl} WHERE kb_version = %s", (kb_version,))
        cur.execute(
            f"INSERT INTO {sch}.fe_kb_versions (kb_version, gear_id, status, build_date, coverage_stats, graph_node_count, graph_edge_count) "
            f"VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s)",
            (kb_version, gear_id, "STAGING", datetime.now(timezone.utc), json.dumps(stats, default=str), len(node_rows), len(edge_rows)),
        )
        if node_rows:
            cur.executemany(
                f"INSERT INTO {sch}.fe_kb_nodes (id, kb_version, kind, label, category, source_locus, confidence, metadata) "
                f"VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb)", list(node_rows))
        if card_rows:
            cur.executemany(
                f"INSERT INTO {sch}.fe_kb_cards (id, kb_version, kind, label, category, source_locus, confidence, text_en, metadata) "
                f"VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)", list(card_rows))
        if edge_rows:
            cur.executemany(
                f"INSERT INTO {sch}.fe_kb_edges (kb_version, from_id, to_id, label, tag, confidence, metadata) "
                f"VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb) ON CONFLICT (kb_version, label, from_id, to_id) DO NOTHING",
                list(edge_rows))
        if activate:
            cur.execute(f"UPDATE {sch}.fe_kb_versions SET status = 'SUPERSEDED' WHERE gear_id = %s AND status = 'ACTIVE' AND kb_version <> %s",
                        (gear_id, kb_version))
            cur.execute(f"UPDATE {sch}.fe_kb_versions SET status = 'ACTIVE' WHERE kb_version = %s", (kb_version,))
        conn.commit()


def graph_export(rows: dict[str, Any]) -> dict[str, Any]:
    """The published nodes / edges as plain JSON (what consumers outside the database get)."""
    return {
        "nodes": [{"id": r[0], "kind": r[2], "label": r[3], "category": r[4], "path": r[5], "metadata": json.loads(r[7])} for r in rows["nodes"]],
        "edges": [{"source": r[1], "target": r[2], "label": r[3], "tag": r[4], "confidence": r[5], "metadata": json.loads(r[6])} for r in rows["edges"]],
        "stats": rows["stats"],
    }


def persist_version_files(*, raw: bytes, rows: dict[str, Any], kb_version: str, gear: str | None, number: int,
                          source_name: str, store: Any = None, key_root: str | None = None) -> dict[str, Any]:
    """Keep one version's files in the artifact store, next to the card builds: the upload itself,
    a ``graph.json`` export of what was published, and a ``summary.json``.

    Key layout follows ``artifact_key``: ``<prefix>/_library/<gear>/deps/v<N>/<artifact_id>/…`` plus the
    store's ``manifest.json`` (sha256 per file). With FE_ARTIFACT_STORE=s3 that is the bucket; with
    ``local`` it is FE_ARTIFACT_ROOT, so the same code runs on a machine without the bucket.
    Best effort: a failure is returned as ``{"ok": false, "error": …}`` and never blocks the publish.
    The returned record is stored in ``fe_kb_versions.coverage_stats.storage``.
    """
    import tempfile  # noqa: PLC0415
    import uuid  # noqa: PLC0415
    from pathlib import Path  # noqa: PLC0415

    from app.agentic_platform.fe_core.artifacts.store import artifact_key, get_artifact_store  # noqa: PLC0415
    from app.agentic_platform.fe_core.config import get_settings  # noqa: PLC0415

    settings = get_settings()
    kind = getattr(settings, "fe_artifact_store", "local")
    try:
        store = store or get_artifact_store(settings)
        kind = store.kind
        artifact_id = f"deps-{uuid.uuid4().hex[:8]}"
        key_prefix = artifact_key(key_root or settings.fe_s3_prefix or "fe", LIBRARY_GEAR_PREFIX, (gear or "").strip() or None,
                                  "deps", number, artifact_id)
        upload_name = Path(source_name or "red_analysis.json").name or "red_analysis.json"
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / upload_name).write_bytes(raw)
            (d / "graph.json").write_text(json.dumps(graph_export(rows), ensure_ascii=False), encoding="utf-8")
            (d / "summary.json").write_text(json.dumps({
                "kb_version": kb_version, "gear": gear or "", "version": number, "source": upload_name,
                "built_at": datetime.now(timezone.utc).isoformat(), **rows["stats"]}, ensure_ascii=False, indent=2), encoding="utf-8")
            manifest = store.put_tree(d, key_prefix, artifact_id=artifact_id, artifact_type="deps", version=number,
                                      project_id=LIBRARY_GEAR_PREFIX, workspace_id=(gear or "").strip() or None)
        record = {"ok": True, "store": kind, "key_prefix": key_prefix, "uri": store.uri(key_prefix), "artifact_id": artifact_id,
                  "files": [f.path for f in manifest.files], "bytes": sum(f.size for f in manifest.files),
                  "tree_sha256": manifest.tree_sha256}
        logger.info("kb_graph.persist_version_files %s -> %s", kb_version, record["uri"])
        return record
    except Exception as exc:  # noqa: BLE001
        logger.warning("kb_graph.persist_version_files: %s not stored (%s): %s", kb_version, kind, exc)
        return {"ok": False, "store": kind, "error": str(exc)[:300]}


def publish_derived(graph: DerivedGraph, *, kb_version: str, gear_id: str, code: str, source: str,
                    url: str | None = None, schema: str | None = None, activate: bool = False) -> dict[str, Any]:
    url = url or kb_db_url()
    if not url:
        raise RuntimeError("no KB database configured (FE_DB_URL, or PG_CONNECTION_MODE=local + PG_LOCAL_URL)")
    schema = schema or kb_schema()
    rows = rows_from_derived(graph, kb_version=kb_version, code=code, source=source)
    write_kb(url=url, schema=schema, kb_version=kb_version, gear_id=gear_id, node_rows=rows["nodes"],
             card_rows=rows["cards"], edge_rows=rows["edges"], stats=rows["stats"], activate=activate)
    out = {**rows["stats"], "kb_version": kb_version, "gear_id": gear_id, "schema": schema,
           "status": "ACTIVE" if activate else "STAGING"}
    logger.info("kb_graph.publish_derived %s", out)
    return out


# ----------------------------------------------------------------------------- read
class KbGraphDao:
    """Reads one kb_version of the dependency graph from fe_kb_nodes / fe_kb_edges."""

    def __init__(self, url: str, schema: str | None = None):
        if not url:
            raise ValueError("KbGraphDao needs a database URL")
        self.url = url
        self.schema = _schema(schema or kb_schema())
        self._pool: Any = None

    def _ensure_pool(self):
        if self._pool is None:
            from psycopg.rows import dict_row  # noqa: PLC0415
            from psycopg_pool import ConnectionPool  # noqa: PLC0415

            self._pool = ConnectionPool(self.url, min_size=1, max_size=4, timeout=15,
                                        kwargs={"row_factory": dict_row, "connect_timeout": 10}, open=True)
        return self._pool

    def _t(self, name: str) -> str:
        return f"{self.schema}.{name}"

    def _rows(self, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
        with self._ensure_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()

    def version(self, kb_version: str) -> dict[str, Any] | None:
        rows = self._rows(f"SELECT kb_version, gear_id, status, build_date, coverage_stats, graph_node_count, graph_edge_count "
                          f"FROM {self._t('fe_kb_versions')} WHERE kb_version = %s", (kb_version,))
        return rows[0] if rows else None

    # -- versions of one series (all rows whose kb_version starts with the series prefix) -------
    def versions(self, prefix: str) -> list[dict[str, Any]]:
        """Every version of a series, oldest first. Matching on the kb_version prefix also picks up
        rows written before the series had its own gear id."""
        return self._rows(f"SELECT kb_version, gear_id, status, build_date, coverage_stats, graph_node_count, graph_edge_count "
                          f"FROM {self._t('fe_kb_versions')} WHERE kb_version LIKE %s ESCAPE '\\' ORDER BY build_date, kb_version",
                          (_like(prefix),))

    def active_version(self, prefix: str) -> str | None:
        rows = self._rows(f"SELECT kb_version FROM {self._t('fe_kb_versions')} WHERE kb_version LIKE %s ESCAPE '\\' "
                          f"AND status = 'ACTIVE' ORDER BY build_date DESC LIMIT 1", (_like(prefix),))
        return rows[0]["kb_version"] if rows else None

    def latest_version(self, prefix: str, pattern: re.Pattern[str] = DEPS_VERSION_RE) -> str | None:
        rows = self.versions(prefix)
        numbered = [(version_number(r["kb_version"], pattern) or 0, r["kb_version"]) for r in rows]
        return max(numbered)[1] if numbered else None

    def next_version_number(self, prefix: str, pattern: re.Pattern[str] = DEPS_VERSION_RE) -> int:
        nums = [version_number(r["kb_version"], pattern) or 0 for r in self.versions(prefix)]
        return (max(nums) + 1) if nums else 1

    def update_version_stats(self, kb_version: str, patch: dict[str, Any]) -> None:
        """Merge keys into fe_kb_versions.coverage_stats (storage location, source file, ...)."""
        with self._ensure_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(f"UPDATE {self._t('fe_kb_versions')} SET coverage_stats = COALESCE(coverage_stats, '{{}}'::jsonb) || %s::jsonb "
                        f"WHERE kb_version = %s", (json.dumps(patch, default=str), kb_version))
            conn.commit()

    def promote(self, kb_version: str, gear_id: str, prefix: str) -> None:
        """Make ``kb_version`` the series' current version: it becomes ACTIVE under ``gear_id`` and
        every other ACTIVE row of the series becomes SUPERSEDED. Works as a rollback too."""
        with self._ensure_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(f"UPDATE {self._t('fe_kb_versions')} SET status = 'SUPERSEDED' "
                        f"WHERE kb_version LIKE %s ESCAPE '\\' AND status = 'ACTIVE' AND kb_version <> %s", (_like(prefix), kb_version))
            cur.execute(f"UPDATE {self._t('fe_kb_versions')} SET status = 'ACTIVE', gear_id = %s WHERE kb_version = %s", (gear_id, kb_version))
            conn.commit()

    def nodes(self, kb_version: str, kinds: Sequence[str] = (PAGE_KIND,)) -> list[dict[str, Any]]:
        return self._rows(f"SELECT id, kind, label, category, source_locus, confidence, metadata FROM {self._t('fe_kb_nodes')} "
                          f"WHERE kb_version = %s AND kind = ANY(%s) ORDER BY label, id", (kb_version, list(kinds)))

    def edges(self, kb_version: str, labels: Sequence[str] = PAGE_EDGE_LABELS) -> list[dict[str, Any]]:
        return self._rows(f"SELECT kb_edge_id AS id, from_id, to_id, label, tag, confidence, metadata FROM {self._t('fe_kb_edges')} "
                          f"WHERE kb_version = %s AND label = ANY(%s) ORDER BY from_id, to_id", (kb_version, list(labels)))

    def node(self, kb_version: str, node_id: str) -> dict[str, Any] | None:
        rows = self._rows(f"SELECT id, kind, label, category, source_locus, confidence, metadata FROM {self._t('fe_kb_nodes')} "
                          f"WHERE kb_version = %s AND id = %s", (kb_version, node_id))
        return rows[0] if rows else None

    def node_edges(self, kb_version: str, node_id: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        rows = self._rows(
            f"""
            SELECT e.kb_edge_id AS id, e.from_id, e.to_id, e.label, e.tag, e.confidence, e.metadata,
                   o.id AS other_id, o.kind AS other_kind, o.label AS other_label, o.category AS other_category
            FROM {self._t('fe_kb_edges')} e
            JOIN {self._t('fe_kb_nodes')} o ON o.kb_version = e.kb_version
                 AND o.id = CASE WHEN e.from_id = %s THEN e.to_id ELSE e.from_id END
            WHERE e.kb_version = %s AND (e.from_id = %s OR e.to_id = %s)
            ORDER BY o.kind, o.label
            """, (node_id, kb_version, node_id, node_id))
        return [r for r in rows if r["from_id"] == node_id], [r for r in rows if r["to_id"] == node_id]

    def search(self, kb_version: str, q: str, limit: int = 20) -> list[dict[str, Any]]:
        like = f"%{q}%"
        return self._rows(f"SELECT id, label, category, source_locus FROM {self._t('fe_kb_nodes')} WHERE kb_version = %s AND kind = %s "
                          f"AND (label ILIKE %s OR source_locus ILIKE %s) ORDER BY (lower(label) = lower(%s)) DESC, label LIMIT %s",
                          (kb_version, PAGE_KIND, like, like, q, limit))

    def module_matrix(self, kb_version: str, labels: Sequence[str] = PAGE_EDGE_LABELS) -> dict[str, Any]:
        mods = self._rows(f"SELECT COALESCE(category, '') AS module, count(*) AS files, "
                          f"COALESCE(sum((metadata->>'loc')::int), 0) AS loc FROM {self._t('fe_kb_nodes')} "
                          f"WHERE kb_version = %s AND kind = %s GROUP BY 1 ORDER BY 1", (kb_version, PAGE_KIND))
        pairs = self._rows(
            f"""
            SELECT COALESCE(s.category, '') AS source, COALESCE(t.category, '') AS target, count(*) AS count
            FROM {self._t('fe_kb_edges')} e
            JOIN {self._t('fe_kb_nodes')} s ON s.kb_version = e.kb_version AND s.id = e.from_id AND s.kind = %s
            JOIN {self._t('fe_kb_nodes')} t ON t.kb_version = e.kb_version AND t.id = e.to_id AND t.kind = %s
            WHERE e.kb_version = %s AND e.label = ANY(%s)
            GROUP BY 1, 2 ORDER BY 3 DESC
            """, (PAGE_KIND, PAGE_KIND, kb_version, list(labels)))
        return {"modules": mods, "pairs": pairs}

    def edges_between(self, kb_version: str, module_a: str, module_b: str,
                      labels: Sequence[str] = PAGE_EDGE_LABELS) -> list[dict[str, Any]]:
        return self._rows(
            f"""
            SELECT e.kb_edge_id AS id, e.label, e.tag, e.confidence, e.metadata,
                   s.id AS source_id, s.label AS source_label, t.id AS target_id, t.label AS target_label
            FROM {self._t('fe_kb_edges')} e
            JOIN {self._t('fe_kb_nodes')} s ON s.kb_version = e.kb_version AND s.id = e.from_id AND s.kind = %s
            JOIN {self._t('fe_kb_nodes')} t ON t.kb_version = e.kb_version AND t.id = e.to_id AND t.kind = %s
            WHERE e.kb_version = %s AND e.label = ANY(%s) AND COALESCE(s.category, '') = %s AND COALESCE(t.category, '') = %s
            ORDER BY t.label, s.label
            """, (PAGE_KIND, PAGE_KIND, kb_version, list(labels), module_a, module_b))

    # -- traversal, one query per hop ---------------------------------------------------
    def _expand(self, kb_version: str, frontier: list[str], direction: str, labels: Sequence[str],
                kinds: Sequence[str]) -> list[dict[str, Any]]:
        parts, params = [], [kb_version, list(labels)]
        if direction in ("down", "both"):
            parts.append("(e.from_id = ANY(%s) AND t.kind = ANY(%s))")
            params += [frontier, list(kinds)]
        if direction in ("up", "both"):
            parts.append("(e.to_id = ANY(%s) AND s.kind = ANY(%s))")
            params += [frontier, list(kinds)]
        return self._rows(
            f"""
            SELECT e.kb_edge_id AS id, e.from_id, e.to_id, e.label, e.tag, e.confidence, e.metadata
            FROM {self._t('fe_kb_edges')} e
            JOIN {self._t('fe_kb_nodes')} s ON s.kb_version = e.kb_version AND s.id = e.from_id
            JOIN {self._t('fe_kb_nodes')} t ON t.kb_version = e.kb_version AND t.id = e.to_id
            WHERE e.kb_version = %s AND e.label = ANY(%s) AND ({' OR '.join(parts)})
            """, params)

    def traverse(self, kb_version: str, start_id: str, *, direction: str = "down", max_depth: int = 2,
                 labels: Sequence[str] = PAGE_EDGE_LABELS, kinds: Sequence[str] = (PAGE_KIND,),
                 exclude: Iterable[str] = ()) -> dict[str, Any]:
        max_depth = max(0, min(int(max_depth), MAX_DEPTH))
        excluded = set(exclude)
        depth: dict[str, int] = {start_id: 0}
        parent: dict[str, str | None] = {start_id: None}
        via: dict[str, int | None] = {start_id: None}
        edges: dict[int, dict[str, Any]] = {}
        frontier = [start_id]
        for d in range(1, max_depth + 1):
            if not frontier:
                break
            nxt: list[str] = []
            for e in self._expand(kb_version, frontier, direction, labels, kinds):
                if direction == "down" or (direction == "both" and e["from_id"] in depth and depth[e["from_id"]] == d - 1):
                    here, other = e["from_id"], e["to_id"]
                else:
                    here, other = e["to_id"], e["from_id"]
                if here not in depth or other in excluded:
                    continue
                edges.setdefault(e["id"], e)
                if other not in depth:
                    depth[other] = d
                    parent[other] = here
                    via[other] = e["id"]
                    nxt.append(other)
            frontier = nxt
        nodes = self._nodes_by_id(kb_version, list(depth))
        for n in nodes:
            n["depth"] = depth[n["id"]]
            n["parent_id"] = parent.get(n["id"])
            n["via_edge_id"] = via.get(n["id"])
        nodes.sort(key=lambda n: (n["depth"], n["label"]))
        return {"start": start_id, "direction": direction, "depth": max_depth, "nodes": nodes,
                "edges": [e for e in edges.values() if e["from_id"] in depth and e["to_id"] in depth]}

    def shortest_path(self, kb_version: str, from_id: str, to_id: str, *, labels: Sequence[str] = PAGE_EDGE_LABELS,
                      kinds: Sequence[str] = (PAGE_KIND,), max_depth: int = MAX_DEPTH,
                      exclude: Iterable[str] = ()) -> dict[str, Any]:
        excluded = set(exclude)
        for direction in ("down", "up", "both"):
            res = self._bfs_path(kb_version, from_id, to_id, direction, labels, kinds, max_depth, excluded)
            if res is not None:
                ids, edge_rows = res
                nodes = self._nodes_by_id(kb_version, ids)
                order = {nid: i for i, nid in enumerate(ids)}
                nodes.sort(key=lambda n: order[n["id"]])
                return {"from": from_id, "to": to_id, "found": True, "direction": direction, "hops": len(ids) - 1,
                        "nodes": nodes, "edges": edge_rows}
        return {"from": from_id, "to": to_id, "found": False, "direction": None, "hops": 0, "nodes": [], "edges": []}

    def _bfs_path(self, kb_version, from_id, to_id, direction, labels, kinds, max_depth, excluded):
        parent: dict[str, tuple[str | None, dict | None]] = {from_id: (None, None)}
        frontier = [from_id]
        for _ in range(max_depth):
            if not frontier or to_id in parent:
                break
            nxt: list[str] = []
            for e in self._expand(kb_version, frontier, direction, labels, kinds):
                if direction == "down":
                    here, other = e["from_id"], e["to_id"]
                elif direction == "up":
                    here, other = e["to_id"], e["from_id"]
                else:
                    here, other = (e["from_id"], e["to_id"]) if e["from_id"] in parent else (e["to_id"], e["from_id"])
                if here not in parent or other in parent or other in excluded:
                    continue
                parent[other] = (here, e)
                nxt.append(other)
            frontier = nxt
        if to_id not in parent:
            return None
        ids: list[str] = []
        edges: list[dict] = []
        cur: str | None = to_id
        while cur is not None:
            ids.append(cur)
            prev, e = parent[cur]
            if e is not None:
                edges.append(e)
            cur = prev
        ids.reverse()
        edges.reverse()
        return ids, edges

    def _nodes_by_id(self, kb_version: str, ids: list[str]) -> list[dict[str, Any]]:
        if not ids:
            return []
        return self._rows(f"SELECT id, kind, label, category, source_locus, confidence, metadata FROM {self._t('fe_kb_nodes')} "
                          f"WHERE kb_version = %s AND id = ANY(%s)", (kb_version, ids))


# ----------------------------------------------------------------------------- DTOs
def submodule_of(path: str, module: str) -> str:
    """Folders between the module folder and the file: ``…/03-business-line/App/x.asp`` -> ``App``.
    Empty when the file sits directly in the module folder or the module is not on the path."""
    parts = [p for p in (path or "").replace("\\", "/").split("/") if p]
    if len(parts) < 3 or not module:
        return ""
    try:
        i = parts.index(module)
    except ValueError:
        return ""
    return "/".join(parts[i + 1:-1])


def node_dto(row: dict[str, Any], full: bool = False) -> dict[str, Any]:
    m = row.get("metadata") or {}
    path = m.get("path") or row.get("source_locus") or ""
    module = row.get("category") or m.get("module") or ""
    dto = {
        "id": row["id"], "kind": row.get("kind"), "file": row.get("label"), "module": module, "submodule": submodule_of(path, module),
        "path": path, "layer": m.get("layer"), "risk": m.get("risk_level"),
        "loc": m.get("loc") or 0, "complexity": m.get("complexity_score") or 0, "cyclomatic": m.get("cyclomatic_complexity") or 0,
        "domain": m.get("business_domain"), "rules": m.get("business_rule_count") or 0, "security": m.get("security_counts") or {},
    }
    for k in ("depth", "parent_id", "via_edge_id"):
        if k in row:
            dto[k] = row[k]
    if full:
        dto.update({"purpose": m.get("summary") or "", "tables": m.get("database_tables") or [], "includes": m.get("includes") or [],
                    "modernization_approach": m.get("modernization_approach"), "estimated_effort": m.get("estimated_effort")})
    return dto


def edge_dto(row: dict[str, Any]) -> dict[str, Any]:
    m = row.get("metadata") or {}
    return {"id": row["id"], "source": row["from_id"], "target": row["to_id"], "label": row["label"], "tag": row.get("tag"),
            "confidence": float(row["confidence"]) if row.get("confidence") is not None else None,
            "evidence": m.get("evidence") or "", "ambiguous": bool(m.get("ambiguous"))}


def group_pairs(nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> dict[tuple[str, str], int]:
    mod = {n["id"]: n.get("category") or "" for n in nodes}
    out: dict[tuple[str, str], int] = defaultdict(int)
    for e in edges:
        out[(mod.get(e["from_id"], ""), mod.get(e["to_id"], ""))] += 1
    return dict(out)
