"""Postgres access for the RED code graph (lmod schema).

Tables (owned by lmod, see ``doc/lmod_tables``):
    red_file_analyses   one row per analysed source file (the RED output)
    graph_nodes         id, project_id, label (ASPPage | IncludeFile | Table | ...), name, path,
                        properties jsonb, domain; unique (project_id, label, name, COALESCE(path,''))
    graph_edges         id, project_id, rel_type, source_id, target_id, extractor_id,
                        source_locator, confidence; unique (project_id, rel_type, source_id, target_id)

Writes are idempotent per project: every edge this module creates carries
``extractor_id = 'red_file_analyses'`` and is replaced on rebuild; nodes are upserted by
their unique key so ids stay stable and edges other extractors created keep pointing at them.

Traversal is done hop by hop (one query per level, ``source_id = ANY(frontier)``) rather than
with a recursive CTE: the AIG graph has a 300-page strongly connected component, and a
path-enumerating CTE explodes on it while a frontier walk is bounded by the node count.

Synchronous psycopg 3 over a small pool, like ``kb/pg_sink.py``.
"""
from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Iterable, Sequence

from app.agentic_platform.fe_core.red_graph.derive import (
    LABEL_INCLUDE,
    LABEL_PAGE,
    LABEL_TABLE,
    REL_CALLS,
    REL_REFERENCES,
    REL_USES,
    DerivedGraph,
)

logger = logging.getLogger(__name__)

EXTRACTOR_ID = "red_file_analyses"
PAGE_RELS = (REL_USES, REL_CALLS)
MAX_DEPTH = 8

_ANALYSIS_COLS = (
    "id, project_id, file_name, file_path, file_type, source_language, purpose, business_domain, "
    "layer, loc, complexity_score, cyclomatic_complexity, business_rule_count, security_issue_count, "
    "modernization_approach, estimated_effort, risk_level, entry_points, internal_dependencies, "
    "database_tables, security_concerns, analyzed_at"
)


