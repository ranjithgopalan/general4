"""Catalog endpoints: KB applications, plugins, pipeline stages.

Paths follow PRD 9.1. Read-only apart from document upload.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File

from app.agentic_platform.api.security import current_principal
from app.agentic_platform.fe_core.auth.roles import Principal
from app.agentic_platform.fe_core.config import get_settings
from app.agentic_platform.fe_core.kb.gateway import build_gateway
from app.agentic_platform.fe_core.pipeline.eligibility import pipeline_status
from app.agentic_platform.fe_core.pipeline.registry import (
    PipelineNotFoundError,
    get_pipeline,
    registry,
)
from app.agentic_platform.fe_core.plugins import PluginLoadError, discover
from app.agentic_platform.fe_core.store import get_store

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/whoami", tags=["auth"])
async def whoami(principal: Principal = Depends(current_principal)) -> dict:
    """Who the caller is, and whether they may see code artefacts.

    Exposed so a UI can render the right workspace without guessing, and so an
    operator can confirm role mapping without decoding a token by hand.
    """
    return principal.describe()


@router.get("/kb-applications", tags=["kb"])
async def list_kb_applications() -> dict:
    try:
        apps = await build_gateway().resolve_all()  # type: ignore[attr-defined]
    except (AttributeError, Exception) as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"count": len(apps), "items": [a.model_dump(mode="json") for a in apps]}


@router.get("/kb-applications/{app_ref}/context", tags=["kb"])
async def get_context(
    app_ref: str,
    question: str = Query(..., min_length=3),
    policy: str = Query(default="modernization"),
) -> dict:
    """Approved-context retrieval. Transport follows FE_KB_TRANSPORT."""
    gateway = build_gateway()
    try:
        app = await gateway.resolve_application(app_ref)
        package = await gateway.assemble_context(
            question, app_id=app.id, policy=policy
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {
        "kb_application_id": app.id,
        "transport": gateway.transport,
        "policy": policy,
        "context_objects": package.context_objects,
    }


@router.post("/kb-applications/{app_ref}/documents", tags=["kb"], status_code=501)
async def upload_document(app_ref: str, file: UploadFile = File(...)) -> dict:
    """KB document ingestion is not available (REST write-back removed).

    Use POST /api/v1/projects/{id}/documents to upload documents for local indexing.
    """
    raise HTTPException(
        status_code=501,
        detail="Direct KB ingestion is not available. Use POST /api/v1/projects/{id}/documents.",
    )


@router.get("/plugins", tags=["plugins"])
async def list_plugins() -> dict:
    """Available GATHER capabilities and versions (PRD 9.1)."""
    settings = get_settings()
    try:
        found = discover(settings.genlite_plugin_root)
    except PluginLoadError:
        found = {}

    required = set(get_pipeline().required_plugins())
    return {
        "root": str(settings.genlite_plugin_root),
        "count": len(found),
        "items": [
            {
                "name": p.name,
                "version": p.version,
                "skills": list(p.skills),
                "agents": list(p.agents),
                "required_by_active_pipeline": p.name in required,
            }
            for p in sorted(found.values(), key=lambda x: x.name)
        ],
        "required_but_missing": sorted(required - set(found)),
    }


@router.get("/pipelines", tags=["pipeline"])
async def list_pipelines() -> dict:
    return {
        "items": [
            {
                "name": p.name,
                "title": p.title,
                "stages": len(p.stages),
                "kb_application": p.kb_application,
                "required_plugins": p.required_plugins(),
            }
            for p in registry().values()
        ]
    }


@router.get("/pipelines/{name}/stages", tags=["pipeline"])
async def stages(name: str) -> dict:
    try:
        pipeline = get_pipeline(name)
    except PipelineNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    rows = pipeline_status(get_store(), pipeline)
    return {
        "pipeline": pipeline.name,
        "title": pipeline.title,
        "summary": {
            "total": len(rows),
            "ready": sum(1 for r in rows if r["ready"]),
            "gaps": sum(1 for r in rows if r["capability_status"] == "gap"),
            "completed": sum(1 for r in rows if r["state"] == "completed"),
            "waiting_for_approval": sum(
                1 for r in rows if r["state"] == "waiting_for_approval"
            ),
        },
        "stages": rows,
    }
