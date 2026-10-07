"""API endpoints for artifact relationships (traceability chains).

GET /ws/{workspace_id}/relationships — list all relationships
GET /ws/{workspace_id}/relationships/from/{artifact_id} — outgoing links
GET /ws/{workspace_id}/relationships/to/{artifact_id} — incoming links
POST /ws/{workspace_id}/relationships — create a relationship
DELETE /ws/{workspace_id}/relationships — delete a relationship
"""

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, model_validator

from app.core.dependencies import get_trace_graph_service, get_traceability_service
from app.lifecycle.traceability.graph_service import TraceGraphService
from app.lifecycle.traceability.service import TraceabilityService
from app.utils.logging import log

router = APIRouter(tags=["workspace-traceability"])


class RelationshipCreate(BaseModel):
    """Request body for creating an artifact relationship."""

    source_artifact_id: str = Field(..., description="Source artifact ID (from)")
    target_artifact_id: str = Field(..., description="Target artifact ID (to)")
    relationship_type: str = Field(
        ..., description="Type: derives_from, implements, depends_on, tests"
    )
    description: str | None = Field(
        None, description="Justification text (why they're linked)"
    )


class Relationship(BaseModel):
    """Artifact relationship (outgoing or incoming link)."""

    id: int | None = None
    source_artifact_id: str
    target_artifact_id: str
    relationship_type: str
    description: str | None = None
    created_at: str | None = None

    @model_validator(mode='before')
    @classmethod
    def convert_datetime_to_string(cls, data: Any) -> Any:
        """Convert datetime objects to ISO format strings."""
        if isinstance(data, dict):
            if isinstance(data.get('created_at'), datetime):
                data['created_at'] = data['created_at'].isoformat()
        return data


class RelationshipsResponse(BaseModel):
    """Response for relationship queries."""

    workspace_id: str
    count: int
    relationships: list[Relationship]


@router.get("/ws/{workspace_id}/trace/graph")
async def get_item_trace_graph(
    workspace_id: str,
    tg: TraceGraphService = Depends(get_trace_graph_service),
) -> dict:
    """Item-level trace graph (RTM P2) — requirement → systems → rules → stories → files → tests.

    Returns ``{nodes, edges, meta}`` where nodes are individual items (a specific rule/story/test),
    for the UI to render + highlight forward/backward from any single item.
    """
    try:
        return await tg.build_item_graph(workspace_id)
    except Exception as e:
        log.error(f"[api] Failed to build item trace graph for {workspace_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to build item trace graph") from e


@router.get("/ws/{workspace_id}/trace/item/{item_id}")
async def get_item_neighbors(
    workspace_id: str,
    item_id: str,
    tg: TraceGraphService = Depends(get_trace_graph_service),
) -> dict:
    """The connected subgraph for one item — ancestors (upstream) + descendants (downstream).

    Powers "highlight everything related to this item" in both directions.
    """
    try:
        return await tg.item_neighbors(workspace_id, item_id)
    except Exception as e:
        log.error(f"[api] Failed to get item neighbors for {workspace_id}/{item_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to get item neighbors") from e


@router.get("/ws/{workspace_id}/relationships")
async def list_relationships(
    workspace_id: str,
    trace: TraceabilityService = Depends(get_traceability_service),
) -> RelationshipsResponse:
    """List all artifact relationships in a workspace."""
    try:
        relationships = await trace.relationship_dao.get_all(workspace_id)
        return RelationshipsResponse(
            workspace_id=workspace_id,
            count=len(relationships),
            relationships=[Relationship(**rel) for rel in relationships],
        )
    except Exception as e:
        log.error(f"[api] Failed to list relationships for {workspace_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to list relationships") from e


@router.get("/ws/{workspace_id}/relationships/from/{artifact_id}")
async def get_outgoing_relationships(
    workspace_id: str,
    artifact_id: str,
    trace: TraceabilityService = Depends(get_traceability_service),
) -> RelationshipsResponse:
    """Get all relationships FROM this artifact (outgoing edges).

    Example: if artifact_id='analysis-123', returns relationships where
    analysis-123 is the source (points to other artifacts).
    """
    try:
        relationships = await trace.relationship_dao.get_outgoing(workspace_id, artifact_id)
        return RelationshipsResponse(
            workspace_id=workspace_id,
            count=len(relationships),
            relationships=[Relationship(**rel) for rel in relationships],
        )
    except Exception as e:
        log.error(f"[api] Failed to get outgoing relationships for {artifact_id}: {e}")
        raise HTTPException(
            status_code=500, detail="Failed to get outgoing relationships"
        ) from e


@router.get("/ws/{workspace_id}/relationships/to/{artifact_id}")
async def get_incoming_relationships(
    workspace_id: str,
    artifact_id: str,
    trace: TraceabilityService = Depends(get_traceability_service),
) -> RelationshipsResponse:
    """Get all relationships TO this artifact (incoming edges).

    Example: if artifact_id='fsd-123', returns relationships where
    fsd-123 is the target (points FROM other artifacts).
    """
    try:
        relationships = await trace.relationship_dao.get_incoming(workspace_id, artifact_id)
        return RelationshipsResponse(
            workspace_id=workspace_id,
            count=len(relationships),
            relationships=[Relationship(**rel) for rel in relationships],
        )
    except Exception as e:
        log.error(f"[api] Failed to get incoming relationships for {artifact_id}: {e}")
        raise HTTPException(
            status_code=500, detail="Failed to get incoming relationships"
        ) from e


@router.post("/ws/{workspace_id}/relationships")
async def create_relationship(
    workspace_id: str,
    body: RelationshipCreate,
    trace: TraceabilityService = Depends(get_traceability_service),
) -> dict:
    """Create a new artifact relationship (manual linking).

    Typically relationships are created automatically when artifacts progress
    through stages, but this endpoint allows manual relationship creation.
    """
    try:
        await trace.link_artifacts(
            workspace_id=workspace_id,
            from_artifact_id=body.source_artifact_id,
            to_artifact_id=body.target_artifact_id,
            relationship_type=body.relationship_type,
            description=body.description,
        )
        return {
            "workspace_id": workspace_id,
            "status": "created",
            "relationship": {
                "source_artifact_id": body.source_artifact_id,
                "target_artifact_id": body.target_artifact_id,
                "relationship_type": body.relationship_type,
                "description": body.description,
            },
        }
    except Exception as e:
        log.error(f"[api] Failed to create relationship: {e}")
        raise HTTPException(status_code=500, detail="Failed to create relationship") from e


@router.delete("/ws/{workspace_id}/relationships")
async def delete_relationship(
    workspace_id: str,
    source_artifact_id: str = Query(..., description="Source artifact ID"),
    target_artifact_id: str = Query(..., description="Target artifact ID"),
    trace: TraceabilityService = Depends(get_traceability_service),
) -> dict:
    """Delete an artifact relationship."""
    try:
        await trace.relationship_dao.delete(
            workspace_id=workspace_id,
            source_artifact_id=source_artifact_id,
            target_artifact_id=target_artifact_id,
        )
        return {
            "workspace_id": workspace_id,
            "status": "deleted",
            "source_artifact_id": source_artifact_id,
            "target_artifact_id": target_artifact_id,
        }
    except Exception as e:
        log.error(f"[api] Failed to delete relationship: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete relationship") from e
