"""Read-only schema and stored-procedure introspection endpoints (PRD FR-033).

Closes the gap that blocks pipeline stage 9. Nothing in this estate previously
introspected stored procedures over a live connection: the knowledge base's
`DatabaseSchemaImporter` reads tables and columns only, and its SP analysis comes
from parsing `.sql` files and Excel exports.

Every statement issued here passes `fe_core.db.safety.assert_read_only` first, so
a coding mistake cannot turn introspection into a business-data read or a DDL
statement. That is defence in depth, not a substitute for a least-privilege login.

**Point these at a restored copy, not production.** The guard prevents writes; it
cannot prevent load, and a catalogue sweep across a large legacy schema is not
free.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Body, HTTPException, Query
from pydantic import BaseModel, Field

from app.agentic_platform.fe_core.config import get_settings
from app.agentic_platform.fe_core.db.datasource import (
    DatasourceConfigError,
    get_datasource,
    load_datasources,
)
from app.agentic_platform.fe_core.db.introspector import DriverUnavailableError, build_introspector
from app.agentic_platform.fe_core.db.models import SchemaSnapshot

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/schema", tags=["schema"])

#: Snapshots are held in memory per process; they are large and are written to the
#: KB by the caller. Persisting them here would duplicate the artefact store.
_snapshots: dict[str, SchemaSnapshot] = {}


class SnapshotRequest(BaseModel):
    datasource: str = Field(description="Name from datasources.yaml")
    include_procedures: bool = Field(
        default=True,
        description="Include stored-procedure signatures and result shapes.",
    )
    driver: str | None = Field(
        default=None,
        description="Override ODBC driver name. Normally auto-detected, newest first.",
    )


@router.get("/datasources")
async def list_registered_datasources() -> dict:
    """Registered datasources, with credentials absent by construction."""
    settings = get_settings()
    try:
        sources = load_datasources(settings.fe_datasources_file)
    except DatasourceConfigError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {
        "file": str(settings.fe_datasources_file),
        "count": len(sources),
        "items": [d.redacted() for d in sources],
    }


@router.post("/snapshot", status_code=201)
async def capture_snapshot(body: SnapshotRequest = Body(...)) -> dict:
    """Capture a read-only metadata snapshot.

    Returns a summary rather than the full snapshot: a real legacy schema produces
    a payload large enough to be unhelpful in a browser. Fetch the detail from
    `/schema/snapshot/{id}`.
    """
    try:
        datasource = get_datasource(body.datasource)
    except DatasourceConfigError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    if not datasource.is_configured():
        raise HTTPException(
            status_code=409,
            detail=(
                f"datasource '{datasource.name}' has no host/database configured. "
                "Set the environment variables its datasources.yaml entry references."
            ),
        )
    if not datasource.password_available():
        raise HTTPException(
            status_code=409,
            detail=(
                f"datasource '{datasource.name}' expects its password in "
                f"${{{datasource.password_env}}}, which is unset."
            ),
        )

    try:
        introspector = build_introspector(datasource, driver=body.driver)
    except NotImplementedError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc

    try:
        snapshot = introspector.snapshot(include_procedures=body.include_procedures)
    except DriverUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("Introspection failed for %s", datasource.name)
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    _snapshots[snapshot.snapshot_id] = snapshot
    return {
        "snapshot_id": snapshot.snapshot_id,
        "summary": snapshot.summary(),
        "next": {
            "detail": f"/api/v1/schema/snapshot/{snapshot.snapshot_id}",
            "pom_schema": f"/api/v1/schema/snapshot/{snapshot.snapshot_id}/pom-schema",
        },
        "warning": (
            "Verify stored-procedure parameter DIRECTIONS before generating DAOs. "
            "An OUT parameter registered as IN compiles, runs and silently returns "
            "nothing."
        ),
    }


@router.get("/snapshot/{snapshot_id}")
async def get_snapshot(snapshot_id: str, include_procedures: bool = True) -> dict:
    snapshot = _snapshots.get(snapshot_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail=f"unknown snapshot: {snapshot_id}")
    payload = snapshot.model_dump(mode="json")
    if not include_procedures:
        payload.pop("procedures", None)
    return payload


@router.get("/snapshot/{snapshot_id}/procedures")
async def list_procedures(
    snapshot_id: str,
    undiscoverable_only: bool = Query(
        default=False,
        description="Only procedures whose result shape could not be described.",
    ),
) -> dict:
    """Procedure signatures, and which ones need manual result mapping.

    A procedure that builds its result with dynamic SQL cannot be described by the
    catalogue. That is expected in legacy estates and is not a failure -- but the
    DAO generator must be told, or it will guess.
    """
    snapshot = _snapshots.get(snapshot_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail=f"unknown snapshot: {snapshot_id}")
    procedures = snapshot.procedures
    if undiscoverable_only:
        procedures = [p for p in procedures if not p.result_discoverable]
    return {
        "snapshot_id": snapshot_id,
        "count": len(procedures),
        "undiscoverable": sum(1 for p in snapshot.procedures
                              if not p.result_discoverable),
        "items": [p.model_dump(mode="json") for p in procedures],
    }


@router.get("/snapshot/{snapshot_id}/pom-schema")
async def pom_schema(snapshot_id: str) -> dict:
    """POM-shaped policy input for GATHER-opa.

    `GATHER-opa/agents/prerequisite-validator.md` hard-blocks without a
    `pom-schema.json` containing a `fields` array, so publishing this from the
    snapshot is what unblocks pipeline stage 14.
    """
    snapshot = _snapshots.get(snapshot_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail=f"unknown snapshot: {snapshot_id}")
    return snapshot.to_pom_schema()
