"""Data Access Layer for artifact links — psycopg3 only, no SQLAlchemy.

Persists artifact-to-artifact links to traceability schema.
Schema name loaded from config (not hardcoded).

Uses the shared psycopg3 async pool (dao.postgres) — raw SQL, no ORM.
Tables must be pre-created by DBA; DAO does NOT create tables.
"""

from datetime import datetime
from typing import Optional

from app.dao.base import BaseRepository
from app.config.settings import get_settings
from app.lifecycle.traceability.schema import ArtifactLink
from app.utils.logging import log



class ArtifactLinkDAO(BaseRepository):
    """Data Access Object for artifact-to-artifact semantic links.

    Writes to fe_artifact_relationships (the canonical relationship table shared
    with ArtifactRelationshipDAO).  ``link_type`` values are normalised to
    lowercase to match the VALID_TYPES expected by that table
    (e.g. 'DERIVES_FROM' → 'derives_from').
    """

    async def create(
        self,
        workspace_id: str,
        source_artifact_id: str,
        target_artifact_id: str,
        link_type: str,
        reason: Optional[str] = None,
    ) -> ArtifactLink:
        """Create a typed link between two artifacts.

        On conflict (same source→target+type) the description is updated
        in-place so re-runs remain idempotent.
        """
        now = datetime.utcnow().isoformat() + "Z"
        rel_type = link_type.lower()  # normalise DERIVES_FROM → derives_from
        link = ArtifactLink(
            workspace_id=workspace_id,
            source_artifact_id=source_artifact_id,
            target_artifact_id=target_artifact_id,
            link_type=rel_type,
            reason=reason,
            created_at=now,
        )

        schema = get_settings().PG_SCHEMA
        try:
            await self.execute(
                f"""
                INSERT INTO {schema}.fe_artifact_relationships
                (workspace_id, source_artifact_id, target_artifact_id,
                 relationship_type, description, created_at)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (workspace_id, source_artifact_id, target_artifact_id, relationship_type)
                DO UPDATE SET
                    description = EXCLUDED.description,
                    updated_at  = CURRENT_TIMESTAMP
                """,
                (
                    link.workspace_id,
                    link.source_artifact_id,
                    link.target_artifact_id,
                    rel_type,
                    link.reason or f"{rel_type} relationship",
                    link.created_at,
                ),
            )
        except Exception as e:
            log.error(f"[traceability] Failed to create link {source_artifact_id} → {target_artifact_id}: {e}")
            raise

        log.debug(f"[traceability] Link created: {source_artifact_id} --[{rel_type}]--> {target_artifact_id}")
        return link

    async def get_upstream(self, workspace_id: str, artifact_id: str) -> list[ArtifactLink]:
        """Get all artifacts that this artifact derives from (upstream / target side)."""
        schema = get_settings().PG_SCHEMA
        try:
            results = await self.fetch_all(
                f"""
                SELECT workspace_id, source_artifact_id, target_artifact_id,
                       relationship_type AS link_type, description AS reason, created_at
                FROM {schema}.fe_artifact_relationships
                WHERE workspace_id = %s
                  AND source_artifact_id = %s
                ORDER BY created_at DESC
                """,
                (workspace_id, artifact_id),
            )
            return [
                ArtifactLink(
                    workspace_id=row["workspace_id"],
                    source_artifact_id=row["source_artifact_id"],
                    target_artifact_id=row["target_artifact_id"],
                    link_type=row["link_type"],
                    reason=row["reason"],
                    created_at=str(row["created_at"]),
                )
                for row in results
            ]
        except Exception as e:
            log.error(f"[traceability] Failed to get upstream links for {artifact_id}: {e}")
            return []

    async def get_downstream(self, workspace_id: str, artifact_id: str) -> list[ArtifactLink]:
        """Get all artifacts that derive from this artifact (downstream / source side)."""
        schema = get_settings().PG_SCHEMA
        try:
            results = await self.fetch_all(
                f"""
                SELECT workspace_id, source_artifact_id, target_artifact_id,
                       relationship_type AS link_type, description AS reason, created_at
                FROM {schema}.fe_artifact_relationships
                WHERE workspace_id = %s
                  AND target_artifact_id = %s
                ORDER BY created_at DESC
                """,
                (workspace_id, artifact_id),
            )
            return [
                ArtifactLink(
                    workspace_id=row["workspace_id"],
                    source_artifact_id=row["source_artifact_id"],
                    target_artifact_id=row["target_artifact_id"],
                    link_type=row["link_type"],
                    reason=row["reason"],
                    created_at=str(row["created_at"]),
                )
                for row in results
            ]
        except Exception as e:
            log.error(f"[traceability] Failed to get downstream links for {artifact_id}: {e}")
            return []


