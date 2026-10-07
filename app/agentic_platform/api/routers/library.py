"""The global card library — upload once, every project selects from it.

The library is the reserved project `_library` (fe_core.kb.library) running the
one-stage `kb-library` pipeline. This router is its front door:

    POST /library/documents   add or replace a module JSON, a screens document, or a RED
                              file-analysis JSON (document_kind=red-analysis: page dependencies)
    POST /library/build       run the KB agent on what has been uploaded
    GET  /library             what is uploaded, the last build, the approved build
    GET  /library/pages       the ASP-file -> card mapping of a build
    GET  /library/graph...    the page / module dependency graph of a Gear ID, read from the
                              fe_kb_* tables (matrix, pair drill-down, node detail, traversal)

A build is approved like any other run (POST /pipeline-runs/{run_id}/approvals).
Projects read approved builds only; they upload ASP files with
POST /projects/{id}/asp-source. The library's cards can be browsed with the
existing KB endpoints under /projects/_library/kb/...
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile

from app.agentic_platform.api.security import current_principal
from app.agentic_platform.fe_core.auth.roles import Principal
from app.agentic_platform.fe_core.config import get_settings
from app.agentic_platform.fe_core.kb import library
from app.agentic_platform.fe_core.store import get_store

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/library", tags=["library"])

_ACTIVE_STATES = {"queued", "running"}


def _workspace():
    """The library's Global Workspace, opened on first use."""
    from app.agentic_platform.fe_core.workspaces.service import WorkspaceService  # noqa: PLC0415

    return WorkspaceService(get_store(), library.LIBRARY_PIPELINE).ensure_global(
        library.LIBRARY_APP_ID, library.LIBRARY_PIPELINE)


def _state(run) -> str:
    return getattr(run.state, "value", str(run.state))


def _runs(workspace_id: str) -> list:
    return get_store().list_runs(library.LIBRARY_PIPELINE, library.LIBRARY_STAGE,
                                 workspace_id=workspace_id)


