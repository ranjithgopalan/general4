"""Projects: one per the KB application.

A "project" is not a new entity in the store. It is the KB application plus
whatever this service has opened against it, joined for presentation:

    the knowledge base kb_applications  (the catalogue -- what could be worked on)
          +
    local workspaces      (the state -- what has been started)

Kept as a join rather than a stored row because duplicating the application list
here would drift from the knowledge base's, and the knowledge base is the system of record for what
applications exist.

The union matters when the knowledge base is down. A project you have already onboarded still
appears, sourced from its local workspaces, so the console keeps working and says
which half is missing rather than showing an empty list.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum


def local_project_id(name: str) -> str:
    """The id for a project this service owns, derived from its name.

    A greenfield project has no KB application to take an id from, so one is
    derived here. Derived rather than generated, because onboarding must stay
    idempotent: typing the same name twice has to return the same project rather
    than a second one, and the Global Workspace id is built from this.

    Lower-cased, and restricted to the characters a workspace id allows, so the
    id needs no further sanitising downstream.
    """
    slug = re.sub(r"[^A-Za-z0-9]+", "-", (name or "").strip()).strip("-").lower()
    return (slug or "project")[:64]


class ProjectSource(str, Enum):
    """Where this project's record came from."""

    BOTH = "both"          # in the knowledge base's catalogue and onboarded here
    KB_ONLY = "kb-only"    # in the knowledge base, not yet onboarded -- available to onboard
    LOCAL_ONLY = "local"   # onboarded here but absent from the platform's catalogue


@dataclass
class ProjectSummary:
    """One project, as the console needs it."""

    kb_application_id: str
    name: str
    source: ProjectSource

    # From the knowledge base, when available.
    business_domain: str | None = None
    owner: str | None = None
    kb_status: str | None = None

    # Recorded at onboarding, read back from the Global Workspace. Held here
    # rather than recomputed per request because "no code knowledge" reads as a
    # failure for a brownfield project and as the expected state for a greenfield
    # one -- so the distinction has to outlive the onboarding request that made it.
    category: str | None = None
    intake_source: str | None = None
    source_language: str | None = None
    target_framework: str | None = None

    # From local workspaces.
    global_workspace_id: str | None = None
    mini_workspace_count: int = 0
    open_mini_count: int = 0
    epics: list[str] = field(default_factory=list)

    # Progress in the Global tier, when it has been opened.
    global_stages: int = 0
    global_completed: int = 0
    global_awaiting_approval: int = 0
    global_ready: list[str] = field(default_factory=list)

    @property
    def onboarded(self) -> bool:
        """Onboarded means a Global Workspace exists -- that is the act of
        starting work, and until then a project is only a catalogue entry."""
        return self.global_workspace_id is not None

    @property
    def stage(self) -> str:
        """A one-word answer to "where is this project?"."""
        if not self.onboarded:
            return "not-started"
        if self.mini_workspace_count:
            return "delivery"          # EPICs exist, Mini Workspaces are running
        if self.global_completed:
            return "definition"        # working down the document ladder
        return "onboarded"

    def to_dict(self) -> dict:
        return {
            "kb_application_id": self.kb_application_id,
            "name": self.name,
            "source": self.source.value,
            "onboarded": self.onboarded,
            "stage": self.stage,
            "business_domain": self.business_domain,
            "owner": self.owner,
            "kb_status": self.kb_status,
            "category": self.category,
            "intake_source": self.intake_source,
            "source_language": self.source_language,
            "target_framework": self.target_framework,
            "global_workspace_id": self.global_workspace_id,
            "mini_workspace_count": self.mini_workspace_count,
            "open_mini_count": self.open_mini_count,
            "epics": self.epics,
            "global_progress": {
                "stages": self.global_stages,
                "completed": self.global_completed,
                "awaiting_approval": self.global_awaiting_approval,
                "ready": self.global_ready,
            },
        }
