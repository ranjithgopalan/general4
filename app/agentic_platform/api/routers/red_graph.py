"""RED code graph — page-to-page dependencies read from lmod's ``graph_nodes`` / ``graph_edges``.

The graph is built from ``red_file_analyses`` (see ``fe_core.red_graph.build``) and every read
here is a SQL query against those tables, so the UI shows what the database holds, and the
traversal endpoints walk the table hop by hop.

    GET  /red-graph/projects                         projects with analyses / built graphs
    POST /red-graph/{project}/build                  (re)build the graph from red_file_analyses
    GET  /red-graph/{project}/graph                  page nodes + page->page edges + module matrix
    GET  /red-graph/{project}/modules                module file counts + module->module edge counts
    GET  /red-graph/{project}/modules/{a}/{b}        the file edges behind one module pair
    GET  /red-graph/{project}/nodes?q=               search pages by name / path
    GET  /red-graph/{project}/nodes/{id}             one page with its in/out edges, tables, includes
    GET  /red-graph/{project}/traverse?start=&direction=down|up|both&depth=&exclude=
    GET  /red-graph/{project}/path?from=&to=&exclude=
    GET  /red-graph/ui                               the interactive map (served HTML)
"""
from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse

from app.agentic_platform.api.security import current_principal
from app.agentic_platform.fe_core.auth.roles import Principal
from app.agentic_platform.fe_core.config import get_settings
from app.agentic_platform.fe_core.red_graph.dao import PAGE_RELS, RedGraphDao
from app.agentic_platform.fe_core.red_graph.derive import LABEL_PAGE, REL_CALLS, REL_USES

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/red-graph", tags=["red-graph"])

_UI_FILE = Path(__file__).with_name("red_graph_ui.html")
_ALLOWED_RELS = {REL_USES, REL_CALLS}


@lru_cache(maxsize=1)
def _cached_dao(url: str, schema: str) -> RedGraphDao:
    return RedGraphDao(url, schema)


def get_dao() -> RedGraphDao:
    from app.agentic_platform.fe_core.red_graph.build import red_graph_db_url  # noqa: PLC0415

    url = red_graph_db_url()
    if not url:
        raise HTTPException(status_code=503, detail="RED graph database is not configured; set PG_CONNECTION_MODE=local "
                                                    "+ PG_LOCAL_URL in app/.env, or RED_DB_URL / FE_DB_URL")
    return _cached_dao(url, get_settings().red_db_schema)


def _rels(param: str | None) -> list[str]:
    if not param:
        return list(PAGE_RELS)
    rels = [r.strip().upper() for r in param.split(",") if r.strip()]
    bad = [r for r in rels if r not in _ALLOWED_RELS]
    if bad:
        raise HTTPException(status_code=422, detail=f"unknown rel_types {bad}; use {sorted(_ALLOWED_RELS)}")
    return rels


def _ids(param: str | None) -> list[int]:
    if not param:
        return []
    try:
        return [int(x) for x in param.split(",") if x.strip()]
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="exclude must be a comma-separated list of node ids") from exc


def _node_dto(row: dict[str, Any], full: bool = False) -> dict[str, Any]:
    p = row.get("properties") or {}
    path = row.get("path") or ""
    dto = {
        "id": row["id"],
        "label": row.get("label"),
        "name": row.get("name"),
        "file": p.get("file_name") or (path.replace("\\", "/").rsplit("/", 1)[-1] if path else row.get("name")),
        "path": path,
        "module": p.get("module") or "",
        "layer": p.get("layer"),
        "risk": p.get("risk_level"),
        "loc": p.get("loc") or 0,
        "complexity": p.get("complexity_score") or 0,
        "cyclomatic": p.get("cyclomatic_complexity") or 0,
        "domain": p.get("business_domain") or row.get("domain"),
        "rules": p.get("business_rule_count") or 0,
        "security": p.get("security_counts") or {},
    }
    for k in ("depth", "parent_id", "via_edge_id"):
        if k in row:
            dto[k] = row[k]
    if full:
        dto["purpose"] = p.get("purpose") or ""
        dto["tables"] = p.get("database_tables") or []
        dto["includes"] = p.get("includes") or []
        dto["modernization_approach"] = p.get("modernization_approach")
        dto["estimated_effort"] = p.get("estimated_effort")
    return dto


def _edge_dto(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "source": row["source_id"],
        "target": row["target_id"],
        "rel_type": row["rel_type"],
        "evidence": row.get("source_locator") or "",
        "confidence": float(row["confidence"]) if row.get("confidence") is not None else None,
    }


# --------------------------------------------------------------------------- endpoints
@router.get("/projects")
def list_projects(dao: RedGraphDao = Depends(get_dao), principal: Principal = Depends(current_principal)) -> dict:
    return {"items": dao.list_projects()}


@router.post("/{project_id}/build")
def build_project(project_id: str, json_path: str | None = Body(default=None, embed=True),
                  dao: RedGraphDao = Depends(get_dao), principal: Principal = Depends(current_principal)) -> dict:
    """Derive page-to-page edges from ``red_file_analyses`` and write them to the graph tables.
    ``json_path`` (a RED export on the server) is for projects whose analyses are not in the table."""
    from app.agentic_platform.fe_core.red_graph.build import build  # noqa: PLC0415

    try:
        return build(project_id, json_path=json_path, dao=dao)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{project_id}/graph")
