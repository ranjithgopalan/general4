"""Chunking, retrieval and the per-project chat box.

Scoped by project throughout, and by workspace within a project. A question asked
inside EPIC 3 must not retrieve EPIC 7's chunks -- the same isolation the artefact
tables enforce (FR-P4), applied to the retrieval corpus.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.agentic_platform.api.security import current_principal
from app.agentic_platform.fe_core.auth.roles import Principal
from app.agentic_platform.fe_core.config import get_settings
from app.agentic_platform.fe_core.store import get_store

logger = logging.getLogger(__name__)
router = APIRouter(tags=["rag"])


def _store():
    """The chunk store, or a 503 that says what to configure."""
    from app.agentic_platform.fe_core.rag.retriever import ChunkStore

    settings = get_settings()
    if not settings.uses_database():
        raise HTTPException(
            status_code=503,
            detail=(
                "retrieval needs Postgres: the corpus lives in the kb_chunk table "
                "with an HNSW index, which the JSON file store cannot provide. Set "
                "FE_DB_URL and run `python run.py db-apply`."
            ),
        )
    return ChunkStore(settings.fe_db_url, settings.fe_db_schema)


def _readable_workspaces(project_id: str, workspace_id: str | None) -> list[str] | None:
    """Which workspaces a question may draw on. None means the whole project."""
    if not workspace_id:
        return None
    from app.agentic_platform.fe_core.workspaces.service import WorkspaceService

    ids = WorkspaceService(get_store()).readable_workspace_ids(workspace_id)
    return ids or [workspace_id]


class AskRequest(BaseModel):
    question: str = Field(min_length=3)
    workspace_id: str | None = Field(
        default=None,
        description=(
            "Scopes the answer to that workspace plus its parent Global tier. "
            "Omit to search the whole project."
        ),
    )
    conversation_id: str | None = None
    route: bool = Field(
        default=True,
        description=(
            "Let a cheap model narrow which artefact types to search. Disable to "
            "search every type -- slower, and a long document type can crowd out "
            "the one that answers."
        ),
    )


@router.post("/projects/{project_id}/chat")
async def ask(
    project_id: str,
    body: AskRequest = Body(...),
    principal: Principal = Depends(current_principal),
) -> dict:
    """Ask a question about one project, answered only from its indexed documents.

    Returns `refused: true` with an explanation when nothing clears the relevance
    floor. That is a result, not an error: answering from an unrelated excerpt is
    how invented requirements get quoted back as if they came from the project.
    """
    from app.agentic_platform.fe_core.rag.chat import ChatService

    service = ChatService(_store(), get_settings().fe_db_schema)
    try:
        return service.ask(
            project_id,
            body.question,
            workspace_ids=_readable_workspaces(project_id, body.workspace_id),
            conversation_id=body.conversation_id,
            asked_by=principal.email or principal.subject,
            route=body.route,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("Chat failed for %s", project_id)
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/projects/{project_id}/chat")
async def chat_history(
    project_id: str,
    conversation_id: str | None = None,
    limit: int = Query(default=50, le=200),
    principal: Principal = Depends(current_principal),
) -> dict:
    from app.agentic_platform.fe_core.rag.chat import ChatService

    service = ChatService(_store(), get_settings().fe_db_schema)
    messages = service.history(project_id, conversation_id, limit)
    return {"project_id": project_id, "count": len(messages), "items": messages}


@router.get("/projects/{project_id}/index")
async def index_status(
    project_id: str,
    principal: Principal = Depends(current_principal),
) -> dict:
    """What is indexed, and how much still needs embedding.

    `awaiting_embedding` above zero is not a fault: chunks are stored before they
    are embedded, so a Bedrock outage leaves them lexically searchable and
    completable later.
    """
    from app.agentic_platform.fe_core.rag.bedrock import credential_status

    store = _store()
    counts = store.counts(project_id)
    creds = credential_status()
    return {
        "project_id": project_id,
        **counts,
        "embeddings": {
            "model": "amazon.titan-embed-text-v2:0",
            "dimensions": 1024,
            "credentials": creds["detail"],
            "usable": creds.get("expired") is not True and bool(creds["source"]),
        },
    }


class IndexRequest(BaseModel):
    artifact_ids: list[str] | None = Field(
        default=None,
        description="Omit to index every APPROVED artefact in the project.",
    )
    include_drafts: bool = Field(
        default=False,
        description=(
            "Drafts are excluded by default: an unapproved document answering a "
            "question as though it were settled is the failure the approval gate "
            "exists to prevent."
        ),
    )
    embed_now: bool = True


@router.post("/projects/{project_id}/index", status_code=202)
async def index_project(
    project_id: str,
    body: IndexRequest = Body(default=IndexRequest()),
    principal: Principal = Depends(current_principal),
) -> dict:
    """Chunk and embed a project's artefacts."""
    from app.agentic_platform.fe_core.kb.models import ArtifactStatus
    from app.agentic_platform.fe_core.rag.indexer import Indexer

    store = get_store()
    indexer = Indexer(_store())

    if body.artifact_ids:
        artifacts = [a for a in (store.get_artifact(i) for i in body.artifact_ids)
                     if a is not None]
        missing = set(body.artifact_ids) - {a.id for a in artifacts}
        if missing:
            raise HTTPException(status_code=404,
                                detail=f"unknown artefact(s): {sorted(missing)}")
    else:
        artifacts = [
            a for a in store.list_artifacts(_unscoped=True)
            if a.kb_application_id == project_id
            and (body.include_drafts or a.status is ArtifactStatus.APPROVED)
        ]

    if not artifacts:
        raise HTTPException(
            status_code=422,
            detail=(
                f"no artefacts to index for {project_id}. Approved artefacts are "
                "indexed by default; pass include_drafts to index Drafts too."
            ),
        )

    reports = [indexer.index_artifact(a, embed_now=body.embed_now).to_dict()
               for a in artifacts]
    return {
        "project_id": project_id,
        "artifacts": len(reports),
        "chunks_written": sum(r["chunks_written"] for r in reports),
        "embedded": sum(r["embedded"] for r in reports),
        "awaiting_embedding": sum(r["awaiting_embedding"] for r in reports),
        "items": reports,
    }


@router.post("/projects/{project_id}/index/embed", status_code=202)
async def drain_embeddings(
    project_id: str,
    principal: Principal = Depends(current_principal),
) -> dict:
    """Embed the backlog. The recovery path after a Bedrock outage."""
    from app.agentic_platform.fe_core.rag.bedrock import BedrockUnavailableError
    from app.agentic_platform.fe_core.rag.indexer import Indexer

    try:
        # Off the event loop: a batch of Titan calls takes minutes and blocked
        # every other request (runs, SSE, the console) while it ran.
        embedded = await asyncio.to_thread(Indexer(_store()).embed_pending, project_id)
    except BedrockUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"project_id": project_id, "embedded": embedded,
            **_store().counts(project_id)}
