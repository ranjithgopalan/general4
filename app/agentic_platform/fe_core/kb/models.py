"""KB contract types.

These mirror the knowledge base's `/fabric/*` shapes closely enough to be useful without
importing the knowledge base's ORM (87 KB in one module, and coupling this service to it
would defeat the separation the PRD argues for).
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class AppStatus(str, Enum):
    ACTIVE = "ACTIVE"
    DRAFT = "DRAFT"
    ARCHIVED = "ARCHIVED"


class KbApplication(BaseModel):
    """A row from the knowledge base's kb_applications table / the KB Applications screen."""

    model_config = ConfigDict(extra="allow")

    id: str
    name: str
    description: str | None = None
    business_domain: str | None = None
    owner: str | None = None
    status: AppStatus | str = AppStatus.ACTIVE
    created_at: datetime | None = None
    updated_at: datetime | None = None


class KbRepo(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    repo_name: str
    repo_role: str | None = None
    processing_status: str | None = None
    local_path: str | None = None
    repo_url: str | None = None
    default_branch: str | None = None


class Citation(BaseModel):
    """the knowledge base returns citation envelopes rather than bare text."""

    model_config = ConfigDict(extra="allow")

    claim_id: str | None = None
    source_uri: str | None = None
    snippet: str | None = None
    confidence: float | None = None


class KbAnswer(BaseModel):
    """Result of POST /fabric/query.

    the knowledge base returns an explicit `refusal` rather than fabricating an answer when
    retrieval finds nothing. Callers MUST check `refusal` before using `answer`
    -- treating a refusal as content is how hallucinated grounding gets into an
    artefact.
    """

    model_config = ConfigDict(extra="allow")

    answer: str | None = None
    refusal: str | None = None
    citations: list[Citation] = Field(default_factory=list)

    @property
    def grounded(self) -> bool:
        return not self.refusal and bool(self.answer)


class ContextPackage(BaseModel):
    """Result of POST /fabric/context/assemble (the Context Assembly Engine)."""

    model_config = ConfigDict(extra="allow")

    context_objects: list[dict] = Field(default_factory=list)
    policy: str | None = None
    anchors: list[dict] = Field(default_factory=list)

    def as_prompt_context(self, limit: int = 20) -> str:
        """Flatten to text for injection into a stage prompt."""
        if not self.context_objects:
            return ""
        lines = ["## Knowledge Base context", ""]
        for obj in self.context_objects[:limit]:
            name = obj.get("name") or obj.get("label") or "context"
            score = obj.get("assembly_score")
            header = f"### {name}" + (f"  (score {score})" if score is not None else "")
            lines.append(header)
            summary = obj.get("summary") or obj.get("description")
            if summary:
                lines.append(str(summary))
            lines.append("")
        return "\n".join(lines)


class ArtifactStatus(str, Enum):
    DRAFT = "DRAFT"
    IN_REVIEW = "IN_REVIEW"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    SUPERSEDED = "SUPERSEDED"


class SdlcArtifact(BaseModel):
    """A versioned, approval-gated stage output.

    Field set follows PRD 9.2 "Artifact". the knowledge base has no PRD/FRD/HLD/LLD artefact
    types today (only EPIC, USER_STORY, API_CONTRACT, UI_SPEC, DATA_MODEL), so
    this type and its store are owned here until the `sdlc_artifacts` table
    lands in the knowledge base.

    FR-026: immutable by version. A content change creates a new version; prior
    content and approvals stay readable.
    """

    id: str
    kb_application_id: str
    pipeline: str
    stage_key: str
    sdlc_stage: str | None = None
    artifact_type: str
    version: int = 1
    supersedes_id: str | None = None
    parent_id: str | None = None

    # --- two-tier workspace scoping (PRD 9, FR-P4, FR-P5) ----------------
    # `workspace_id` is part of the identity of an artefact, not decoration:
    # version numbering and supersede are scoped by it, which is what stops
    # EPIC 2's `ui-source` v1 from superseding EPIC 1's.
    workspace_id: str | None = None
    tier: str | None = None          # "global" | "mini"
    epic_id: str | None = None

    # --- persona provenance (FR-P3) --------------------------------------
    produced_by_persona: str | None = None
    approved_by_persona: str | None = None
    artifact_tier: str | None = None  # value|requirement|backlog|design|code|test|release|acceptance

    path: str | None = None
    content_uri: str | None = None
    checksum: str | None = None
    content_sha256: str | None = None  # retained alias for checksum
    status: ArtifactStatus = ArtifactStatus.DRAFT

    # --- where the body lives (ECS-safe storage; see fe_core.artifacts) ---------
    # worktree  files only in the run worktree (legacy / local dev)
    # s3|local  uploaded through the ArtifactStore; manifest_uri points at manifest.json
    # git       Mini-workspace code: body is in Git, platform keeps only the reference
    # inline    small text carried in the store (sync endpoint `content`)
    storage_kind: str | None = None
    manifest_uri: str | None = None
    files: list[dict] = Field(default_factory=list)   # [{path, sha256, size, content_type}]
    git_repo: str | None = None
    git_branch: str | None = None
    git_commit: str | None = None
    pr_url: str | None = None
    #: Traceability links (AIDLC `fe_artifact_links` equivalent):
    #: {"type": "DERIVES_FROM", "artifact_id": ...} for each consumed upstream artefact,
    #: {"type": "GROUNDS", "kb_chunk_id": ..., "artifact_type": ...} for each KB chunk
    #: retrieved into the prompt that produced this artefact.
    links: list[dict] = Field(default_factory=list)

    # provenance (PRD 9.2 / FR-019)
    source_workspace: str | None = None
    run_id: str | None = None
    plugin: str | None = None
    plugin_version: str | None = None
    model: str | None = None
    runner: str | None = None
    source_artifact_ids: list[str] = Field(default_factory=list)

    approved_by: str | None = None
    approved_at: datetime | None = None
    approval_feedback: str | None = None

    created_by: str | None = None
    created_at: datetime | None = None
    created_by_run: str | None = None

    @property
    def is_consumable(self) -> bool:
        """FR-025: only the latest Approved version may feed a later stage."""
        return self.status is ArtifactStatus.APPROVED


class ApprovalDecision(str, Enum):
    APPROVE = "approve"
    REJECT = "reject"


class Approval(BaseModel):
    """Append-only approval record tied to an exact artefact version (PRD 9.2)."""

    approval_id: str
    artifact_id: str
    artifact_version: int
    decision: ApprovalDecision
    role: str | None = None
    decided_by: str
    decided_at: datetime
    comment: str | None = None


class OutboxStatus(str, Enum):
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"


class OutboxEvent(BaseModel):
    """FR-029: indexing must be reliable but must not block the metadata commit.

    The artefact record commits first; indexing/sync work is queued here and
    retried, so a KB indexing outage cannot lose an artefact.
    """

    event_id: str
    aggregate_id: str
    aggregate_version: int
    operation: str
    idempotency_key: str
    status: OutboxStatus = OutboxStatus.PENDING
    attempts: int = 0
    next_attempt_at: datetime | None = None
    last_error: str | None = None
    created_at: datetime | None = None