def project_graph(project_id: str, rel_types: str | None = None,
                  dao: RedGraphDao = Depends(get_dao), principal: Principal = Depends(current_principal)) -> dict:
    rels = _rels(rel_types)
    nodes = [_node_dto(r) for r in dao.nodes(project_id)]
    if not nodes:
        raise HTTPException(status_code=404, detail=f"no page nodes for project {project_id!r}; build it first")
    edges = [_edge_dto(r) for r in dao.edges(project_id, rel_types=rels)]
    return {"project_id": project_id, "rel_types": rels, "nodes": nodes, "edges": edges,
            "modules": dao.module_matrix(project_id, rel_types=rels)}


@router.get("/{project_id}/modules")
def project_modules(project_id: str, rel_types: str | None = None,
                    dao: RedGraphDao = Depends(get_dao), principal: Principal = Depends(current_principal)) -> dict:
    return {"project_id": project_id, **dao.module_matrix(project_id, rel_types=_rels(rel_types))}


@router.get("/{project_id}/modules/{module_a}/{module_b}")
def module_pair(project_id: str, module_a: str, module_b: str, rel_types: str | None = None,
                dao: RedGraphDao = Depends(get_dao), principal: Principal = Depends(current_principal)) -> dict:
    rows = dao.edges_between(project_id, module_a, module_b, rel_types=_rels(rel_types))
    return {"project_id": project_id, "source_module": module_a, "target_module": module_b, "count": len(rows),
            "edges": [{"id": r["id"], "rel_type": r["rel_type"], "evidence": r["source_locator"] or "",
                       "confidence": float(r["confidence"]) if r["confidence"] is not None else None,
                       "source": {"id": r["source_id"], "name": r["source_name"], "path": r["source_path"]},
                       "target": {"id": r["target_id"], "name": r["target_name"], "path": r["target_path"]}} for r in rows]}


@router.get("/{project_id}/nodes")
def search_nodes(project_id: str, q: str = Query(min_length=1), limit: int = Query(default=20, ge=1, le=200),
                 dao: RedGraphDao = Depends(get_dao), principal: Principal = Depends(current_principal)) -> dict:
    rows = dao.search(project_id, q, limit=limit)
    return {"project_id": project_id, "query": q,
            "items": [{"id": r["id"], "name": r["name"], "path": r["path"], "module": r["module"]} for r in rows]}


@router.get("/{project_id}/nodes/{node_id}")
def node_detail(project_id: str, node_id: int,
                dao: RedGraphDao = Depends(get_dao), principal: Principal = Depends(current_principal)) -> dict:
    row = dao.node(project_id, node_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"node {node_id} not found in project {project_id!r}")
    out, inc = dao.node_edges(project_id, node_id)

    def other(r: dict[str, Any]) -> dict[str, Any]:
        return {**_edge_dto(r), "other": {"id": r["other_id"], "label": r["other_label"], "name": r["other_name"],
                                          "path": r["other_path"], "module": r["other_module"]}}

    return {"project_id": project_id, "node": _node_dto(row, full=True),
            "outgoing": [other(r) for r in out], "incoming": [other(r) for r in inc]}


@router.get("/{project_id}/traverse")
def traverse(project_id: str, start: int, direction: str = Query(default="down", pattern="^(down|up|both)$"),
             depth: int = Query(default=2, ge=1, le=8), rel_types: str | None = None, exclude: str | None = None,
             dao: RedGraphDao = Depends(get_dao), principal: Principal = Depends(current_principal)) -> dict:
    """Walk the graph table from ``start``: ``down`` = pages this page uses, ``up`` = pages that use it."""
    if dao.node(project_id, start) is None:
        raise HTTPException(status_code=404, detail=f"node {start} not found in project {project_id!r}")
    res = dao.traverse(project_id, start, direction=direction, max_depth=depth, rel_types=_rels(rel_types),
                       labels=(LABEL_PAGE,), exclude=_ids(exclude))
    return {"project_id": project_id, "start": start, "direction": direction, "depth": depth,
            "nodes": [_node_dto(n) for n in res["nodes"]], "edges": [_edge_dto(e) for e in res["edges"]]}


@router.get("/{project_id}/path")
def shortest_path(project_id: str, from_id: int = Query(alias="from"), to_id: int = Query(alias="to"),
                  rel_types: str | None = None, exclude: str | None = None,
                  dao: RedGraphDao = Depends(get_dao), principal: Principal = Depends(current_principal)) -> dict:
    for nid in (from_id, to_id):
        if dao.node(project_id, nid) is None:
            raise HTTPException(status_code=404, detail=f"node {nid} not found in project {project_id!r}")
    res = dao.shortest_path(project_id, from_id, to_id, rel_types=_rels(rel_types), labels=(LABEL_PAGE,),
                            exclude=_ids(exclude))
    return {"project_id": project_id, "from": from_id, "to": to_id, "found": res["found"], "direction": res["direction"],
            "hops": res["hops"], "nodes": [_node_dto(n) for n in res["nodes"]], "edges": [_edge_dto(e) for e in res["edges"]]}


@router.get("/ui", response_class=HTMLResponse, include_in_schema=False)
def ui_page() -> HTMLResponse:
    """The interactive dependency map. It calls the JSON endpoints above relative to its own URL."""
    if not _UI_FILE.exists():
        raise HTTPException(status_code=404, detail="UI page is missing from the deployment")
    return HTMLResponse(_UI_FILE.read_text(encoding="utf-8"))
