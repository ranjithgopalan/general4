"""S0 · INTAKE stage handler — deterministic capture, no LLM.

Saves the submitted workspace requirement to S3 (overwrite on re-save) and mirrors it in
``fe_workspace_artifacts`` (kind='intake'). Read-back is S3-first with DB mirror fallback —
the same pattern used by the ANALYSIS stage (docs/23 §5).

S3 key: ``{prefix}/{workspace_id}/intake/intake.json``
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.lifecycle.stages.intake.schema import AttachedFile, IntakeArtifact
from app.lifecycle.traceability.analysis_dao import WorkspaceAnalysisDAO
from app.models.workspace import Workspace
from app.services.artifact_store import ArtifactStore, get_artifact_store
from app.services.workspace_service import WorkspaceService
from app.utils.logging import log


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class IntakeService:
    """Persist and retrieve the S0·INTAKE artifact (requirement snapshot)."""

    _STAGE_FOLDER = "intake"

    def __init__(
        self,
        *,
        workspace: WorkspaceService,
        store: ArtifactStore | None = None,
    ) -> None:
        self._workspace = workspace
        self._store = store or get_artifact_store()

    def _s3_key(self, workspace_id: str) -> str:
        return self._store.key(workspace_id, self._STAGE_FOLDER, "intake.json")

    # ── public API ────────────────────────────────────────────────────────────

    async def save(
        self,
        ws: Workspace,
        *,
        attached_files: list[AttachedFile] | None = None,
    ) -> IntakeArtifact:
        """Snapshot the workspace submission to S3 + DB artifact.  Best-effort — never raises."""
        artifact = IntakeArtifact(
            workspace_id=ws.workspace_id,
            type=ws.type,
            route=ws.route,
            gear_id=ws.gear_id,
            pinned_kb_version=ws.pinned_kb_version,
            requirement_text=ws.requirement_text or "",
            attached_files=attached_files or [],
            created_at=ws.created_at.isoformat() if ws.created_at else _now_iso(),
            created_by=ws.created_by,
        )
        await self._persist(ws.workspace_id, artifact)
        return artifact

    async def get(self, workspace_id: str) -> IntakeArtifact | None:
        """Return the persisted intake artifact — S3 first, DB mirror fallback."""
        await self._workspace.get(workspace_id)  # 404 if missing

        s3_text = await self._store.get_text(self._s3_key(workspace_id))
        if s3_text:
            parsed = self._parse(workspace_id, s3_text, source="s3")
            if parsed is not None:
                return parsed

        artifacts = await self._workspace.list_artifacts(workspace_id)
        intakes = [a for a in artifacts if a.get("kind") == "intake" and a.get("content")]
        if not intakes:
            return None
        return self._parse(workspace_id, intakes[-1]["content"], source="db")

    # ── internals ─────────────────────────────────────────────────────────────

    @staticmethod
    def _parse(workspace_id: str, text: str, *, source: str) -> IntakeArtifact | None:
        try:
            return IntakeArtifact.model_validate_json(text)
        except Exception as exc:  # noqa: BLE001
            log.warning(f"[intake] {source} artifact for {workspace_id} not parseable: {exc}")
            return None

    async def _persist(self, workspace_id: str, artifact: IntakeArtifact) -> None:
        """Upload to S3 (overwrite) + DB mirror.  Both are best-effort; errors are logged, not raised."""
        content = artifact.model_dump_json()
        s3_uri: str | None = None
        try:
            s3_uri = await self._store.put_json(self._s3_key(workspace_id), artifact.model_dump())
        except Exception as exc:  # noqa: BLE001
            log.warning(f"[intake] S3 write failed for {workspace_id}: {exc}")

        try:
            await self._workspace.attach_artifact(
                workspace_id,
                kind="intake",
                content=content,
                s3_uri=s3_uri,
                template_id=artifact.template_id,
                template_version=artifact.template_version,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning(f"[intake] DB artifact write failed for {workspace_id}: {exc}")

        # Save Intake metrics to fe_workspace_analysis table for UI traceability tab
        try:
            dao = WorkspaceAnalysisDAO()

            # Extract scope from intake requirement
            scope = {
                "in_scope": [
                    {"item_id": "requirement", "item_description": artifact.requirement[:100] if artifact.requirement else "Requirement", "scope_category": "IN_SCOPE"}
                ],
                "out_of_scope": [],
                "deferred": [],
            }

            # Extract metrics (basic for intake)
            metrics = {
                "total_artifacts": 1,
                "total_relationships": 0,
                "coverage_pct": 0.0,
                "orphan_count": 0,
                "systems_affected": 0,
            }

            # Save to database
            await dao.update_scope(workspace_id, scope)
            await dao.update_metrics(workspace_id, metrics)

            log.info(f"[intake] Saved Intake data for {workspace_id}")
        except Exception as exc:  # noqa: BLE001
            log.warning(f"[intake] Failed to save Intake data to traceability table for {workspace_id}: {exc}")