def _corpus_view(settings) -> tuple[dict, float]:
    """What has been uploaded, and the newest modification time among those files."""
    from app.agentic_platform.api.routers.projects import _stored_modules  # noqa: PLC0415
    from app.agentic_platform.fe_core.kb.cards_schema import iter_all_cards, module_key_of  # noqa: PLC0415

    corpus = settings.corpus_root_for(library.LIBRARY_APP_ID)
    newest = 0.0
    modules: list[dict] = []
    for path, doc in _stored_modules(corpus / "re-cards"):
        newest = max(newest, path.stat().st_mtime)
        modules.append({
            "module_key": module_key_of(doc, path.stem),
            "module": doc.get("module", ""),
            "file": path.name,
            "format": doc.get("source_format") or "cards-json",
            "pages": [p.get("page", "") for p in doc.get("pages", [])],
            "cards": len(iter_all_cards(doc)),
        })
    screens: list[dict] = []
    screens_root = corpus / "screens"
    if screens_root.is_dir():
        for path in screens_root.glob("catalog-v*.json"):
            try:
                cat = json.loads(path.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue
            newest = max(newest, path.stat().st_mtime)
            screens.append({
                "version": cat.get("version"),
                "source_filename": cat.get("source_filename", ""),
                "pages": sum(1 for sc in cat.get("screens", []) if sc.get("images")),
                "unbound": len(cat.get("unbound", [])),
                "report": cat.get("report", []),
            })
    screens.sort(key=lambda s: s.get("version") or 0)
    return {"modules": modules, "screens": screens}, newest


def _status() -> dict:
    settings = get_settings()
    workspace = _workspace()
    corpus, newest = _corpus_view(settings)
    runs = _runs(workspace.id)
    latest = runs[0] if runs else None
    latest_build = None
    built_at = 0.0
    if latest is not None:
        stamp = latest.queued_at or latest.started_at
        built_at = stamp.timestamp() if stamp else 0.0
        latest_build = {
            "run_id": latest.run_id,
            "state": _state(latest),
            "queued_at": latest.queued_at.isoformat() if latest.queued_at else None,
            "finished_at": latest.finished_at.isoformat() if getattr(latest, "finished_at", None) else None,
            "error": getattr(latest, "error", None),
        }
    artifact = library.approved_library_artifact(get_store())
    approved = None
    if artifact is not None:
        approved = {
            "artifact_id": artifact.id,
            "version": artifact.version,
            "approved_by": getattr(artifact, "approved_by", None),
            "approved_at": artifact.approved_at.isoformat() if getattr(artifact, "approved_at", None) else None,
        }
    return {
        "library_id": library.LIBRARY_APP_ID,
        "workspace_id": workspace.id,
        "pipeline": library.LIBRARY_PIPELINE,
        "corpus": corpus,
        # Uploaded since the last build was queued: build again to include it.
        "dirty": bool(corpus["modules"]) and newest > built_at,
        "latest_build": latest_build,
        "approved": approved,
    }


@router.get("")
async def library_status(principal: Principal = Depends(current_principal)) -> dict:
    """What is in the library, the last build, and the build projects are using."""
    return await asyncio.to_thread(_status)


@router.post("/documents", status_code=202)
async def upload_library_document(
    file: UploadFile = File(...),
    document_kind: str = Form(
        description="re-cards (a module's JSON, RED JSON layout) | screens (the screens Word document) | "
                    "red-analysis (the RED per-file analysis JSON: page dependencies)"),
    gear_id: str | None = Form(default=None, description="Gear ID the upload belongs to"),
    activate: bool | None = Form(
        default=None,
        description="red-analysis only: make the new version current. Default: only when the Gear ID has no current version yet"),
    principal: Principal = Depends(current_principal),
) -> dict:
    """Add or replace one library input. Nothing is built until POST /library/build.

    `re-cards`: validated on its own and against the modules already uploaded;
    a clash of card ids or ASP page names is refused with the list. A module
    uploaded again replaces its earlier file.
    `screens`: pictures are extracted and bound to ASP pages.
    `red-analysis`: the RED per-file analysis export (result.files[]). Page-to-page
    references are derived and written to the fe_kb_* tables as a NEW version of the
    Gear ID's dependency graph (`_library-<gear>-deps-v<N>`); earlier versions are
    kept. `activate` makes it current; GET /library/graph reads the current one.
    """
    from app.agentic_platform.api.routers.projects import _handle_re_cards, _handle_screens  # noqa: PLC0415

    if document_kind not in ("re-cards", "screens", "red-analysis"):
        raise HTTPException(
            status_code=422,
            detail="the library takes document_kind 're-cards' (module JSON), 'screens' (Word document) "
                   "or 'red-analysis' (RED per-file analysis JSON)")
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=422, detail="the uploaded file is empty")
    if document_kind == "red-analysis":
        return await asyncio.to_thread(_handle_red_analysis, raw, file.filename or "red_analysis.json", gear_id, activate)

    workspace = await asyncio.to_thread(_workspace)
    handler = _handle_re_cards if document_kind == "re-cards" else _handle_screens
    # emit_event=False: an upload must not start a pipeline thread for the library.
    reply = await handler(
        raw=raw, file=file, project_id=library.LIBRARY_APP_ID, workspace=workspace,
        store_=get_store(), principal=principal, emit_event=False,
    )
    reply["library"] = {"build": "POST /library/build", "note": "uploaded; build the library to use it"}
    return reply


