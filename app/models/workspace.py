"""Workspace domain model + value enums (docs/19 §2, docs/04 §8).

Pure value types shared by the state machine (``app.domain.workspace_state``), the repositories,
the service, and the API DTOs. ``StrEnum`` members serialize to the exact TEXT stored in
``fe_workspaces`` (and enforced by its CHECK constraints).
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class WorkspaceState(StrEnum):
    """The 10 stored lifecycle states (OPEN is a conceptual superstate, never persisted)."""

    INTAKE = "INTAKE"
    ANALYSIS = "ANALYSIS"
    FSD = "FSD"
    BRD = "BRD"
    STORIES = "STORIES"
    ARCHITECTURE = "ARCHITECTURE"
    DEVELOPMENT = "DEVELOPMENT"
    QA_TESTING = "QA_TESTING"
    MERGE = "MERGE"  # DevOps persona: merge the delivered work + trigger the KB-refresh round-trip
    PENDING_SYNC = "PENDING_SYNC"
    CLOSED = "CLOSED"


class WorkspaceType(StrEnum):
    """Workspace type classification (user-selected at INTAKE, independent of change_class from analysis).

    These classify the KIND of change being made, not its scope (scope is determined by impact analysis).
    See docs/19 §2 + CLAUDE.md Workspace types.
    """
    NEW_FEATURE = "NewFeature"
    ENHANCEMENT = "Enhancement"
    TECH_MODERNIZATION = "TechModernization"
    UPGRADE = "Upgrade"
    DEFECT_RESOLUTION = "DefectResolution"


class Route(StrEnum):
    A = "a"
    B = "b"
    C = "c"
    D = "d"
    E = "e"
    F = "f"


class Workspace(BaseModel):
    """The SDLC workspace entity — mirrors ``fe_workspaces`` (docs/09 §3.6)."""

    workspace_id: str
    gear_id: str = "japan"
    title: str | None = None
    type: WorkspaceType
    route: Route | None = None
    state: WorkspaceState
    pinned_kb_version: str | None = None
    requirement_text: str | None = None
    created_by: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    closed_at: datetime | None = None

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> Workspace:
        """Build from a repository dict row (extra keys ignored)."""
        return cls.model_validate(row)


# ── API request/response DTOs (docs/19 §6) ────────────────────────────────────────
class CreateWorkspaceRequest(BaseModel):
    """INTAKE workspace creation request. Requirement can be provided as TEXT or via FILE.

    TEXT mode: requirement_text + optional files (for reference).
    FILE mode: files only (requirement_text extracted from file content).
    Both can be provided; text takes precedence. File content is parsed if .md or .txt.
    """
    type: WorkspaceType
    title: str | None = None
    requirement_text: str | None = None
    route: Route | None = None
    gear_id: str = "japan"
    pinned_kb_version: str | None = None


class TransitionRequest(BaseModel):
    to_state: WorkspaceState


class AttachArtifactRequest(BaseModel):
    kind: str
    content: str | None = None
    s3_uri: str | None = None
    template_id: str | None = None
    template_version: str | None = None
    grounding_score: float | None = None


class WorkspaceArtifact(BaseModel):
    """An artifact row (mirrors ``fe_workspace_artifacts``)."""

    artifact_id: str
    workspace_id: str
    kind: str
    template_id: str | None = None
    template_version: str | None = None
    content: str | None = None
    s3_uri: str | None = None
    grounding_score: float | None = None


class TraceNode(BaseModel):
    """A node in a workspace's traceability graph."""

    id: str
    kind: str  # 'artifact' | 'kb_card'
    label: str | None = None


class TraceEdge(BaseModel):
    """A traceability edge — GROUNDS (-> KB card) or DERIVES_FROM (-> upstream artifact)."""

    from_id: str
    to_id: str
    link_type: str  # 'GROUNDS' | 'DERIVES_FROM'
    stage: str | None = None
    source_locus: str | None = None


class TraceGraph(BaseModel):
    """The two-edge traceability graph for one workspace (docs/19 §7)."""

    workspace_id: str
    nodes: list[TraceNode] = Field(default_factory=list)
    edges: list[TraceEdge] = Field(default_factory=list)
