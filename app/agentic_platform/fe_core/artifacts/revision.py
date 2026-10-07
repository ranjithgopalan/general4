"""Human revision of a generated artefact.

A reviewer may hand-edit a stage's document (or upload a corrected file) before
approving it. Rather than mutate the agent's output, an edit becomes the artefact's
**next version** (status ``IN_REVIEW``, ``runner="human-edit"``) with the AI original
kept and marked ``SUPERSEDED`` — the same immutable-versioning contract regeneration
uses (FR-026). The body is written through the ArtifactStore, so the same
download / content / export endpoints serve it and it lands in S3 like any other
artefact.

The caller (``JobService.revise_artifact``) is responsible for the guards and for
repointing the run at the new version so the subsequent approval carries it forward.
"""

from __future__ import annotations

import logging
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app.agentic_platform.fe_core.artifacts.store import artifact_key, get_artifact_store
from app.agentic_platform.fe_core.kb.models import ArtifactStatus, SdlcArtifact
from app.agentic_platform.fe_core.store import sha256

logger = logging.getLogger(__name__)


def write_markdown_revision(
    *, store, settings, base: SdlcArtifact, content_md: str,
    editor: str | None, comment: str | None,
) -> SdlcArtifact:
    """Persist ``content_md`` as the next version of ``base`` and return it.

    ``base`` is superseded; the new version copies ``base``'s scope (project,
    workspace, pipeline, stage, type, run) and carries the human editor and the
    per-document ``comment`` as its ``approval_feedback``.
    """
    pipeline, stage_key, atype = base.pipeline, base.stage_key, base.artifact_type
    workspace_id = base.workspace_id

    version = store.next_version(pipeline, stage_key, atype, workspace_id=workspace_id)
    superseded = store.supersede_previous(pipeline, stage_key, atype, workspace_id=workspace_id)

    new_id = uuid.uuid4().hex[:12]
    astore = get_artifact_store(settings)
    prefix = settings.fe_s3_prefix if astore.kind == "s3" else "fe"
    key = artifact_key(prefix, base.kb_application_id, workspace_id, atype, version, new_id)
    checksum = sha256(f"{atype}:{sha256(content_md)}")

    with tempfile.TemporaryDirectory(prefix="fe-revision-") as tmp:
        root = Path(tmp)
        (root / f"{atype}.md").write_text(content_md, encoding="utf-8")  # the <type>.md contract
        manifest = astore.put_tree(
            root, key, artifact_id=new_id, artifact_type=atype,
            version=version, project_id=base.kb_application_id, workspace_id=workspace_id,
        )

    now = datetime.now(timezone.utc)
    revision = base.model_copy(update={
        "id": new_id,
        "version": version,
        "parent_id": base.id,
        "supersedes_id": superseded,
        "status": ArtifactStatus.IN_REVIEW,
        "path": astore.uri(key),
        "content_uri": astore.uri(key),
        "manifest_uri": astore.uri(f"{key}/manifest.json"),
        "storage_kind": astore.kind,
        "files": [{"path": f.path, "sha256": f.sha256, "size": f.size, "content_type": f.content_type}
                  for f in manifest.files],
        "checksum": checksum,
        "content_sha256": checksum,
        "runner": "human-edit",
        "created_by": editor,
        "created_at": now,
        "approval_feedback": comment,
        "approved_by": None,
        "approved_at": None,
        "approved_by_persona": None,
    })
    store.put_artifact(revision)
    logger.info(
        "Revision: %s v%d (human edit of %s) for %s stored at %s",
        atype, version, base.id, base.kb_application_id, revision.content_uri,
    )
    return revision