@router.post("/build", status_code=202)
async def build_library(principal: Principal = Depends(current_principal)) -> dict:
    """Run the KB agent on the uploaded inputs. Approve the run to publish it to projects."""
    from app.agentic_platform.api.services.jobs import JobService  # noqa: PLC0415
    from app.agentic_platform.fe_core.pipeline.eligibility import StageBlockedError  # noqa: PLC0415
    from app.agentic_platform.fe_core.pipeline.registry import PipelineNotFoundError, get_pipeline  # noqa: PLC0415

    settings = get_settings()
    workspace = await asyncio.to_thread(_workspace)
    corpus, _newest = await asyncio.to_thread(_corpus_view, settings)
    if not corpus["modules"]:
        raise HTTPException(
            status_code=409,
            detail="the library has no module JSON yet; upload one with document_kind=re-cards first")
    active = [r for r in _runs(workspace.id) if _state(r) in _ACTIVE_STATES]
    if active:
        raise HTTPException(
            status_code=409,
            detail=f"a library build is already {_state(active[0])} (run {active[0].run_id})")

    try:
        service = JobService(pipeline=get_pipeline(library.LIBRARY_PIPELINE))
        # force: the stage has usually completed before; a rebuild is the point.
        run = await service.create_run(
            library.LIBRARY_STAGE, workspace_id=workspace.id,
            initiated_by=principal.email or principal.subject, force=True,
        )
    except PipelineNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except StageBlockedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "run": run.model_dump(mode="json"),
        "events": f"{settings.fe_api_prefix}/pipeline-runs/{run.run_id}/events",
        "approve": f"{settings.fe_api_prefix}/pipeline-runs/{run.run_id}/approvals",
    }


def _page_index(source: str) -> tuple[dict, dict]:
    """(page_index, build info) for the approved build, or for the latest built one."""
    settings = get_settings()
    if source == "latest":
        kb = library.locate_library_kb(settings.kb_root_for(library.LIBRARY_APP_ID))
        if kb is None:
            raise HTTPException(status_code=404, detail="the library has not been built yet")
        info = {"source": "latest", "path": str(kb)}
    else:
        ref = library.materialise_library(get_store(), settings)
        if ref is None:
            raise HTTPException(
                status_code=404,
                detail="no approved library build yet; use source=latest to see an unapproved build")
        kb = Path(ref["path"])
        info = {"source": "approved", "version": ref["version"], "artifact_id": ref["artifact_id"]}
    page_index, _merged = library.load_library(kb)
    return page_index, info


@router.get("/pages")
async def library_pages(
    q: str | None = Query(default=None, description="Filter by part of the ASP file name."),
    module_key: str | None = None,
    source: str = Query(default="approved", pattern="^(approved|latest)$",
                        description="approved = the build projects use; latest = the last build, approved or not."),
    principal: Principal = Depends(current_principal),
) -> dict:
    """The ASP-file to card mapping: for each page, its cards, shared entities and pictures."""
    page_index, info = await asyncio.to_thread(_page_index, source)
    needle = (q or "").strip().lower()
    pages = [
        {"page_norm": norm, **entry}
        for norm, entry in page_index.get("pages", {}).items()
        if (not needle or needle in norm) and (not module_key or entry.get("module_key") == module_key)
    ]
    return {
        "build": info,
        "count": len(pages),
        "pages": pages,
        "modules": page_index.get("modules", []),
        "pages_without_screenshot": page_index.get("pages_without_screenshot", []),
        "pictures_without_screen_card": page_index.get("pictures_without_screen_card", []),
        "catalog_pages_without_cards": page_index.get("catalog_pages_without_cards", []),
        "unbound_pictures": page_index.get("unbound_pictures", 0),
    }


# ---------------------------------------------------------------------------
# Dependency graph (fe_kb_* tables, one version per Gear ID)
# ---------------------------------------------------------------------------

