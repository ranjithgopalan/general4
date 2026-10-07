"""Workspace instances: one Global, one Mini per EPIC (PRD 5.3, FR-P4, FR-P5).

The Global Workspace runs once for the programme and ends at the approved EPIC
set. Each EPIC then opens its own Mini Workspace, identical in shape, scoped to
that EPIC, admitting all eight personas, and closing when the EPIC is accepted.

Isolation is by construction rather than by convention: `workspace_id` is part of
every artefact key, so a query that forgets to filter by workspace returns
nothing for the wrong EPIC instead of leaking another EPIC's work. That is what
makes FR-P5 ("ten open at once") safe.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field, field_validator

from app.agentic_platform.fe_core.auth.personas import WorkspaceTier

GLOBAL_WORKSPACE_SUFFIX = "global"
ARCHITECTURE_WORKSPACE_SUFFIX = "architecture"


class WorkspaceStatus(str, Enum):
    OPEN = "open"
    CLOSED = "closed"       # the EPIC was accepted, or the programme finished
    CANCELLED = "cancelled"  # the EPIC was dropped


class Epic(BaseModel):
    """One EPIC decomposed from the approved SRD (PRD 8.1 G7 -> G8).

    `key` is what appears in a workspace id and a path, so it is constrained to
    a slug: an EPIC titled "Rate/Limit Review" must not create a directory
    separator.
    """

    key: str
    title: str
    summary: str | None = None
    sequence: int = 0
    source_artifact_id: str | None = None   # the SRD/EPIC-set artefact it came from

    @field_validator("key", mode="before")
    @classmethod
    def _slug(cls, value: object) -> str:
        raw = str(value or "").strip().lower()
        slug = re.sub(r"[^a-z0-9]+", "-", raw).strip("-")
        if not slug:
            raise ValueError("epic key cannot be empty")
        return slug[:48]


class Workspace(BaseModel):
    """A workspace instance. PRD 9 `workspaces` table."""

    id: str
    kb_application_id: str
    tier: WorkspaceTier
    pipeline: str
    epic_id: str | None = None
    epic_title: str | None = None
    status: WorkspaceStatus = WorkspaceStatus.OPEN
    parent_workspace_id: str | None = None      # a Mini points at the Global
    created_from_artifact_id: str | None = None  # the approved EPIC set
    opened_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    closed_at: datetime | None = None

    # Intake, recorded on the Global Workspace only -- the Global Workspace *is*
    # the project, so this is where a project-level fact belongs without adding a
    # second store. Optional so that workspaces persisted before these existed
    # still load.
    category: str | None = None            # greenfield | brownfield
    intake_source: str | None = None       # requirements | reverse-engineering | ...
    source_language: str | None = None     # vb6-vba | asp-vbscript | mainframe
    target_framework: str | None = None    # angular-springboot | angular-fastapi
    gear_id: str | None = None             # RE Graph gear identifier (e.g. 'japan')

    # --- orchestration (FE_ORCHESTRATOR=langgraph) ---------------------------
    # {status: idle|running|waiting|handed_off|completed|failed, current_stage,
    #  waiting_on_persona, run_id, thread_id, last_error, updated_at}
    orchestration: dict | None = None

    @property
    def is_global(self) -> bool:
        return self.tier is WorkspaceTier.GLOBAL

    @property
    def is_open(self) -> bool:
        return self.status is WorkspaceStatus.OPEN

    @property
    def label(self) -> str:
        if self.is_global:
            return "Global Workspace"
        if self.tier is WorkspaceTier.ARCHITECTURE:
            return "Architecture Workspace"
        if self.tier is WorkspaceTier.MINI and self.epic_id is None:
            return "EPIC Assembler Workspace"
        return f"EPIC {self.epic_id}" + (f" - {self.epic_title}" if self.epic_title else "")

    def close(self, *, cancelled: bool = False) -> "Workspace":
        return self.model_copy(update={
            "status": WorkspaceStatus.CANCELLED if cancelled else WorkspaceStatus.CLOSED,
            "closed_at": datetime.now(timezone.utc),
        })


def global_workspace_id(kb_application_id: str) -> str:
    """Deterministic, so the Global Workspace cannot be created twice.

    FR-P4 says exactly one exists per programme; deriving the id rather than
    generating one means a concurrent create is idempotent instead of a race.
    """
    return f"{_safe(kb_application_id)}--{GLOBAL_WORKSPACE_SUFFIX}"


def architecture_workspace_id(kb_application_id: str) -> str:
    """Deterministic, so the Architecture Workspace (singleton per programme)
    cannot be created twice."""
    return f"{_safe(kb_application_id)}--{ARCHITECTURE_WORKSPACE_SUFFIX}"


def mini_workspace_id(kb_application_id: str, epic_key: str) -> str:
    """Deterministic per EPIC, for the same reason.

    Two normalisations, both load-bearing for FR-P4 ("one Mini Workspace per
    EPIC"):

    * **Lower-cased.** `EPIC-1` and `epic-1` are the same EPIC, so they must yield
      the same id. Without this they produce two workspaces for one EPIC.
    * **Prefix not doubled.** Keys usually arrive as `EPIC-1` and slug to
      `epic-1`; the naive form produced ids reading `app--epic-epic-1`.
    """
    key = _safe(epic_key).lower()
    suffix = key if key.startswith("epic-") else f"epic-{key}"
    return f"{_safe(kb_application_id)}--{suffix}"


def _safe(component: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(component)).strip("-")
    return (cleaned or "unknown")[:64]


def new_global_workspace(kb_application_id: str, pipeline: str) -> Workspace:
    return Workspace(
        id=global_workspace_id(kb_application_id),
        kb_application_id=kb_application_id,
        tier=WorkspaceTier.GLOBAL,
        pipeline=pipeline,
    )


def new_architecture_workspace(kb_application_id: str, pipeline: str) -> Workspace:
    """One Architecture Workspace per programme (singleton), parented to Global."""
    return Workspace(
        id=architecture_workspace_id(kb_application_id),
        kb_application_id=kb_application_id,
        tier=WorkspaceTier.ARCHITECTURE,
        pipeline=pipeline,
        parent_workspace_id=global_workspace_id(kb_application_id),
    )


def new_mini_workspace(
    kb_application_id: str,
    pipeline: str,
    epic: Epic,
    *,
    parent_workspace_id: str | None = None,
    created_from_artifact_id: str | None = None,
) -> Workspace:
    return Workspace(
        id=mini_workspace_id(kb_application_id, epic.key),
        kb_application_id=kb_application_id,
        tier=WorkspaceTier.MINI,
        pipeline=pipeline,
        epic_id=epic.key,
        epic_title=epic.title,
        parent_workspace_id=parent_workspace_id
        or global_workspace_id(kb_application_id),
        created_from_artifact_id=created_from_artifact_id or epic.source_artifact_id,
    )
