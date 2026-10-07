"""FE workspace data layer — psycopg 3 async repositories over the shared pool.

Persistence ONLY: no LLM, no business rules. The state machine (guards) and the service layer sit
ABOVE these repos and validate transitions before calling ``set_state`` / ``close``. Tables are
defined in ``migrations/0001_fe_workspace.sql`` (docs/09 §3.6; shapes: docs/19 §3). Every repository
subclasses ``BaseRepository`` (delegates to ``dao.postgres``) so callers depend on
``RepositoryProtocol``, not on psycopg.
"""

from __future__ import annotations

from typing import Any

from psycopg.types.json import Jsonb

from app.dao.base import BaseRepository

_WS_COLS = (
    "workspace_id, gear_id, title, type, route, state, pinned_kb_version, "
    "requirement_text, created_by, created_at, updated_at, closed_at"
)
_ART_COLS = (
    "artifact_id, workspace_id, kind, template_id, template_version, content, s3_uri, grounding_score, created_at, "
    "triggered_by_user, triggered_by_name, triggered_by_persona, reviewed_by_user, reviewed_by_name, reviewed_at"
)


class WorkspaceRepository(BaseRepository):
    """CRUD + state persistence for ``fe_workspaces`` (the service validates transitions first)."""

    async def create(
        self,
        *,
        workspace_id: str,
        type: str,
        state: str,
        gear_id: str = "japan",
        title: str | None = None,
        route: str | None = None,
        pinned_kb_version: str | None = None,
        requirement_text: str | None = None,
        created_by: str | None = None,
    ) -> dict[str, Any] | None:
        sql = (
            "INSERT INTO fe_workspaces "
            "(workspace_id, gear_id, title, type, route, state, pinned_kb_version, requirement_text, created_by) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
            f"RETURNING {_WS_COLS}"
        )
        return await self.fetch_one(
            sql, (workspace_id, gear_id, title, type, route, state, pinned_kb_version, requirement_text, created_by)
        )

    async def get(self, workspace_id: str) -> dict[str, Any] | None:
        return await self.fetch_one(f"SELECT {_WS_COLS} FROM fe_workspaces WHERE workspace_id = %s", (workspace_id,))

    async def list(
        self,
        *,
        gear_id: str | None = None,
        state: str | None = None,
        type: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        sql = f"SELECT {_WS_COLS} FROM fe_workspaces WHERE 1 = 1"
        params: list[Any] = []
        if gear_id:
            sql += " AND gear_id = %s"
            params.append(gear_id)
        if state:
            sql += " AND state = %s"
            params.append(state)
        if type:
            sql += " AND type = %s"
            params.append(type)
        sql += " ORDER BY created_at DESC LIMIT %s OFFSET %s"
        params += [limit, offset]
        return await self.fetch_all(sql, params)

    async def set_state(self, workspace_id: str, new_state: str) -> dict[str, Any] | None:
        """Persist a state change. The caller (service) MUST validate the transition beforehand."""
        return await self.fetch_one(
            f"UPDATE fe_workspaces SET state = %s, updated_at = now() WHERE workspace_id = %s RETURNING {_WS_COLS}",
            (new_state, workspace_id),
        )

    async def close(self, workspace_id: str) -> dict[str, Any] | None:
        return await self.fetch_one(
            "UPDATE fe_workspaces SET state = 'CLOSED', closed_at = now(), updated_at = now() "
            f"WHERE workspace_id = %s RETURNING {_WS_COLS}",
            (workspace_id,),
        )


class ArtifactRepository(BaseRepository):
    """Artifacts (``fe_workspace_artifacts``) + the two-edge traceability links (``fe_artifact_links``)."""

    async def add(
        self,
        *,
        artifact_id: str,
        workspace_id: str,
        kind: str,
        template_id: str | None = None,
        template_version: str | None = None,
        content: str | None = None,
        s3_uri: str | None = None,
        grounding_score: float | None = None,
        triggered_by_user: str | None = None,
        triggered_by_name: str | None = None,
        triggered_by_persona: str | None = None,
        # Machine provenance (migration 0009)
        version: int | None = None,
        run_id: str | None = None,
        workflow_run_id: str | None = None,
        agent_run_id: str | None = None,
        model: str | None = None,
        plugin: str | None = None,
        plugin_version: str | None = None,
    ) -> dict[str, Any] | None:
        sql = (
            "INSERT INTO fe_workspace_artifacts "
            "(artifact_id, workspace_id, kind, template_id, template_version, content, s3_uri, grounding_score,"
            " triggered_by_user, triggered_by_name, triggered_by_persona,"
            " version, run_id, workflow_run_id, agent_run_id, model, plugin, plugin_version) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
            f"RETURNING {_ART_COLS}"
        )
        return await self.fetch_one(
            sql,
            (artifact_id, workspace_id, kind, template_id, template_version, content, s3_uri, grounding_score,
             triggered_by_user, triggered_by_name, triggered_by_persona,
             version, run_id, workflow_run_id, agent_run_id, model, plugin, plugin_version),
        )

    async def set_reviewed(self, workspace_id: str, kind: str, user: str | None, name: str | None) -> None:
        """Stamp reviewed_by_* on the newest artifact of a kind (called when a stage is accepted)."""
        await self.execute(
            "UPDATE fe_workspace_artifacts SET reviewed_by_user = %s, reviewed_by_name = %s, "
            "reviewed_at = now() WHERE artifact_id = ("
            "  SELECT artifact_id FROM fe_workspace_artifacts WHERE workspace_id = %s AND kind = %s "
            "  ORDER BY created_at DESC LIMIT 1)",
            (user, name, workspace_id, kind),
        )

    async def list_for_workspace(self, workspace_id: str) -> list[dict[str, Any]]:
        return await self.fetch_all(
            f"SELECT {_ART_COLS} FROM fe_workspace_artifacts WHERE workspace_id = %s ORDER BY created_at",
            (workspace_id,),
        )

    async def latest_for_kind(self, workspace_id: str, kind: str) -> dict[str, Any] | None:
        """The newest artifact of one kind (with content) — O(1) read for the stage GET path.

        Avoids pulling EVERY artifact's content just to keep the last of one kind (which made the
        stage read scale with SDLC depth + re-run count).
        """
        return await self.fetch_one(
            f"SELECT {_ART_COLS} FROM fe_workspace_artifacts "
            "WHERE workspace_id = %s AND kind = %s AND content IS NOT NULL "
            "ORDER BY created_at DESC LIMIT 1",
            (workspace_id, kind),
        )

    async def add_grounds_link(
        self,
        *,
        workspace_id: str,
        from_artifact_id: str,
        persona: str,
        artifact_kind: str,
        to_kb_card_id: str,
        kb_version: str,
        stage: str | None = None,
        source_locus: str | None = None,
        char_span: str | None = None,
        applied_because: str | None = None,
    ) -> dict[str, Any] | None:
        """A GROUNDS edge (artifact -> KB card). ``char_span`` is an int4range literal, e.g. ``'[1240,1580)'``."""
        sql = (
            "INSERT INTO fe_artifact_links "
            "(workspace_id, from_artifact_id, persona, artifact_kind, stage, link_type, "
            " to_kb_card_id, kb_version, source_locus, char_span, applied_because) "
            "VALUES (%s, %s, %s, %s, %s, 'GROUNDS', %s, %s, %s, %s::int4range, %s) "
            "RETURNING link_id, link_type, to_kb_card_id, kb_version"
        )
        return await self.fetch_one(
            sql,
            (
                workspace_id,
                from_artifact_id,
                persona,
                artifact_kind,
                stage,
                to_kb_card_id,
                kb_version,
                source_locus,
                char_span,
                applied_because,
            ),
        )

    async def add_derives_link(
        self,
        *,
        workspace_id: str,
        from_artifact_id: str,
        persona: str,
        artifact_kind: str,
        to_artifact_id: str,
        stage: str | None = None,
        diagram_ref: str | None = None,
    ) -> dict[str, Any] | None:
        """A DERIVES_FROM edge (artifact -> upstream artifact)."""
        sql = (
            "INSERT INTO fe_artifact_links "
            "(workspace_id, from_artifact_id, persona, artifact_kind, stage, link_type, to_artifact_id, diagram_ref) "
            "VALUES (%s, %s, %s, %s, %s, 'DERIVES_FROM', %s, %s) "
            "RETURNING link_id, link_type, to_artifact_id"
        )
        return await self.fetch_one(
            sql, (workspace_id, from_artifact_id, persona, artifact_kind, stage, to_artifact_id, diagram_ref)
        )

    async def list_links(self, workspace_id: str) -> list[dict[str, Any]]:
        return await self.fetch_all(
            "SELECT link_id, from_artifact_id, link_type, to_kb_card_id, kb_version, to_artifact_id, "
            "persona, artifact_kind, stage, source_locus, applied_because, diagram_ref "
            "FROM fe_artifact_links WHERE workspace_id = %s ORDER BY link_id",
            (workspace_id,),
        )


class KnowledgeGapRepository(BaseRepository):
    """Typed review-queue items (``fe_kb_knowledge_gaps``) raised during a workspace."""

    async def add(
        self,
        *,
        kb_version: str,
        gap_type: str,
        description: str,
        subject_id: str | None = None,
        raised_by: str | None = None,
        workspace_id: str | None = None,
        stage_key: str | None = None,
    ) -> dict[str, Any] | None:
        return await self.fetch_one(
            "INSERT INTO fe_kb_knowledge_gaps "
            "(kb_version, gap_type, subject_id, description, raised_by, workspace_id, stage_key) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id, kb_version, gap_type, status",
            (kb_version, gap_type, subject_id, description, raised_by, workspace_id, stage_key),
        )

    async def list(self, *, status: str | None = None) -> list[dict[str, Any]]:
        sql = (
            "SELECT id, kb_version, gap_type, subject_id, description, raised_by, status, resolved_card_id "
            "FROM fe_kb_knowledge_gaps"
        )
        params: list[Any] = []
        if status:
            sql += " WHERE status = %s"
            params.append(status)
        sql += " ORDER BY id"
        return await self.fetch_all(sql, params)

    async def resolve(
        self, gap_id: int, *, resolved_card_id: str | None = None, status: str = "RESOLVED"
    ) -> dict[str, Any] | None:
        return await self.fetch_one(
            "UPDATE fe_kb_knowledge_gaps SET status = %s, resolved_card_id = %s WHERE id = %s "
            "RETURNING id, status, resolved_card_id",
            (status, resolved_card_id, gap_id),
        )


class AuditRepository(BaseRepository):
    """Append-only audit + state-transition provenance (``fe_audit_log``)."""

    async def append(
        self,
        *,
        actor: str,
        action: str,
        persona: str | None = None,
        resource_id: str | None = None,
        workspace_id: str | None = None,
        correlation_id: str | None = None,
        result: str | None = None,
        deny_reason: str | None = None,
        before_state: dict[str, Any] | None = None,
        after_state: dict[str, Any] | None = None,
    ) -> int:
        return await self.execute(
            "INSERT INTO fe_audit_log "
            "(actor, persona, action, resource_id, workspace_id, correlation_id, result, deny_reason, "
            " before_state, after_state) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                actor,
                persona,
                action,
                resource_id,
                workspace_id,
                correlation_id,
                result,
                deny_reason,
                Jsonb(before_state) if before_state is not None else None,
                Jsonb(after_state) if after_state is not None else None,
            ),
        )