def _handle_red_analysis(raw: bytes, filename: str, gear_id: str | None, activate: bool | None = None) -> dict:
    """Derive page-to-page edges from a RED per-file analysis export and publish them as a new version."""
    from app.agentic_platform.fe_core.red_graph import kb_graph  # noqa: PLC0415
    from app.agentic_platform.fe_core.red_graph.derive import derive_from_rows  # noqa: PLC0415

    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=422, detail=f"red-analysis file is not valid JSON: {exc}") from exc
    files = None
    if isinstance(data, dict):
        files = (data.get("result") or {}).get("files") if isinstance(data.get("result"), dict) else None
        files = files or data.get("files")
    elif isinstance(data, list):
        files = data
    if not files or not isinstance(files, list) or not all(isinstance(f, dict) for f in files):
        raise HTTPException(status_code=422, detail="red-analysis JSON must hold result.files[] (one entry per analysed file)")
    graph = derive_from_rows(files)
    if not graph.nodes:
        raise HTTPException(status_code=422, detail="no analysed files with a file_path were found in the JSON")

    dao = _kb_dao()
    prefix = kb_graph.library_deps_prefix(gear_id)
    number = dao.next_version_number(prefix)
    kb_version = kb_graph.library_deps_version(gear_id, number)
    previous_current = dao.active_version(prefix)
    make_current = activate if activate is not None else previous_current is None
    source_name = Path(filename).name
    deps_gear_id = kb_graph.library_deps_gear_id(gear_id)
    try:
        # 1. Database: the new version's rows (STAGING until made current).
        rows = kb_graph.rows_from_derived(graph, kb_version=kb_version, code=kb_graph.code_for(gear_id or "library"), source=source_name)
        kb_graph.write_kb(url=dao.url, schema=dao.schema, kb_version=kb_version, gear_id=deps_gear_id, node_rows=rows["nodes"],
                          card_rows=rows["cards"], edge_rows=rows["edges"], stats={**rows["stats"], "source": source_name})
        stats = {**rows["stats"], "kb_version": kb_version, "gear_id": deps_gear_id, "schema": dao.schema}
        # 2. Artifact store (S3 at the client, local folder here): upload + graph.json + summary.json + manifest.
        storage = kb_graph.persist_version_files(raw=raw, rows=rows, kb_version=kb_version, gear=gear_id, number=number,
                                                 source_name=source_name)
        dao.update_version_stats(kb_version, {"storage": storage, "source": source_name})
        stats["storage"] = storage
        # 3. Current pointer.
        if make_current:
            dao.promote(kb_version, deps_gear_id, prefix)
    except Exception as exc:  # noqa: BLE001
        logger.exception("red-analysis publish failed")
        raise HTTPException(status_code=500, detail=f"could not write the dependency graph: {exc}") from exc

    # Keep the upload beside the other library inputs on local disk too (best effort), one file per version.
    try:
        corpus_dir = get_settings().corpus_root_for(library.LIBRARY_APP_ID) / "red-analysis"
        corpus_dir.mkdir(parents=True, exist_ok=True)
        (corpus_dir / f"{kb_version}.json").write_bytes(raw)
        stats["stored"] = True
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not store the red-analysis upload: %s", exc)
        stats["stored"] = False
    stats.update({"document_kind": "red-analysis", "gear": gear_id or "", "version": number,
                  "is_current": make_current, "previous_current": previous_current,
                  "status": "ACTIVE" if make_current else "STAGING",
                  "unresolved_page_refs": sum(graph.unresolved_pages.values()),
                  "library": {"graph": f"{get_settings().fe_api_prefix}/library/graph?gear_id={gear_id or ''}",
                              "activate": f"{get_settings().fe_api_prefix}/library/graph/versions/{number}/activate?gear_id={gear_id or ''}"}})
    return stats


def _deps_version_row(dao, gear_id: str | None, version: str | None) -> dict:
    """Resolve the dependency-graph version to read: an explicit number / kb_version, else the
    current (ACTIVE) one, else the newest. 404 when the Gear ID has none."""
    from app.agentic_platform.fe_core.red_graph import kb_graph  # noqa: PLC0415

    prefix = kb_graph.library_deps_prefix(gear_id)
    if version:
        v = version.strip()
        if v.lower().startswith("v") and v[1:].isdigit():
            v = v[1:]
        kb_version = kb_graph.library_deps_version(gear_id, int(v)) if v.isdigit() else v
        row = dao.version(kb_version)
        if row is None or not kb_version.startswith(prefix):
            raise HTTPException(status_code=404, detail=f"Gear ID '{gear_id or ''}' has no dependency graph version '{version}'")
        return row
    kb_version = dao.active_version(prefix) or dao.latest_version(prefix)
    if kb_version is None:
        raise HTTPException(
            status_code=404,
            detail=f"no dependency graph for Gear ID '{gear_id or ''}' yet; upload the RED file-analysis JSON "
                   f"(document_kind=red-analysis) to build it")
    return dao.version(kb_version)


