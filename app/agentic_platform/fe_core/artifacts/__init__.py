"""Artefact file storage — where documents and generated files live between agents.

Metadata (which artefact, version, checksum, approvals) stays in the control-plane
store (JsonFileStore / PostgresStore). *Bodies* — uploaded documents, generated
Markdown, code trees — go through the `ArtifactStore` port so the same code runs
against a local directory on a laptop and an S3 bucket on ECS.

Key layout (identical for both backends):

    <prefix>/<project>/<workspace>/<artifact_type>/v<version>/<artifact_id>/<relative file path>
    <prefix>/<project>/<workspace>/<artifact_type>/v<version>/<artifact_id>/manifest.json

`manifest.json` lists every file with sha256 and size so a consumer can verify
what it downloaded and an agent's intake step can pull exactly the named inputs.
"""

from app.agentic_platform.fe_core.artifacts.store import (
    ArtifactStore,
    FileRef,
    LocalArtifactStore,
    Manifest,
    S3ArtifactStore,
    artifact_key,
    get_artifact_store,
)

__all__ = [
    "ArtifactStore", "FileRef", "LocalArtifactStore", "Manifest", "S3ArtifactStore",
    "artifact_key", "get_artifact_store",
]
