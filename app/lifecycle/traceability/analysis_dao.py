"""DAO for workspace analysis (systems, scope, metrics in one table)."""

import json
from typing import Any, Dict, List, Optional

from app.dao.base import BaseRepository
from app.config.settings import get_settings
from app.utils.logging import log


class WorkspaceAnalysisDAO(BaseRepository):
    """Data access for fe_workspace_analysis table (single unified DAO).

    Uses the shared psycopg3 async pool via BaseRepository.
    Schema name loaded from config (not hardcoded).
    """

    # ── Systems Methods ────────────────────────────────────────────────────

    async def update_systems(
        self,
        workspace_id: str,
        systems: List[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        """Update systems_extracted JSON column."""
        try:
            systems_json = json.dumps(systems)
            schema = get_settings().PG_SCHEMA

            row = await self.fetch_one(
                f"""
                INSERT INTO {schema}.fe_workspace_analysis (workspace_id, systems_extracted)
                VALUES (%s, %s)
                ON CONFLICT (workspace_id)
                DO UPDATE SET systems_extracted = EXCLUDED.systems_extracted,
                              updated_at = CURRENT_TIMESTAMP
                RETURNING *;
                """,
                (workspace_id, systems_json),
            )

            return self._format_row(row) if row else None
        except Exception as e:
            log.error(f"[analysis_dao] update_systems failed: {e}")
        return None

    async def get_systems(self, workspace_id: str) -> List[Dict[str, Any]]:
        """Get systems from workspace analysis."""
        try:
            analysis = await self.get(workspace_id)
            if analysis and analysis.get("systems_extracted"):
                return analysis["systems_extracted"]
        except Exception as e:
            log.error(f"[analysis_dao] get_systems failed: {e}")
        return []

    # ── Scope Methods ──────────────────────────────────────────────────────

    async def update_scope(
        self,
        workspace_id: str,
        scope: Dict[str, List[Dict[str, Any]]],  # {in_scope, out_of_scope, deferred}
    ) -> Optional[Dict[str, Any]]:
        """Update scope_items JSON column."""
        try:
            scope_json = json.dumps(scope)
            schema = get_settings().PG_SCHEMA

            row = await self.fetch_one(
                f"""
                INSERT INTO {schema}.fe_workspace_analysis (workspace_id, scope_items)
                VALUES (%s, %s)
                ON CONFLICT (workspace_id)
                DO UPDATE SET scope_items = EXCLUDED.scope_items,
                              updated_at = CURRENT_TIMESTAMP
                RETURNING *;
                """,
                (workspace_id, scope_json),
            )

            return self._format_row(row) if row else None
        except Exception as e:
            log.error(f"[analysis_dao] update_scope failed: {e}")
        return None

    async def get_scope(
        self, workspace_id: str
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Get scope items from workspace analysis."""
        try:
            analysis = await self.get(workspace_id)
            if analysis and analysis.get("scope_items"):
                return analysis["scope_items"]
        except Exception as e:
            log.error(f"[analysis_dao] get_scope failed: {e}")
        return {"in_scope": [], "out_of_scope": [], "deferred": []}

    # ── Metrics Methods ────────────────────────────────────────────────────

    async def update_metrics(
        self,
        workspace_id: str,
        metrics: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """Update metrics JSON column."""
        try:
            metrics_json = json.dumps(metrics)
            schema = get_settings().PG_SCHEMA

            row = await self.fetch_one(
                f"""
                INSERT INTO {schema}.fe_workspace_analysis (workspace_id, metrics)
                VALUES (%s, %s)
                ON CONFLICT (workspace_id)
                DO UPDATE SET metrics = EXCLUDED.metrics,
                              updated_at = CURRENT_TIMESTAMP
                RETURNING *;
                """,
                (workspace_id, metrics_json),
            )

            return self._format_row(row) if row else None
        except Exception as e:
            log.error(f"[analysis_dao] update_metrics failed: {e}")
        return None

    async def get_metrics(self, workspace_id: str) -> Dict[str, Any]:
        """Get metrics from workspace analysis."""
        try:
            analysis = await self.get(workspace_id)
            if analysis and analysis.get("metrics"):
                return analysis["metrics"]
        except Exception as e:
            log.error(f"[analysis_dao] get_metrics failed: {e}")
        return {}

    # ── PRD Impact Sections (Aug 18, 2026) ────────────────────────────────

    async def update_analysis_sections(
        self,
        workspace_id: str,
        what_is_modified: List[Dict[str, Any]],
        where_changes_needed: List[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        """Save the two impact sections for PRD (what_is_modified + where_changes_needed)"""
        try:
            what_is_modified_json = json.dumps(what_is_modified)
            where_changes_needed_json = json.dumps(where_changes_needed)
            schema = get_settings().PG_SCHEMA

            row = await self.fetch_one(
                f"""
                INSERT INTO {schema}.fe_workspace_analysis (workspace_id, what_is_modified, where_changes_needed)
                VALUES (%s, %s, %s)
                ON CONFLICT (workspace_id)
                DO UPDATE SET
                    what_is_modified = EXCLUDED.what_is_modified,
                    where_changes_needed = EXCLUDED.where_changes_needed,
                    updated_at = CURRENT_TIMESTAMP
                RETURNING *;
                """,
                (workspace_id, what_is_modified_json, where_changes_needed_json),
            )

            return self._format_row(row) if row else None
        except Exception as e:
            log.error(f"[analysis_dao] update_analysis_sections failed: {e}")
        return None

    async def get_what_is_modified(self, workspace_id: str) -> List[Dict[str, Any]]:
        """Get direct change targets for PRD section 3.1"""
        try:
            analysis = await self.get(workspace_id)
            if analysis and analysis.get("what_is_modified"):
                return analysis["what_is_modified"]
        except Exception as e:
            log.error(f"[analysis_dao] get_what_is_modified failed: {e}")
        return []

    async def get_where_changes_needed(self, workspace_id: str) -> List[Dict[str, Any]]:
        """Get downstream impact for PRD section 3.2"""
        try:
            analysis = await self.get(workspace_id)
            if analysis and analysis.get("where_changes_needed"):
                return analysis["where_changes_needed"]
        except Exception as e:
            log.error(f"[analysis_dao] get_where_changes_needed failed: {e}")
        return []

    # ── Core Methods ───────────────────────────────────────────────────────

    async def get(self, workspace_id: str) -> Optional[Dict[str, Any]]:
        """Get complete analysis record for a workspace."""
        try:
            schema = get_settings().PG_SCHEMA
            row = await self.fetch_one(
                f"SELECT * FROM {schema}.fe_workspace_analysis WHERE workspace_id = %s",
                (workspace_id,),
            )
            return self._format_row(row) if row else None
        except Exception as e:
            log.error(f"[analysis_dao] get failed: {e}")
        return None

    async def create(self, workspace_id: str) -> Optional[Dict[str, Any]]:
        """Create empty analysis record for a workspace."""
        try:
            schema = get_settings().PG_SCHEMA
            row = await self.fetch_one(
                f"""
                INSERT INTO {schema}.fe_workspace_analysis (workspace_id)
                VALUES (%s)
                ON CONFLICT (workspace_id) DO NOTHING
                RETURNING *;
                """,
                (workspace_id,),
            )

            return self._format_row(row) if row else None
        except Exception as e:
            log.error(f"[analysis_dao] create failed: {e}")
        return None

    def _format_row(self, row) -> Dict[str, Any]:
        """Format database row into dictionary."""
        if not row:
            return {}

        systems = []
        scope = {"in_scope": [], "out_of_scope": [], "deferred": []}
        metrics = {}
        what_is_modified = []
        where_changes_needed = []

        # Parse JSON columns
        try:
            if row.get("systems_extracted"):
                systems = json.loads(row["systems_extracted"])
        except (json.JSONDecodeError, TypeError):
            systems = []

        try:
            if row.get("scope_items"):
                scope = json.loads(row["scope_items"])
        except (json.JSONDecodeError, TypeError):
            scope = {"in_scope": [], "out_of_scope": [], "deferred": []}

        try:
            if row.get("metrics"):
                metrics = json.loads(row["metrics"])
        except (json.JSONDecodeError, TypeError):
            metrics = {}

        try:
            if row.get("what_is_modified"):
                what_is_modified = json.loads(row["what_is_modified"])
        except (json.JSONDecodeError, TypeError):
            what_is_modified = []

        try:
            if row.get("where_changes_needed"):
                where_changes_needed = json.loads(row["where_changes_needed"])
        except (json.JSONDecodeError, TypeError):
            where_changes_needed = []

        return {
            "id": row.get("id"),
            "workspace_id": row.get("workspace_id"),
            "systems_extracted": systems,
            "scope_items": scope,
            "metrics": metrics,
            "what_is_modified": what_is_modified,
            "where_changes_needed": where_changes_needed,
            "extraction_method": row.get("extraction_method"),
            "created_at": str(row.get("created_at"))
            if row.get("created_at")
            else None,
            "updated_at": str(row.get("updated_at"))
            if row.get("updated_at")
            else None,
        }
