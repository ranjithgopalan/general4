"""Clarification data layer — gap clarifications + requirement versions.

Persistence for impact analysis clarifications and requirement iteration tracking.
(docs/IMPACT_ANALYSIS_CLARIFICATION_MERGED.md)
"""

from __future__ import annotations

from typing import Any

from app.config import get_settings
from app.dao.base import BaseRepository
from app.utils.logging import log

_SCHEMA = get_settings().PG_SCHEMA


class ClarificationRepository(BaseRepository):
    """CRUD for ``fe_workspace_clarifications`` — user clarifications for gaps."""

    async def create(
        self,
        *,
        clarification_id: str,
        workspace_id: str,
        gap_id: str,
        gap_type: str,
        user_input: str,
        clarification_type: str,
        analysis_iteration: int = 1,
        created_by: str | None = None,
    ) -> dict[str, Any] | None:
        """Insert a clarification."""
        sql = f"""
            INSERT INTO {_SCHEMA}.fe_workspace_clarifications
            (clarification_id, workspace_id, analysis_iteration, gap_id, gap_type,
             user_input, clarification_type, created_by)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING clarification_id, workspace_id, analysis_iteration, gap_id, gap_type,
                      user_input, clarification_type, created_at, created_by
        """
        result = await self.fetch_one(
            sql,
            (
                clarification_id,
                workspace_id,
                analysis_iteration,
                gap_id,
                gap_type,
                user_input,
                clarification_type,
                created_by,
            ),
        )
        if result:
            log.info(f"[clarifications] Saved clarification {clarification_id} for {workspace_id}")
        return result

    async def get_by_workspace(
        self,
        workspace_id: str,
        analysis_iteration: int | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch all clarifications for a workspace."""
        if analysis_iteration is not None:
            sql = f"""
                SELECT clarification_id, workspace_id, analysis_iteration, gap_id, gap_type,
                       user_input, clarification_type, created_at, created_by
                FROM {_SCHEMA}.fe_workspace_clarifications
                WHERE workspace_id = %s AND analysis_iteration = %s
                ORDER BY created_at ASC
            """
            return await self.fetch(sql, (workspace_id, analysis_iteration))
        else:
            sql = f"""
                SELECT clarification_id, workspace_id, analysis_iteration, gap_id, gap_type,
                       user_input, clarification_type, created_at, created_by
                FROM {_SCHEMA}.fe_workspace_clarifications
                WHERE workspace_id = %s
                ORDER BY created_at ASC
            """
            return await self.fetch(sql, (workspace_id,))

    async def delete_for_workspace(self, workspace_id: str) -> int:
        """Delete all clarifications for a workspace (on workspace close)."""
        sql = f"DELETE FROM {_SCHEMA}.fe_workspace_clarifications WHERE workspace_id = %s"
        result = await self._pool.execute(sql, (workspace_id,))
        return result


class RequirementVersionRepository(BaseRepository):
    """CRUD for ``fe_workspace_requirement_versions`` — requirement evolution."""

    async def create(
        self,
        *,
        workspace_id: str,
        requirement_version: int,
        requirement_text: str,
        source: str,
        clarifications_applied: list[str] | None = None,
        analysis_artifact_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Insert a requirement version."""
        sql = f"""
            INSERT INTO {_SCHEMA}.fe_workspace_requirement_versions
            (workspace_id, requirement_version, requirement_text, source,
             clarifications_applied, analysis_artifact_id)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING workspace_id, requirement_version, requirement_text, source,
                      clarifications_applied, analysis_artifact_id, created_at
        """
        result = await self.fetch_one(
            sql,
            (
                workspace_id,
                requirement_version,
                requirement_text,
                source,
                clarifications_applied or [],
                analysis_artifact_id,
            ),
        )
        if result:
            log.info(
                f"[requirement-versions] Saved requirement v{requirement_version} for {workspace_id} "
                f"(source: {source})"
            )
        return result

    async def get_latest_version(self, workspace_id: str) -> dict[str, Any] | None:
        """Get the latest requirement version."""
        sql = f"""
            SELECT workspace_id, requirement_version, requirement_text, source,
                   clarifications_applied, analysis_artifact_id, created_at
            FROM {_SCHEMA}.fe_workspace_requirement_versions
            WHERE workspace_id = %s
            ORDER BY requirement_version DESC
            LIMIT 1
        """
        return await self.fetch_one(sql, (workspace_id,))

    async def get_version(
        self,
        workspace_id: str,
        requirement_version: int,
    ) -> dict[str, Any] | None:
        """Get a specific requirement version."""
        sql = f"""
            SELECT workspace_id, requirement_version, requirement_text, source,
                   clarifications_applied, analysis_artifact_id, created_at
            FROM {_SCHEMA}.fe_workspace_requirement_versions
            WHERE workspace_id = %s AND requirement_version = %s
        """
        return await self.fetch_one(sql, (workspace_id, requirement_version))

    async def get_all_versions(self, workspace_id: str) -> list[dict[str, Any]]:
        """Get all requirement versions for a workspace."""
        sql = f"""
            SELECT workspace_id, requirement_version, requirement_text, source,
                   clarifications_applied, analysis_artifact_id, created_at
            FROM {_SCHEMA}.fe_workspace_requirement_versions
            WHERE workspace_id = %s
            ORDER BY requirement_version ASC
        """
        return await self.fetch(sql, (workspace_id,))

    async def delete_for_workspace(self, workspace_id: str) -> int:
        """Delete all requirement versions for a workspace (on workspace close)."""
        sql = f"DELETE FROM {_SCHEMA}.fe_workspace_requirement_versions WHERE workspace_id = %s"
        result = await self._pool.execute(sql, (workspace_id,))
        return result


class ArtifactTraceabilityDAO(BaseRepository):
    """Chain-level traceability for the AIDLC pipeline hierarchy.

    Reads artifact metadata, chain relationships, and KB grounding data to power
    the Workspace Traceability page (6 tabs: Timeline, Artifacts, Relationships,
    Trace Graph, Coverage Matrix, KB Grounding).

    Chain: PRD → FRD → SRD → SDD → EPIC → Feature → User Story → Code → Test

    Uses only BaseRepository methods (fetch_one, fetch_all, execute) with
    psycopg3 %s placeholders.  Never raises — traceability must not block the pipeline.
    """

    # ── Artifacts ──────────────────────────────────────────────────────────

    async def get_artifacts(
        self, workspace_ids: list[str]
    ) -> list[dict[str, Any]]:
        """Fetch artifact metadata for a set of workspace IDs (global + related workspaces).

        Returns artifact_id, workspace_id, kind (= stage key), s3_uri,
        grounding_score, triggered_by_name, triggered_by_persona,
        reviewed_by_name, reviewed_at, created_at.

        Note: fe_workspace_artifacts has NO status column — stage state comes
        from fe_pipeline_runs via the /stages API, not this table.
        """
        if not workspace_ids:
            return []
        placeholders = ", ".join(["%s"] * len(workspace_ids))
        sql = f"""
            SELECT artifact_id, workspace_id, kind,
                   s3_uri, grounding_score,
                   triggered_by_name, triggered_by_persona,
                   reviewed_by_name, reviewed_at, created_at
            FROM {_SCHEMA}.fe_workspace_artifacts
            WHERE workspace_id IN ({placeholders})
            ORDER BY created_at ASC
        """
        try:
            return await self.fetch_all(sql, tuple(workspace_ids))
        except Exception:
            log.warning("ArtifactTraceabilityDAO.get_artifacts failed", exc_info=True)
            return []

    # ── Relationships (auto-populated by stage_executor._record_lineage) ───

    async def get_relationship_chain(
        self, workspace_ids: list[str]
    ) -> list[dict[str, Any]]:
        """Fetch artifact relationships across all given workspace IDs.

        Relationships are written automatically when each stage completes:
          user-story → epic/feature   (refines)
          feature    → epic           (refines)
          lld/code   → user-story     (implements)
          api-test   → code           (tests)

        Accepts a list so a mini-workspace traceability page can merge results
        from the global workspace, architecture workspace, developer workspace, etc.
        """
        if not workspace_ids:
            return []
        placeholders = ", ".join(["%s"] * len(workspace_ids))
        sql = f"""
            SELECT source_artifact_id, target_artifact_id,
                   relationship_type, description, created_at
            FROM {_SCHEMA}.fe_artifact_relationships
            WHERE workspace_id IN ({placeholders})
            ORDER BY created_at ASC
        """
        try:
            return await self.fetch_all(sql, tuple(workspace_ids))
        except Exception:
            log.warning("ArtifactTraceabilityDAO.get_relationship_chain failed", exc_info=True)
            return []

    # ── KB Grounding (GROUNDS links from fe_artifact_links) ────────────────

    async def get_kb_grounding(
        self, workspace_ids: list[str]
    ) -> list[dict[str, Any]]:
        """KB cards grounded across all given workspace IDs, with coverage counts.

        Reads GROUNDS edges from fe_artifact_links — written by add_grounds_link()
        whenever an artifact cites a KB card.  Accepts a list so multiple
        workspaces (global, architecture, developer, tester) can be merged.
        """
        if not workspace_ids:
            return []
        placeholders = ", ".join(["%s"] * len(workspace_ids))
        sql = f"""
            SELECT to_kb_card_id,
                   stage,
                   COUNT(*)        AS coverage_count,
                   MAX(created_at) AS last_linked_at
            FROM {_SCHEMA}.fe_artifact_links
            WHERE workspace_id  IN ({placeholders})
              AND link_type     = 'GROUNDS'
              AND to_kb_card_id IS NOT NULL
            GROUP BY to_kb_card_id, stage
            ORDER BY coverage_count DESC, to_kb_card_id
        """
        try:
            return await self.fetch_all(sql, tuple(workspace_ids))
        except Exception:
            log.warning("ArtifactTraceabilityDAO.get_kb_grounding failed", exc_info=True)
            return []

    # ── Coverage matrix ────────────────────────────────────────────────────

    async def get_coverage_matrix(
        self, workspace_ids: list[str]
    ) -> list[dict[str, Any]]:
        """Per-KB-card, per-stage grounding counts across all given workspace IDs."""
        if not workspace_ids:
            return []
        placeholders = ", ".join(["%s"] * len(workspace_ids))
        sql = f"""
            SELECT to_kb_card_id, stage, COUNT(*) AS link_count
            FROM {_SCHEMA}.fe_artifact_links
            WHERE workspace_id   IN ({placeholders})
              AND link_type      = 'GROUNDS'
              AND to_kb_card_id IS NOT NULL
            GROUP BY to_kb_card_id, stage
            ORDER BY to_kb_card_id, stage
        """
        try:
            return await self.fetch_all(sql, tuple(workspace_ids))
        except Exception:
            log.warning("ArtifactTraceabilityDAO.get_coverage_matrix failed", exc_info=True)
            return []

    # ── Chain links (new IMPLEMENTS / TRACES_TO / DERIVES_FROM) ───────────

    async def insert_link(
        self,
        *,
        workspace_id: str,
        from_artifact_id: str,
        to_artifact_id: str,
        link_type: str,
        stage: str,
        persona: str = "system",
        artifact_kind: str | None = None,
    ) -> dict[str, Any] | None:
        """Insert one traceability hop into fe_artifact_links; idempotent via ON CONFLICT DO NOTHING.

        Returns the inserted row dict, or None if the link already existed or an error occurred.
        Requires DBA to extend the link_type CHECK constraint to include IMPLEMENTS, TRACES_TO.
        """
        sql = f"""
            INSERT INTO {_SCHEMA}.fe_artifact_links
                (workspace_id, from_artifact_id, to_artifact_id,
                 link_type, stage, persona, artifact_kind)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT DO NOTHING
            RETURNING link_id, link_type, stage,
                      from_artifact_id, to_artifact_id, created_at
        """
        try:
            return await self.fetch_one(
                sql,
                (workspace_id, from_artifact_id, to_artifact_id,
                 link_type, stage, persona, artifact_kind),
            )
        except Exception:
            log.warning("ArtifactTraceabilityDAO.insert_link failed", exc_info=True)
            return None