def _version_dto(row: dict, current: str | None) -> dict:
    from app.agentic_platform.fe_core.red_graph import kb_graph  # noqa: PLC0415

    stats = row.get("coverage_stats") or {}
    return {"kb_version": row["kb_version"], "version": kb_graph.version_number(row["kb_version"]), "gear_id": row.get("gear_id"),
            "status": row.get("status"), "is_current": row["kb_version"] == current,
            "built_at": row["build_date"].isoformat() if row.get("build_date") else None,
            "pages": stats.get("pages"), "edges": stats.get("edges"), "modules": stats.get("modules"),
            "nodes_total": row.get("graph_node_count"), "edges_total": row.get("graph_edge_count"),
            "source": stats.get("source"), "storage": stats.get("storage")}


def _kb_dao():
    from app.agentic_platform.fe_core.red_graph import kb_graph  # noqa: PLC0415

    url = kb_graph.kb_db_url()
    if not url:
        raise HTTPException(status_code=503, detail="no KB database configured (FE_DB_URL, or PG_CONNECTION_MODE=local + PG_LOCAL_URL)")
    return _cached_kb_dao(url, kb_graph.kb_schema())


@lru_cache(maxsize=2)
def _cached_kb_dao(url: str, schema: str):
    """One connection pool per (url, schema) for the process."""
    from app.agentic_platform.fe_core.red_graph.kb_graph import KbGraphDao  # noqa: PLC0415

    return KbGraphDao(url, schema)


def _graph_version(gear_id: str | None, version: str | None = None) -> tuple[str, Any]:
    dao = _kb_dao()
    row = _deps_version_row(dao, gear_id, version)
    return row["kb_version"], dao


def _ids(param: str | None) -> list[str]:
    return [x.strip() for x in (param or "").split(",") if x.strip()]


@router.get("/graph/gears")
def library_graph_gears(principal: Principal = Depends(current_principal)) -> dict:
    """The Gear IDs that have a dependency graph, from fe_kb_versions, with each one's current version."""
    from app.agentic_platform.fe_core.red_graph import kb_graph  # noqa: PLC0415

    dao = _kb_dao()
    rows = dao.versions(f"{kb_graph.LIBRARY_GEAR_PREFIX}-")
    gears: dict[str, dict] = {}
    for r in rows:
        kv = r["kb_version"]
        m = re.match(rf"^{re.escape(kb_graph.LIBRARY_GEAR_PREFIX)}-(.+)-deps-v(\d+)$", kv)
        if not m:
            continue
        g = gears.setdefault(m.group(1), {"gear_id": m.group(1), "versions": 0, "current": None, "latest": None})
        g["versions"] += 1
        g["latest"] = kv
        if r["status"] == "ACTIVE":
            g["current"] = kv
    return {"items": sorted(gears.values(), key=lambda g: g["gear_id"])}


@router.get("/graph/versions")
def library_graph_versions(gear_id: str | None = None, principal: Principal = Depends(current_principal)) -> dict:
    """Every dependency-graph version of a Gear ID, oldest first, with the current one flagged."""
    from app.agentic_platform.fe_core.red_graph import kb_graph  # noqa: PLC0415

    dao = _kb_dao()
    prefix = kb_graph.library_deps_prefix(gear_id)
    current = dao.active_version(prefix)
    items = [_version_dto(r, current) for r in dao.versions(prefix)]
    return {"gear_id": gear_id or "", "current": current, "latest": dao.latest_version(prefix), "items": items}


@router.post("/graph/versions/{version}/activate")
def library_graph_activate(version: str, gear_id: str | None = None, principal: Principal = Depends(current_principal)) -> dict:
    """Make a dependency-graph version the current one for the Gear ID (also the rollback path)."""
    from app.agentic_platform.fe_core.red_graph import kb_graph  # noqa: PLC0415

    dao = _kb_dao()
    row = _deps_version_row(dao, gear_id, version)
    prefix = kb_graph.library_deps_prefix(gear_id)
    previous = dao.active_version(prefix)
    dao.promote(row["kb_version"], kb_graph.library_deps_gear_id(gear_id), prefix)
    return {"gear_id": gear_id or "", "current": row["kb_version"], "version": kb_graph.version_number(row["kb_version"]),
            "previous_current": previous, "changed": previous != row["kb_version"]}


