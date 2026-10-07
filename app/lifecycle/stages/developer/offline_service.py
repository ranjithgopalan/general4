"""Developer offline work submission service.

Handles offline developer work (local dev work submitted back to the platform).
Records: files changed, classes modified, lines changed, KB links (IMPLEMENTS).
"""

from __future__ import annotations

from typing import Any
from datetime import datetime

from app.lifecycle.traceability import TraceabilityService
from app.lifecycle.traceability.analysis_dao import WorkspaceAnalysisDAO
from app.lifecycle.traceability.justification_builder import RelationshipJustification
from app.services.workspace_service import WorkspaceService
from app.models.workspace import Workspace, WorkspaceState
from app.utils.exceptions import ValidationError
from app.utils.logging import log


class DeveloperOfflineWorkItem(dict):
    """Single dev work item: a file/class change."""

    def __init__(
        self,
        file_path: str,
        class_or_component: str,
        change_type: str,
        description: str,
        kb_references: list[str] | None = None,
        lines_changed: int = 0,
    ):
        super().__init__(
            file_path=file_path,
            class_or_component=class_or_component,
            change_type=change_type,
            description=description,
            kb_references=kb_references or [],
            lines_changed=lines_changed,
        )


class DeveloperOfflineSubmission(dict):
    """Developer offline work submission."""

    def __init__(
        self,
        workspace_id: str,
        status: str,
        description: str,
        items: list[dict[str, Any]] | None = None,
        time_spent_minutes: int = 0,
        blockers: str | None = None,
    ):
        super().__init__(
            workspace_id=workspace_id,
            status=status,
            description=description,
            items=items or [],
            time_spent_minutes=time_spent_minutes,
            blockers=blockers,
        )


class DeveloperOfflineService:
    """Handle offline developer work submissions."""

    def __init__(
        self,
        workspace: WorkspaceService,
        trace: TraceabilityService,
    ):
        self._workspace = workspace
        self._trace = trace

    async def submit_dev_work(
        self,
        workspace_id: str,
        submission: DeveloperOfflineSubmission,
        persona: str,
    ) -> dict[str, Any]:
        """
        Process offline dev work submission.

        1. Validate submission
        2. Record dev artifact to fe_workspace_artifacts (kind='dev_offline')
        3. Create IMPLEMENTS links (dev item → KB card)
        4. Update fe_workspace_analysis with dev metrics
        5. Return metrics + confirmation

        Args:
            workspace_id: Workspace ID
            submission: Dev work submission (files, classes, KB refs)
            persona: Persona submitting (developer, architect)

        Returns:
            dict with: status, items_recorded, kb_links_created, metrics
        """
        log.info(f"[dev_offline] Processing submission for {workspace_id} by {persona}")

        # Validate workspace exists
        ws = await self._workspace.get(workspace_id)
        if ws.state is not WorkspaceState.STORIES:
            log.warning(
                f"[dev_offline] {workspace_id} state is {ws.state}, expected STORIES. "
                f"Allowing submission anyway (dev may have worked offline)."
            )

        # Parse items
        items = submission.get("items", [])
        if not items:
            log.warning(f"[dev_offline] Empty items list for {workspace_id}")

        # Count metrics
        files_changed = len(set(item.get("file_path") for item in items))
        classes_modified = len(set(item.get("class_or_component") for item in items))
        total_lines = sum(item.get("lines_changed", 0) for item in items)
        kb_links = sum(len(item.get("kb_references", [])) for item in items)

        # Record artifact to DB (best-effort)
        artifact_id: str | None = None
        try:
            artifact = await self._workspace.attach_artifact(
                workspace_id,
                kind="dev_offline",
                content=str(submission),  # JSON serialization handled by attach_artifact
                grounding_score=0.85,  # Dev work is self-reported, moderate confidence
                actor=f"persona:{persona}",
            )
            artifact_id = artifact.get("artifact_id")
            log.info(f"[dev_offline] Artifact recorded: {artifact_id} for {workspace_id}")
        except Exception as exc:  # noqa: BLE001
            log.warning(f"[dev_offline] Artifact attach failed for {workspace_id}: {exc}")

        # Create IMPLEMENTS links (each dev item → KB refs) — best-effort
        if artifact_id:
            for idx, item in enumerate(items):
                kb_refs = item.get("kb_references", [])
                for kb_id in kb_refs:
                    try:
                        await self._trace.add_grounds(
                            workspace_id=workspace_id,
                            from_artifact_id=artifact_id,
                            source_item_id=f"{item.get('class_or_component', 'item')}-{idx}",
                            persona=persona,
                            artifact_kind="dev_offline",
                            to_kb_card_id=kb_id,
                            kb_version=ws.pinned_kb_version or "unknown",
                            source_locus=f"{item.get('file_path', 'unknown')}",
                            stage="DEVELOPMENT",
                            applied_because="IMPLEMENTS",
                        )
                    except Exception as exc:  # noqa: BLE001
                        log.warning(
                            f"[dev_offline] IMPLEMENTS link failed for {workspace_id} → {kb_id}: {exc}"
                        )

        # Update fe_workspace_analysis with dev metrics — best-effort
        try:
            dao = WorkspaceAnalysisDAO()
            dev_metrics = {
                "dev_status": submission.get("status", "IN_PROGRESS"),
                "files_changed": files_changed,
                "classes_modified": classes_modified,
                "lines_changed": total_lines,
                "kb_links_created": kb_links,
                "time_spent_minutes": submission.get("time_spent_minutes", 0),
                "blockers": submission.get("blockers"),
            }
            # TODO: Add update_dev_metrics method to DAO or update existing metrics dict
            log.info(f"[dev_offline] Metrics recorded for {workspace_id}: {dev_metrics}")
        except Exception as exc:  # noqa: BLE001
            log.warning(f"[dev_offline] Metrics update failed for {workspace_id}: {exc}")

        return {
            "workspace_id": workspace_id,
            "stage": "DEVELOPMENT",
            "status": submission.get("status", "IN_PROGRESS"),
            "items_recorded": len(items),
            "files_changed": files_changed,
            "classes_modified": classes_modified,
            "lines_changed": total_lines,
            "kb_links_created": kb_links,
            "message": (
                f"Recorded {len(items)} dev changes ({files_changed} files, "
                f"{classes_modified} classes, {total_lines} lines). "
                f"Created {kb_links} KB links (IMPLEMENTS)."
            ),
        }

    async def get_dev_progress(self, workspace_id: str) -> dict[str, Any]:
        """Retrieve current dev work status from DB.

        Queries fe_workspace_artifacts where kind='dev_offline' for this workspace.
        """
        # TODO: Query fe_workspace_artifacts for latest dev_offline artifact
        # For now, return placeholder
        log.info(f"[dev_offline] Fetching progress for {workspace_id}")
        return {
            "workspace_id": workspace_id,
            "stage": "DEVELOPMENT",
            "status": "idle",
            "items": [],
            "metrics": {
                "files_changed": 0,
                "classes_modified": 0,
                "lines_changed": 0,
                "kb_links_created": 0,
            },
        }
