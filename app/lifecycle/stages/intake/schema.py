"""S0 · INTAKE stage artifact schema.

No LLM runs at INTAKE — this is a deterministic capture of exactly what the PO/BA
submitted when creating the workspace: the requirement text, workspace metadata, and
any file attachments. Saved to S3 + mirrored in ``fe_workspace_artifacts`` (kind='intake')
so it is readable, auditable, and shown in the S0 accordion panel.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class AttachedFile(BaseModel):
    name: str
    size_bytes: int | None = None
    s3_uri: str | None = None  # set if file was uploaded to S3
    content_type: str | None = None


class IntakeArtifact(BaseModel):
    """Verbatim capture of the S0·INTAKE submission — no LLM, no inference."""

    workspace_id: str
    template_id: str = "aig-intake"
    template_version: str = "v1"

    # workspace identity
    type: str
    route: str | None = None
    gear_id: str = "japan"
    pinned_kb_version: str | None = None

    # submitted requirement
    requirement_text: str
    attached_files: list[AttachedFile] = Field(default_factory=list)

    # provenance
    created_at: str
    created_by: str | None = None