@router.get("/versions")
def library_versions(gear_id: str | None = None, principal: Principal = Depends(current_principal)) -> dict:
    """All KB versions behind a Gear ID's library: the card builds (``<app>-re-v<N>``, promoted by
    approval) and the dependency-graph uploads (``_library-<gear>-deps-v<N>``)."""
    from app.agentic_platform.fe_core.red_graph import kb_graph  # noqa: PLC0415

    dao = _kb_dao()
    deps_prefix = kb_graph.library_deps_prefix(gear_id)
    deps_current = dao.active_version(deps_prefix)
    cards_prefix = f"{library.LIBRARY_APP_ID}-re-v"
    cards_current = dao.active_version(cards_prefix)
    cards = [{**_version_dto(r, cards_current), "version": kb_graph.version_number(r["kb_version"], kb_graph.CARDS_VERSION_RE)}
             for r in dao.versions(cards_prefix)]
    return {"gear_id": gear_id or "",
            "cards": {"current": cards_current, "items": cards,
                      "note": "card builds are published per application (_library) and promoted on approval"},
            "dependency_graph": {"current": deps_current, "items": [_version_dto(r, deps_current) for r in dao.versions(deps_prefix)]}}


@router.get("/graph")
def library_graph(gear_id: str | None = None, version: str | None = None, principal: Principal = Depends(current_principal)) -> dict:
    """Page nodes, page-to-page edges and the module matrix of a Gear ID's dependency graph
    (the current version unless ``version`` names another one)."""
    from app.agentic_platform.fe_core.red_graph import kb_graph  # noqa: PLC0415
    from app.agentic_platform.fe_core.red_graph.kb_graph import edge_dto, node_dto  # noqa: PLC0415

    kb_version, dao = _graph_version(gear_id, version)
    v = dao.version(kb_version) or {}
    prefix = kb_graph.library_deps_prefix(gear_id)
    current = dao.active_version(prefix)
    return {
        "gear_id": gear_id or "", "kb_version": kb_version, "version": kb_graph.version_number(kb_version),
        "is_current": kb_version == current, "current": current, "status": v.get("status"),
        "built_at": v.get("build_date").isoformat() if v.get("build_date") else None,
        "stats": v.get("coverage_stats") or {},
        "nodes": [node_dto(r) for r in dao.nodes(kb_version)],
        "edges": [edge_dto(r) for r in dao.edges(kb_version)],
        **dao.module_matrix(kb_version),
    }


@router.get("/graph/modules/{module_a}/{module_b}")
def library_graph_pair(module_a: str, module_b: str, gear_id: str | None = None, version: str | None = None,
                       principal: Principal = Depends(current_principal)) -> dict:
    kb_version, dao = _graph_version(gear_id, version)
    rows = dao.edges_between(kb_version, module_a, module_b)
    return {"gear_id": gear_id or "", "source_module": module_a, "target_module": module_b, "count": len(rows),
            "edges": [{"id": r["id"], "label": r["label"], "tag": r["tag"], "evidence": (r.get("metadata") or {}).get("evidence") or "",
                       "confidence": float(r["confidence"]) if r.get("confidence") is not None else None,
                       "source": {"id": r["source_id"], "file": r["source_label"]},
                       "target": {"id": r["target_id"], "file": r["target_label"]}} for r in rows]}


@router.get("/graph/search")
def library_graph_search(q: str = Query(min_length=1), gear_id: str | None = None, version: str | None = None,
                         limit: int = Query(default=20, ge=1, le=200), principal: Principal = Depends(current_principal)) -> dict:
    kb_version, dao = _graph_version(gear_id, version)
    return {"gear_id": gear_id or "", "query": q,
            "items": [{"id": r["id"], "file": r["label"], "module": r["category"] or "", "path": r["source_locus"] or ""}
                      for r in dao.search(kb_version, q, limit=limit)]}


