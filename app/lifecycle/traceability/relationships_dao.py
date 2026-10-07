"""Data Access Layer for artifact-to-artifact relationships — psycopg3 only.

Manages relationships between workspace artifacts (derives_from, implements, depends_on, tests).
Each relationship has a type and justification (description).

Uses the shared psycopg3 async pool (dao.postgres) — raw SQL, no ORM.
Schema name loaded from config (not hardcoded).
"""

from datetime import datetime
from typing import Optional

from app.dao.base import BaseRepository
from app.config.settings import get_settings
from app.utils.logging import log


class ArtifactRelationshipDAO(BaseRepository):
    """Data Access Object for artifact-to-artifact relationships (psycopg3 + BaseRepository)."""

    # Valid relationship types
    VALID_TYPES = frozenset({
        "derives_from",      # requirement → analysis
        "implements",        # analysis → design / design → code
        "depends_on",        # design → code / code → other systems
        "tests",             # test → code
        "extends",           # variant of implements
        "refines",           # variant of implements
        "custom",            # user-defined
    })

    async def create(
        self,
        workspace_id: str,
        source_artifact_id: str,
        target_artifact_id: str,
        relationship_type: str,
        description: Optional[str] = None,
    ) -> None:
        """Create or update a relationship between two artifacts.

        On duplicate (same source→target + type), updates the description instead of creating a new entry.
        This prevents duplicate relationships when re-running the same stage.

        Args:
            workspace_id: Workspace ID
            source_artifact_id: Source artifact ID (from)
            target_artifact_id: Target artifact ID (to)
            relationship_type: Type of relationship (derives_from, implements, depends_on, tests, etc.)
            description: Justification text (why this relationship exists)
        """
        # Validate relationship type
        if relationship_type not in self.VALID_TYPES:
            log.warning(
                f"[relationships-dao] Invalid relationship type: {relationship_type}. "
                f"Valid types: {self.VALID_TYPES}"
            )
            return

        now = datetime.utcnow().isoformat() + "Z"
        desc = description or f"{relationship_type} relationship"
        schema = get_settings().PG_SCHEMA

        try:
            # Use INSERT ... ON CONFLICT ... DO UPDATE to handle re-runs (PostgreSQL)
            # This is the idempotent upsert pattern that prevents duplicates on re-runs
            await self.execute(
                f"""
                INSERT INTO {schema}.fe_artifact_relationships
                (workspace_id, source_artifact_id, target_artifact_id, relationship_type, description, created_at)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (workspace_id, source_artifact_id, target_artifact_id, relationship_type)
                DO UPDATE SET
                    description = EXCLUDED.description,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    workspace_id,
                    source_artifact_id,
                    target_artifact_id,
                    relationship_type,
                    desc,
                    now,
                ),
            )
            log.debug(
                f"[relationships-dao] Relationship created/updated: {source_artifact_id} "
                f"--[{relationship_type}]--> {target_artifact_id}"
            )
        except Exception as e:
            log.error(
                f"[relationships-dao] FAILED to create relationship "
                f"{source_artifact_id} → {target_artifact_id}: {type(e).__name__}: {e}",
                exc_info=True
            )

    async def get_outgoing(
        self, workspace_id: str, artifact_id: str
    ) -> list[dict]:
        """Get all relationships FROM this artifact (outgoing edges).

        Args:
            workspace_id: Workspace ID
            artifact_id: Artifact ID to query

        Returns:
            List of relationships where artifact_id is the source
        """
        try:
            schema = get_settings().PG_SCHEMA
            results = await self.fetch_all(
                f"""
                SELECT id, source_artifact_id, target_artifact_id, relationship_type, description, created_at
                FROM {schema}.fe_artifact_relationships
                WHERE workspace_id = %s AND source_artifact_id = %s
                ORDER BY created_at DESC
                """,
                (workspace_id, artifact_id),
            )
            return [dict(row) for row in results]
        except Exception as e:
            log.warning(
                f"[relationships-dao] Failed to get outgoing relationships for {artifact_id}: {e}"
            )
            return []

    async def get_incoming(
        self, workspace_id: str, artifact_id: str
    ) -> list[dict]:
        """Get all relationships TO this artifact (incoming edges).

        Args:
            workspace_id: Workspace ID
            artifact_id: Artifact ID to query

        Returns:
            List of relationships where artifact_id is the target
        """
        try:
            schema = get_settings().PG_SCHEMA
            results = await self.fetch_all(
                f"""
                SELECT id, source_artifact_id, target_artifact_id, relationship_type, description, created_at
                FROM {schema}.fe_artifact_relationships
                WHERE workspace_id = %s AND target_artifact_id = %s
                ORDER BY created_at DESC
                """,
                (workspace_id, artifact_id),
            )
            return [dict(row) for row in results]
        except Exception as e:
            log.warning(
                f"[relationships-dao] Failed to get incoming relationships for {artifact_id}: {e}"
            )
            return []

    async def get_all(self, workspace_id: str) -> list[dict]:
        """Get all relationships in a workspace.

        Args:
            workspace_id: Workspace ID

        Returns:
            List of all relationships
        """
        try:
            schema = get_settings().PG_SCHEMA
            results = await self.fetch_all(
                f"""
                SELECT id, source_artifact_id, target_artifact_id, relationship_type, description, created_at
                FROM {schema}.fe_artifact_relationships
                WHERE workspace_id = %s
                ORDER BY created_at DESC
                """,
                (workspace_id,),
            )
            return [dict(row) for row in results]
        except Exception as e:
            log.warning(
                f"[relationships-dao] Failed to get all relationships for {workspace_id}: {e}"
            )
            return []

    async def delete(
        self,
        workspace_id: str,
        source_artifact_id: str,
        target_artifact_id: str,
    ) -> None:
        """Delete a relationship between two artifacts.

        Args:
            workspace_id: Workspace ID
            source_artifact_id: Source artifact ID
            target_artifact_id: Target artifact ID
        """
        try:
            schema = get_settings().PG_SCHEMA
            await self.execute(
                f"""
                DELETE FROM {schema}.fe_artifact_relationships
                WHERE workspace_id = %s AND source_artifact_id = %s AND target_artifact_id = %s
                """,
                (workspace_id, source_artifact_id, target_artifact_id),
            )
            log.debug(
                f"[relationships-dao] Relationship deleted: {source_artifact_id} → {target_artifact_id}"
            )
        except Exception as e:
            log.warning(
                f"[relationships-dao] Failed to delete relationship "
                f"{source_artifact_id} → {target_artifact_id}: {e}"
            )

    async def exists(
        self,
        workspace_id: str,
        source_artifact_id: str,
        target_artifact_id: str,
    ) -> bool:
        """Check if a relationship already exists.

        Args:
            workspace_id: Workspace ID
            source_artifact_id: Source artifact ID
            target_artifact_id: Target artifact ID

        Returns:
            True if relationship exists, False otherwise
        """
        try:
            schema = get_settings().PG_SCHEMA
            result = await self.fetch_one(
                f"""
                SELECT COUNT(*) as cnt FROM {schema}.fe_artifact_relationships
                WHERE workspace_id = %s AND source_artifact_id = %s AND target_artifact_id = %s
                """,
                (workspace_id, source_artifact_id, target_artifact_id),
            )
            return bool(result and result.get("cnt", 0) > 0)
        except Exception as e:
            log.warning(
                f"[relationships-dao] Failed to check relationship existence: {e}"
            )
            return False

    async def delete_all_for_artifact(
        self,
        workspace_id: str,
        artifact_id: str,
    ) -> None:
        """Delete all relationships (incoming and outgoing) for an artifact.

        Used during cleanup to remove all edges when an artifact is being replaced.

        Args:
            workspace_id: Workspace ID
            artifact_id: Artifact ID to delete relationships for
        """
        try:
            schema = get_settings().PG_SCHEMA
            # Delete both directions: artifact as source OR target
            await self.execute(
                f"""
                DELETE FROM {schema}.fe_artifact_relationships
                WHERE workspace_id = %s AND (source_artifact_id = %s OR target_artifact_id = %s)
                """,
                (workspace_id, artifact_id, artifact_id),
            )
            log.debug(
                f"[relationships-dao] Deleted all relationships for artifact {artifact_id}"
            )
        except Exception as e:
            log.warning(
                f"[relationships-dao] Failed to delete relationships for {artifact_id}: {e}"
            )