class RedGraphDao:
    def __init__(self, url: str, schema: str = "lmod"):
        if not url:
            raise ValueError("RED_DB_URL (or FE_DB_URL) is required for the RED graph")
        self.url = url
        self.schema = "".join(c for c in schema if c.isalnum() or c == "_") or "lmod"
        self._pool: Any = None

    # -- connection -----------------------------------------------------------
    def _ensure_pool(self):
        if self._pool is None:
            from psycopg.rows import dict_row  # noqa: PLC0415
            from psycopg_pool import ConnectionPool  # noqa: PLC0415

            self._pool = ConnectionPool(
                self.url, min_size=1, max_size=4, timeout=15,
                kwargs={"row_factory": dict_row, "connect_timeout": 10}, open=True,
            )
        return self._pool

    def _t(self, name: str) -> str:
        return f"{self.schema}.{name}"

    def _rows(self, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
        with self._ensure_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()

    def _one(self, sql: str, params: Sequence[Any] = ()) -> dict[str, Any] | None:
        rows = self._rows(sql, params)
        return rows[0] if rows else None

    # -- source: red_file_analyses -------------------------------------------
    def list_projects(self) -> list[dict[str, Any]]:
        """Projects with RED analyses and/or a built graph, with counts of each."""
        sql = f"""
            WITH a AS (
                SELECT project_id, count(*) AS analyses, max(analyzed_at) AS analyzed_at
                FROM {self._t('red_file_analyses')} GROUP BY project_id
            ), n AS (
                SELECT project_id, count(*) AS pages
                FROM {self._t('graph_nodes')} WHERE label = %s GROUP BY project_id
            ), e AS (
                SELECT project_id, count(*) AS edges, max(updated_at) AS built_at
                FROM {self._t('graph_edges')} WHERE extractor_id = %s GROUP BY project_id
            )
            SELECT COALESCE(a.project_id, n.project_id, e.project_id) AS project_id,
                   COALESCE(a.analyses, 0) AS analyses, a.analyzed_at,
                   COALESCE(n.pages, 0) AS pages, COALESCE(e.edges, 0) AS edges, e.built_at,
                   p.name
            FROM a FULL OUTER JOIN n ON n.project_id = a.project_id
                   FULL OUTER JOIN e ON e.project_id = COALESCE(a.project_id, n.project_id)
                   LEFT JOIN {self._t('projects')} p ON p.id = COALESCE(a.project_id, n.project_id, e.project_id)
            ORDER BY 1
        """
        return self._rows(sql, (LABEL_PAGE, EXTRACTOR_ID))

    def fetch_file_analyses(self, project_id: str) -> list[dict[str, Any]]:
        sql = f"SELECT {_ANALYSIS_COLS} FROM {self._t('red_file_analyses')} WHERE project_id = %s ORDER BY file_path"
        return self._rows(sql, (project_id,))

    # -- write: graph_nodes / graph_edges ------------------------------------
    def upsert_graph(self, project_id: str, graph: DerivedGraph) -> dict[str, Any]:
        """Write the derived graph. One transaction; replaces this extractor's edges."""
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        with self._ensure_pool().connection() as conn, conn.cursor() as cur:
            # Store the path exactly as the analysis recorded it (lmod's indexer keeps Windows
            # backslashes); matching against existing rows is done on the normalised form.
            page_ids = self._upsert_nodes(
                cur, project_id, LABEL_PAGE,
                [(n.name, n.record.file_path, n.properties(), n.record.business_domain) for n in graph.nodes.values()],
            )
            key_to_id = {k: page_ids[(n.name, _norm(n.record.file_path))] for k, n in graph.nodes.items()}

            include_ids = self._upsert_nodes(
                cur, project_id, LABEL_INCLUDE,
                [(name, None, {"language": "asp", "shared": True, "used_by": count}, None) for name, count in graph.includes.items()],
            )
            table_names = sorted({t for n in graph.nodes.values() for t in n.record.database_tables})
            table_ids = self._upsert_nodes(cur, project_id, LABEL_TABLE, [(t, None, {}, None) for t in table_names])

            cur.execute(
                f"DELETE FROM {self._t('graph_edges')} WHERE project_id = %s AND extractor_id = %s",
                (project_id, EXTRACTOR_ID),
            )
            rows: list[tuple] = []
            for e in graph.edges:
                rows.append((project_id, e.rel_type, key_to_id[e.source_key], key_to_id[e.target_key],
                             EXTRACTOR_ID, e.evidence, e.confidence, now, now))
            for node in graph.nodes.values():
                sid = key_to_id[node.key]
                for inc in node.includes:
                    rows.append((project_id, REL_USES, sid, include_ids[(inc, None)], EXTRACTOR_ID, inc, 1.0, now, now))
                for tbl in node.record.database_tables:
                    rows.append((project_id, REL_REFERENCES, sid, table_ids[(tbl, None)], EXTRACTOR_ID, tbl, 1.0, now, now))
            if rows:
                cur.executemany(
                    f"INSERT INTO {self._t('graph_edges')} "
                    f"(project_id, rel_type, source_id, target_id, extractor_id, source_locator, confidence, created_at, updated_at) "
                    f"VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
                    f"ON CONFLICT (project_id, rel_type, source_id, target_id) DO UPDATE SET "
                    f"extractor_id = EXCLUDED.extractor_id, source_locator = EXCLUDED.source_locator, "
                    f"confidence = EXCLUDED.confidence, updated_at = EXCLUDED.updated_at",
                    rows,
                )
            conn.commit()
        stats = graph.summary()
        stats.update({"project_id": project_id, "include_nodes": len(include_ids), "table_nodes": len(table_ids),
                      "edges_written": len(rows)})
        logger.info("red_graph.upsert_graph %s", stats)
        return stats

    def _upsert_nodes(self, cur, project_id: str, label: str,
                      items: Iterable[tuple[str, str | None, dict, str | None]]) -> dict[tuple[str, str | None], int]:
        """Insert nodes missing by (project, label, name, normalised path); merge properties on
        existing ones. The returned map is keyed by (name, normalised path or None)."""
        items = list(items)
        ids: dict[tuple[str, str | None], int] = {}
        if not items:
            return ids
        cur.execute(
            f"SELECT id, name, path FROM {self._t('graph_nodes')} WHERE project_id = %s AND label = %s",
            (project_id, label),
        )
        for r in cur.fetchall():
            ids.setdefault((r["name"], _norm(r["path"])), r["id"])
        for name, path, props, domain in items:
            k = (name, _norm(path))
            if k in ids:
                cur.execute(
                    f"UPDATE {self._t('graph_nodes')} SET properties = COALESCE(properties, '{{}}'::jsonb) || %s::jsonb, "
                    f"domain = COALESCE(%s, domain) WHERE id = %s",
                    (json.dumps(props, ensure_ascii=False), domain, ids[k]),
                )
            else:
                cur.execute(
                    f"INSERT INTO {self._t('graph_nodes')} (project_id, label, name, path, properties, domain) "
                    f"VALUES (%s, %s, %s, %s, %s::jsonb, %s) RETURNING id",
                    (project_id, label, name, path, json.dumps(props, ensure_ascii=False), domain),
                )
                ids[k] = cur.fetchone()["id"]
        return ids

    # -- read ---------------------------------------------------------------
    def nodes(self, project_id: str, labels: Sequence[str] = (LABEL_PAGE,), with_purpose: bool = False) -> list[dict[str, Any]]:
        props = "properties" if with_purpose else "properties - 'purpose' - 'includes' - 'database_tables' AS properties"
        sql = (f"SELECT id, label, name, path, {props}, domain FROM {self._t('graph_nodes')} "
               f"WHERE project_id = %s AND label = ANY(%s) ORDER BY name, path")
        return self._rows(sql, (project_id, list(labels)))

    def edges(self, project_id: str, labels: Sequence[str] = (LABEL_PAGE,),
              rel_types: Sequence[str] | None = None) -> list[dict[str, Any]]:
        sql = f"""
            SELECT e.id, e.rel_type, e.source_id, e.target_id, e.source_locator, e.confidence, e.extractor_id
            FROM {self._t('graph_edges')} e
            JOIN {self._t('graph_nodes')} s ON s.id = e.source_id
            JOIN {self._t('graph_nodes')} t ON t.id = e.target_id
            WHERE e.project_id = %s AND s.label = ANY(%s) AND t.label = ANY(%s)
        """
        params: list[Any] = [project_id, list(labels), list(labels)]
        if rel_types:
            sql += " AND e.rel_type = ANY(%s)"
            params.append(list(rel_types))
        sql += " ORDER BY e.source_id, e.target_id"
        return self._rows(sql, params)

    def node(self, project_id: str, node_id: int) -> dict[str, Any] | None:
        return self._one(
            f"SELECT id, label, name, path, properties, domain FROM {self._t('graph_nodes')} WHERE project_id = %s AND id = %s",
            (project_id, node_id),
        )

    def node_edges(self, project_id: str, node_id: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """(outgoing, incoming) edges of a node, each with the other endpoint's label/name/path/module."""
        sql = f"""
            SELECT e.id, e.rel_type, e.source_id, e.target_id, e.source_locator, e.confidence, e.extractor_id,
                   o.label AS other_label, o.name AS other_name, o.path AS other_path,
                   o.properties->>'module' AS other_module, o.id AS other_id
            FROM {self._t('graph_edges')} e
            JOIN {self._t('graph_nodes')} o ON o.id = CASE WHEN e.source_id = %s THEN e.target_id ELSE e.source_id END
            WHERE e.project_id = %s AND (e.source_id = %s OR e.target_id = %s)
            ORDER BY o.label, o.name
        """
        rows = self._rows(sql, (node_id, project_id, node_id, node_id))
        out = [r for r in rows if r["source_id"] == node_id]
        inc = [r for r in rows if r["target_id"] == node_id]
        return out, inc

    def search(self, project_id: str, q: str, limit: int = 20, labels: Sequence[str] = (LABEL_PAGE,)) -> list[dict[str, Any]]:
        sql = (f"SELECT id, label, name, path, properties->>'module' AS module FROM {self._t('graph_nodes')} "
               f"WHERE project_id = %s AND label = ANY(%s) AND (name ILIKE %s OR path ILIKE %s) "
               f"ORDER BY (lower(name) = lower(%s)) DESC, name LIMIT %s")
        like = f"%{q}%"
        return self._rows(sql, (project_id, list(labels), like, like, q, limit))

    def module_matrix(self, project_id: str, rel_types: Sequence[str] = PAGE_RELS) -> dict[str, Any]:
        """Per-module file counts and module -> module edge counts (pages only), straight from SQL."""
        mods = self._rows(
            f"SELECT properties->>'module' AS module, count(*) AS files, "
            f"COALESCE(sum((properties->>'loc')::int), 0) AS loc "
            f"FROM {self._t('graph_nodes')} WHERE project_id = %s AND label = %s GROUP BY 1 ORDER BY 1",
            (project_id, LABEL_PAGE),
        )
        pairs = self._rows(
            f"""
            SELECT s.properties->>'module' AS source, t.properties->>'module' AS target, count(*) AS count
            FROM {self._t('graph_edges')} e
            JOIN {self._t('graph_nodes')} s ON s.id = e.source_id AND s.label = %s
            JOIN {self._t('graph_nodes')} t ON t.id = e.target_id AND t.label = %s
            WHERE e.project_id = %s AND e.rel_type = ANY(%s)
            GROUP BY 1, 2 ORDER BY 3 DESC
            """,
            (LABEL_PAGE, LABEL_PAGE, project_id, list(rel_types)),
        )
        return {"modules": mods, "pairs": pairs}

    def edges_between(self, project_id: str, module_a: str, module_b: str,
                      rel_types: Sequence[str] = PAGE_RELS) -> list[dict[str, Any]]:
        sql = f"""
            SELECT e.id, e.rel_type, e.source_locator, e.confidence,
                   s.id AS source_id, s.name AS source_name, s.path AS source_path,
                   t.id AS target_id, t.name AS target_name, t.path AS target_path
            FROM {self._t('graph_edges')} e
            JOIN {self._t('graph_nodes')} s ON s.id = e.source_id AND s.label = %s
            JOIN {self._t('graph_nodes')} t ON t.id = e.target_id AND t.label = %s
            WHERE e.project_id = %s AND e.rel_type = ANY(%s)
              AND s.properties->>'module' = %s AND t.properties->>'module' = %s
            ORDER BY t.name, s.name
        """
        return self._rows(sql, (LABEL_PAGE, LABEL_PAGE, project_id, list(rel_types), module_a, module_b))

    # -- traversal (hop by hop) ----------------------------------------------
    def _expand(self, project_id: str, frontier: list[int], direction: str,
                rel_types: Sequence[str], labels: Sequence[str]) -> list[dict[str, Any]]:
        """All edges leaving (down), entering (up) or touching (both) the frontier nodes whose
        other endpoint carries one of ``labels``."""
        parts = []
        if direction in ("down", "both"):
            parts.append(f"(e.source_id = ANY(%s) AND t.label = ANY(%s))")
        if direction in ("up", "both"):
            parts.append(f"(e.target_id = ANY(%s) AND s.label = ANY(%s))")
        sql = f"""
            SELECT e.id, e.rel_type, e.source_id, e.target_id, e.source_locator, e.confidence
            FROM {self._t('graph_edges')} e
            JOIN {self._t('graph_nodes')} s ON s.id = e.source_id
            JOIN {self._t('graph_nodes')} t ON t.id = e.target_id
            WHERE e.project_id = %s AND e.rel_type = ANY(%s) AND ({' OR '.join(parts)})
        """
        params: list[Any] = [project_id, list(rel_types)]
        for _ in parts:
            params += [frontier, list(labels)]
        return self._rows(sql, params)

    def traverse(self, project_id: str, start_id: int, *, direction: str = "down", max_depth: int = 2,
                 rel_types: Sequence[str] = PAGE_RELS, labels: Sequence[str] = (LABEL_PAGE,),
                 exclude: Sequence[int] = ()) -> dict[str, Any]:
        """Breadth-first walk from ``start_id``. Returns every node reached with its depth and the
        parent it was first reached from, plus the edges followed. ``exclude`` drops hub nodes
        (menus, sign-out pages) so they do not connect everything to everything."""
        max_depth = max(0, min(int(max_depth), MAX_DEPTH))
        excluded = set(exclude)
        depth = {start_id: 0}
        parent: dict[int, int | None] = {start_id: None}
        via: dict[int, int | None] = {start_id: None}
        edges: dict[int, dict[str, Any]] = {}
        frontier = [start_id]
        for d in range(1, max_depth + 1):
            if not frontier:
                break
            nxt: list[int] = []
            for e in self._expand(project_id, frontier, direction, rel_types, labels):
                here, other = (e["source_id"], e["target_id"]) if e["source_id"] in depth and depth[e["source_id"]] == d - 1 else (e["target_id"], e["source_id"])
                if direction == "down" and e["source_id"] not in depth:
                    continue
                if direction == "up" and e["target_id"] not in depth:
                    continue
                if other in excluded:
                    continue
                edges.setdefault(e["id"], e)
                if other not in depth:
                    depth[other] = d
                    parent[other] = here
                    via[other] = e["id"]
                    nxt.append(other)
            frontier = nxt
        nodes = self._nodes_by_id(project_id, list(depth))
        for n in nodes:
            n["depth"] = depth[n["id"]]
            n["parent_id"] = parent.get(n["id"])
            n["via_edge_id"] = via.get(n["id"])
        nodes.sort(key=lambda n: (n["depth"], n["name"]))
        edges_list = [e for e in edges.values() if e["source_id"] in depth and e["target_id"] in depth]
        return {"start": start_id, "direction": direction, "depth": max_depth, "nodes": nodes, "edges": edges_list}

    def shortest_path(self, project_id: str, from_id: int, to_id: int, *, rel_types: Sequence[str] = PAGE_RELS,
                      labels: Sequence[str] = (LABEL_PAGE,), max_depth: int = MAX_DEPTH,
                      exclude: Sequence[int] = ()) -> dict[str, Any]:
        """Shortest directed reference chain from -> to. Falls back to the reverse direction,
        then to an undirected chain, and says which one it found."""
        for direction in ("down", "up", "both"):
            res = self._bfs_path(project_id, from_id, to_id, direction, rel_types, labels, max_depth, set(exclude))
            if res is not None:
                node_ids, edge_rows = res
                nodes = self._nodes_by_id(project_id, node_ids)
                order = {nid: i for i, nid in enumerate(node_ids)}
                nodes.sort(key=lambda n: order[n["id"]])
                return {"from": from_id, "to": to_id, "found": True, "direction": direction,
                        "hops": len(node_ids) - 1, "nodes": nodes, "edges": edge_rows}
        return {"from": from_id, "to": to_id, "found": False, "direction": None, "hops": 0, "nodes": [], "edges": []}

    def _bfs_path(self, project_id, from_id, to_id, direction, rel_types, labels, max_depth, excluded):
        parent: dict[int, tuple[int | None, dict | None]] = {from_id: (None, None)}
        frontier = [from_id]
        for _ in range(max_depth):
            if not frontier or to_id in parent:
                break
            nxt: list[int] = []
            for e in self._expand(project_id, frontier, direction, rel_types, labels):
                if direction == "down":
                    here, other = e["source_id"], e["target_id"]
                elif direction == "up":
                    here, other = e["target_id"], e["source_id"]
                else:
                    here, other = (e["source_id"], e["target_id"]) if e["source_id"] in parent else (e["target_id"], e["source_id"])
                if here not in parent or other in parent or other in excluded:
                    continue
                parent[other] = (here, e)
                nxt.append(other)
            frontier = nxt
        if to_id not in parent:
            return None
        ids: list[int] = []
        edges: list[dict] = []
        cur: int | None = to_id
        while cur is not None:
            ids.append(cur)
            prev, e = parent[cur]
            if e is not None:
                edges.append(e)
            cur = prev
        ids.reverse()
        edges.reverse()
        return ids, edges

    def _nodes_by_id(self, project_id: str, ids: list[int]) -> list[dict[str, Any]]:
        if not ids:
            return []
        return self._rows(
            f"SELECT id, label, name, path, properties - 'purpose' - 'includes' - 'database_tables' AS properties, domain "
            f"FROM {self._t('graph_nodes')} WHERE project_id = %s AND id = ANY(%s)",
            (project_id, ids),
        )


def _norm(path: str | None) -> str | None:
    """Path comparison key: forward slashes, trimmed, None when empty."""
    p = (path or "").replace("\\", "/").strip()
    return p or None


def group_edges_by_module(nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> dict[tuple[str, str], int]:
    mod = {n["id"]: (n.get("properties") or {}).get("module", "") for n in nodes}
    out: dict[tuple[str, str], int] = defaultdict(int)
    for e in edges:
        out[(mod.get(e["source_id"], ""), mod.get(e["target_id"], ""))] += 1
    return dict(out)
