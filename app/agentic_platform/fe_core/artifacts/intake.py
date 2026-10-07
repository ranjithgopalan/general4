"""Document intake: register an uploaded document as a durable, approved input artefact.

The upload path used to keep only RAG chunks; the file itself lived nowhere the
agents could reach after the request ended. Now every upload becomes an
`SdlcArtifact` of type `input-<document_kind>` (stage `intake`, status APPROVED —
documentary evidence, authority tier C) with the raw file and its canonical
Markdown stored through the ArtifactStore. That means:

* every stage's intake step can materialise it like any other approved input
* the same download / content / presigned endpoints serve it
* an `intake_ready` outbox event is queued so an orchestrator can start the graph
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
import tempfile

from app.agentic_platform.fe_core.artifacts.store import artifact_key, get_artifact_store
from app.agentic_platform.fe_core.kb.models import ArtifactStatus, SdlcArtifact
from app.agentic_platform.fe_core.store import make_idempotency_key, sha256

logger = logging.getLogger(__name__)

INTAKE_STAGE = "intake"


def input_artifact_type(document_kind: str) -> str:
    kind = "".join(c for c in (document_kind or "document").lower() if c.isalnum() or c in "-_") or "document"
    return f"input-{kind}"


def register_input_document(
    *, store, settings, project_id: str, workspace_id: str | None, pipeline: str,
    filename: str, document_kind: str, raw: bytes, canonical_md: str, extracted_as: str,
    uploaded_by: str | None, emit_event: bool = True,
) -> SdlcArtifact:
    """Store raw + canonical Markdown and return the APPROVED input artefact.

    Idempotent on content: the same bytes for the same project/kind return the
    existing artefact (checksum match) instead of a new version.

    `emit_event=False` stores the document without queueing `intake_ready`. The
    global library uses it: its build is started explicitly, and an upload must
    not start a pipeline thread for it.
    """
    artifact_type = input_artifact_type(document_kind)
    checksum = sha256(f"{artifact_type}:{sha256(raw.hex())}")
    existing = store.find_artifact_by_checksum(pipeline, INTAKE_STAGE, artifact_type, checksum,
                                               workspace_id=workspace_id)
    if existing is not None:
        return existing

    version = store.next_version(pipeline, INTAKE_STAGE, artifact_type, workspace_id=workspace_id)
    superseded = store.supersede_previous(pipeline, INTAKE_STAGE, artifact_type, workspace_id=workspace_id)
    art_id = uuid.uuid4().hex[:12]
    astore = get_artifact_store(settings)
    prefix = settings.fe_s3_prefix if astore.kind == "s3" else "fe"
    key = artifact_key(prefix, project_id, workspace_id, artifact_type, version, art_id)

    safe_name = Path(filename or "document").name
    stem = Path(safe_name).stem or "document"
    with tempfile.TemporaryDirectory(prefix="fe-intake-") as tmp:
        root = Path(tmp)
        (root / safe_name).write_bytes(raw)
        (root / f"{stem}.md").write_text(canonical_md, encoding="utf-8")
        (root / f"{artifact_type}.md").write_text(canonical_md, encoding="utf-8")  # the <type>.md contract
        manifest = astore.put_tree(root, key, artifact_id=art_id, artifact_type=artifact_type,
                                   version=version, project_id=project_id, workspace_id=workspace_id)

    now = datetime.now(timezone.utc)
    artifact = SdlcArtifact(
        id=art_id, kb_application_id=project_id, pipeline=pipeline, stage_key=INTAKE_STAGE,
        sdlc_stage=INTAKE_STAGE, artifact_type=artifact_type, version=version,
        supersedes_id=superseded, workspace_id=workspace_id, tier="global",
        produced_by_persona="business-analyst", artifact_tier="requirement",
        path=astore.uri(key), content_uri=astore.uri(key), manifest_uri=astore.uri(f"{key}/manifest.json"),
        storage_kind=astore.kind,
        files=[{"path": f.path, "sha256": f.sha256, "size": f.size, "content_type": f.content_type}
               for f in manifest.files],
        checksum=checksum, content_sha256=checksum,
        status=ArtifactStatus.APPROVED,             # documentary evidence, tier C
        approved_by=uploaded_by or "upload", approved_at=now,
        approval_feedback=f"uploaded {safe_name} ({extracted_as})",
        runner="upload", created_by=uploaded_by, created_at=now,
    )
    store.put_artifact(artifact)
    if emit_event:
        store.enqueue_outbox(
            aggregate_id=artifact.id, aggregate_version=version, operation="intake_ready",
            idempotency_key=make_idempotency_key(project_id, workspace_id or "_", artifact_type, checksum),
        )
    logger.info("Intake: %s v%d for %s stored at %s", artifact_type, version, project_id, artifact.content_uri)
    return artifact