@router.get("/graph/nodes/{node_id}")
def library_graph_node(node_id: str, gear_id: str | None = None, version: str | None = None,
                       principal: Principal = Depends(current_principal)) -> dict:
    from app.agentic_platform.fe_core.red_graph.kb_graph import edge_dto, node_dto  # noqa: PLC0415

    kb_version, dao = _graph_version(gear_id, version)
    row = dao.node(kb_version, node_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"node '{node_id}' is not in the dependency graph of Gear ID '{gear_id or ''}'")
    out, inc = dao.node_edges(kb_version, node_id)

    def other(r):
        return {**edge_dto(r), "other": {"id": r["other_id"], "kind": r["other_kind"], "file": r["other_label"],
                                          "module": r["other_category"] or ""}}

    # The Global Library's cards for this page (KB tables > approved build > latest build > uploaded module JSONs).
    try:
        from app.agentic_platform.fe_core.red_graph.library_cards import cards_for_page  # noqa: PLC0415

        cards = cards_for_page(get_settings(), get_store(), row["label"] or "", dao)
    except Exception as exc:  # noqa: BLE001
        logger.warning("library cards lookup failed for %s: %s", row.get("label"), exc)
        cards = None
    return {"gear_id": gear_id or "", "node": node_dto(row, full=True),
            "outgoing": [other(r) for r in out], "incoming": [other(r) for r in inc], "library": cards}


@router.get("/graph/traverse")
def library_graph_traverse(start: str, gear_id: str | None = None, version: str | None = None,
                           direction: str = Query(default="down", pattern="^(down|up|both)$"),
                           depth: int = Query(default=2, ge=1, le=8), exclude: str | None = None,
                           principal: Principal = Depends(current_principal)) -> dict:
    """Walk the edge table from ``start``: down = pages it uses, up = pages that use it."""
    from app.agentic_platform.fe_core.red_graph.kb_graph import edge_dto, node_dto  # noqa: PLC0415

    kb_version, dao = _graph_version(gear_id, version)
    if dao.node(kb_version, start) is None:
        raise HTTPException(status_code=404, detail=f"node '{start}' is not in the dependency graph")
    res = dao.traverse(kb_version, start, direction=direction, max_depth=depth, exclude=_ids(exclude))
    return {"gear_id": gear_id or "", "start": start, "direction": direction, "depth": depth,
            "nodes": [node_dto(n) for n in res["nodes"]], "edges": [edge_dto(e) for e in res["edges"]]}


@router.get("/graph/path")
def library_graph_path(from_id: str = Query(alias="from"), to_id: str = Query(alias="to"), gear_id: str | None = None,
                       version: str | None = None, exclude: str | None = None,
                       principal: Principal = Depends(current_principal)) -> dict:
    from app.agentic_platform.fe_core.red_graph.kb_graph import edge_dto, node_dto  # noqa: PLC0415

    kb_version, dao = _graph_version(gear_id, version)
    for nid in (from_id, to_id):
        if dao.node(kb_version, nid) is None:
            raise HTTPException(status_code=404, detail=f"node '{nid}' is not in the dependency graph")
    res = dao.shortest_path(kb_version, from_id, to_id, exclude=_ids(exclude))
    return {"gear_id": gear_id or "", "from": from_id, "to": to_id, "found": res["found"], "direction": res["direction"],
            "hops": res["hops"], "nodes": [node_dto(n) for n in res["nodes"]], "edges": [edge_dto(e) for e in res["edges"]]}


@router.get("/pages/{page}")
async def library_page(
    page: str,
    source: str = Query(default="approved", pattern="^(approved|latest)$"),
    principal: Principal = Depends(current_principal),
) -> dict:
    """One ASP page of the library. The name is matched ignoring case and folder."""
    from app.agentic_platform.fe_core.kb.cards_schema import normalise_page  # noqa: PLC0415

    page_index, info = await asyncio.to_thread(_page_index, source)
    norm = normalise_page(page)
    entry = page_index.get("pages", {}).get(norm)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"the library has no cards for '{page}'")
    return {"build": info, "page_norm": norm, **entry}