class GroundsLinkDAO(BaseRepository):
    """Data Access Object for artifact-to-KB-card GROUNDS links (psycopg3 + BaseRepository).

    GROUNDS links connect workspace artifacts to KB cards.
    Unlike ArtifactLinkDAO, this does NOT require KB IDs to exist in fe_workspace_artifacts.
    Uses dedicated table: traceability schema fe_artifact_kb_grounds
    """

    async def create(
        self,
        workspace_id: str,
        artifact_id: str,
        kb_card_id: str,
        kb_card_kind: str,
        kb_card_label: str,
        source_locus: str,
        applied_because: str,
        persona: str,
        kb_version: str,
        stage: str,
        reason: str,
        source_item_id: str | None = None,
    ) -> None:
        """Create a GROUNDS link from artifact to KB card.

        Args:
            workspace_id: Workspace ID
            artifact_id: Workspace artifact ID (must exist in fe_workspace_artifacts)
            kb_card_id: KB card ID (e.g., BR-JAUTO-001) — no FK required
            kb_card_kind: Kind of KB card ('BR', 'FR', 'SCR', 'WF', 'SYS', etc.)
            kb_card_label: Display name from KB
            source_locus: Evidence location (file §section char:start-end)
            applied_because: How link was created ('RETRIEVAL_MATCH' or 'GRAPH_WALK')
            persona: Persona who initiated ('ba', 'architect', etc.)
            kb_version: KB version at time of linking
            stage: SDLC stage ('ANALYSIS', 'FSD', 'ARCHITECTURE', etc.)
            reason: Full reason text
            source_item_id: The item WITHIN the source artifact that grounds to this card (e.g. a
                story_id) — enables item-level trace. NULL for artifact-level groundings.
        """
        now = datetime.utcnow().isoformat() + "Z"
        schema = get_settings().PG_SCHEMA

        try:
            await self.execute(
                f"""
                INSERT INTO {schema}.fe_artifact_kb_grounds
                (workspace_id, artifact_id, source_item_id, kb_card_id, kb_card_kind, kb_card_label,
                 source_locus, applied_because, persona, kb_version, stage, reason, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    workspace_id,
                    artifact_id,
                    source_item_id,
                    kb_card_id,
                    kb_card_kind,
                    kb_card_label,
                    source_locus,
                    applied_because,
                    persona,
                    kb_version,
                    stage,
                    reason,
                    now,
                ),
            )
            log.debug(
                f"[grounds-dao] GROUNDS link created: {artifact_id} ← {kb_card_id} ({applied_because})"
            )
        except Exception as e:
            log.warning(
                f"[grounds-dao] Failed to create GROUNDS link {artifact_id} → {kb_card_id}: {e}"
            )

    async def get_by_artifact(self, workspace_id: str, artifact_id: str) -> list[dict]:
        """Get all KB cards grounding an artifact.

        Args:
            workspace_id: Workspace ID
            artifact_id: Artifact ID to query

        Returns:
            List of KB card references
        """
        schema = get_settings().PG_SCHEMA
        try:
            results = await self.fetch_all(
                f"""
                SELECT kb_card_id, kb_card_kind, kb_card_label, source_locus,
                       applied_because, reason, created_at
                FROM {schema}.fe_artifact_kb_grounds
                WHERE workspace_id = %s AND artifact_id = %s
                ORDER BY created_at DESC
                """,
                (workspace_id, artifact_id),
            )
            return [dict(row) for row in results]
        except Exception as e:
            log.warning(
                f"[grounds-dao] Failed to get GROUNDS links for artifact {artifact_id}: {e}"
            )
            return []

    async def get_by_kb_card(self, workspace_id: str, kb_card_id: str) -> list[dict]:
        """Get all artifacts grounded by a KB card.

        Args:
            workspace_id: Workspace ID
            kb_card_id: KB card ID to query

        Returns:
            List of artifacts grounded by this card
        """
        schema = get_settings().PG_SCHEMA
        try:
            results = await self.fetch_all(
                f"""
                SELECT artifact_id, kb_card_kind, applied_because, reason, created_at
                FROM {schema}.fe_artifact_kb_grounds
                WHERE workspace_id = %s AND kb_card_id = %s
                ORDER BY created_at DESC
                """,
                (workspace_id, kb_card_id),
            )
            return [dict(row) for row in results]
        except Exception as e:
            log.warning(
                f"[grounds-dao] Failed to get GROUNDS links for KB card {kb_card_id}: {e}"
            )
            return []
