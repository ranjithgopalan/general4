"""Workspace service — the framework's orchestration over the data layer + state machine.

Framework ONLY (no LLM): it enforces the pure state machine (``app.domain.workspace_state``) on top of
the repositories (``app.dao.workspace_dao``), maps rows to the ``Workspace`` model, and writes an audit
trail (before -> after) for every mutation. The API (Step 4) and the conductor (Step 5) call this
service; persona / LLM logic is out of scope (docs/19 §10).

Repositories are injected so the service is unit-testable without a database.
"""

from __future__ import annotations

import uuid
from typing import Any

from app.dao import graph_dao
from app.dao.postgres import get_pool
from app.dao.workspace_dao import ArtifactRepository, AuditRepository, WorkspaceRepository
from app.domain.workspace_state import TransitionError, WorkspaceStateMachine
from app.core.request_context import current_actor
from app.models.workspace import Route, Workspace, WorkspaceState, WorkspaceType
from app.services.refresh_queue import RefreshEnqueuer, get_refresh_enqueuer
from app.utils.exceptions import ResourceNotFoundError, ValidationError
from app.utils.logging import log

_SM = WorkspaceStateMachine

# persona id (from the "persona:<id>" actor) → friendly agent label for Triggered By.
_PERSONA_AGENT = {
    "po": "Product Owner Agent", "ba": "Business Analyst Agent", "architect": "Architect Agent",
    "scrum": "Scrum Master Agent", "dev": "Developer Agent", "developer": "Developer Agent",
    "qa": "QA Agent", "devops": "DevOps Agent",
}
# workspace state being accepted → the artifact kind reviewed at that transition (Reviewed/Approved By).
_STATE_KIND = {
    "INTAKE": "intake", "ANALYSIS": "analysis", "FSD": "fsd", "ARCHITECTURE": "srd",
    "STORIES": "stories", "DEVELOPMENT": "dev", "QA_TESTING": "test-plan",
}


class WorkspaceService:
    """Create/read/transition/close workspaces + attach artifacts, guarded by the state machine."""

    def __init__(
        self,
        workspaces: WorkspaceRepository | None = None,
        artifacts: ArtifactRepository | None = None,
        audit: AuditRepository | None = None,
        refresh: RefreshEnqueuer | None = None,
    ) -> None:
        self._ws = workspaces or WorkspaceRepository()
        self._art = artifacts or ArtifactRepository()
        self._audit = audit or AuditRepository()
        self._refresh = refresh or get_refresh_enqueuer()

    # ── create ───────────────────────────────────────────────────────────────────
    async def create(
        self,
        *,
        type: str,
        title: str | None = None,
        requirement_text: str | None = None,
        gear_id: str = "japan",
        route: str | None = None,
        pinned_kb_version: str | None = None,
        actor: str = "system",
        workspace_id: str | None = None,
    ) -> Workspace:
        """Open a new workspace at INTAKE. ``type`` is validated; ``route`` defaults from the type."""
        wtype = WorkspaceType(type)  # raises ValueError on an unknown type
        wroute = Route(route) if route else _SM.default_route(wtype)
        wid = workspace_id or f"ws-{uuid.uuid4().hex[:12]}"
        state = _SM.initial_state()
        # Anchor the workspace to the current ACTIVE KB snapshot (so grounding + GROUNDS links
        # reference a real kb_version). Best-effort — no ACTIVE version yet leaves it null.
        pinned = pinned_kb_version
        if pinned is None:
            try:
                pinned = await graph_dao.active_version(get_pool(), gear_id)
            except Exception as exc:  # noqa: BLE001 — pinning is best-effort, never blocks create
                log.warning(f"[workspace] could not resolve ACTIVE kb_version for {gear_id}: {exc}")
        row = await self._ws.create(
            workspace_id=wid,
            type=wtype.value,
            state=state.value,
            gear_id=gear_id,
            title=title,
            route=wroute.value if wroute else None,
            pinned_kb_version=pinned,
            requirement_text=requirement_text,
            created_by=actor,
        )
        ws = Workspace.from_row(row)
        await self._audit_event(actor, "workspace.create", ws, before=None, after={"state": ws.state.value})
        return ws

    # ── reads ────────────────────────────────────────────────────────────────────
    async def get(self, workspace_id: str) -> Workspace:
        row = await self._ws.get(workspace_id)
        if not row:
            raise ResourceNotFoundError(f"workspace not found: {workspace_id}", {"workspace_id": workspace_id})
        return Workspace.from_row(row)

    async def list(
        self,
        *,
        gear_id: str | None = None,
        state: str | None = None,
        type: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Workspace]:
        rows = await self._ws.list(gear_id=gear_id, state=state, type=type, limit=limit, offset=offset)
        return [Workspace.from_row(r) for r in rows]

    # ── transitions ──────────────────────────────────────────────────────────────
    async def transition(self, workspace_id: str, to_state: str, *, actor: str = "system") -> Workspace:
        """Move a workspace to ``to_state`` — validated by the state machine; audited before -> after."""
        current = await self.get(workspace_id)
        dst = WorkspaceState(to_state)
        try:
            _SM.validate(current.state, dst)
        except TransitionError as exc:
            raise ValidationError(
                str(exc), {"workspace_id": workspace_id, "from": current.state.value, "to": dst.value}
            ) from exc
        if dst is WorkspaceState.CLOSED:
            row = await self._ws.close(workspace_id)
        else:
            row = await self._ws.set_state(workspace_id, dst.value)
        ws = Workspace.from_row(row)
        await self._audit_event(
            actor,
            "workspace.transition",
            ws,
            before={"state": current.state.value},
            after={"state": ws.state.value},
        )
        # A forward transition = the from-stage was accepted → stamp Reviewed/Approved By on its artifact.
        review_kind = _STATE_KIND.get(current.state.value)
        if review_kind:
            rev_user, rev_name = current_actor()
            try:
                await self._art.set_reviewed(workspace_id, review_kind, rev_user, rev_name)
            except Exception as exc:  # noqa: BLE001 — provenance is best-effort; never block the transition
                log.warning(f"[workspace] set_reviewed failed for {workspace_id}/{review_kind}: {exc}")
        return ws

    async def advance(self, workspace_id: str, *, actor: str = "system") -> Workspace:
        """Advance one step along the happy path (INTAKE -> ANALYSIS -> ...)."""
        current = await self.get(workspace_id)
        nxt = _SM.next_state(current.state)
        if nxt is None:
            raise ValidationError(
                f"workspace {workspace_id} is terminal ({current.state.value})", {"workspace_id": workspace_id}
            )
        return await self.transition(workspace_id, nxt.value, actor=actor)

    async def close(self, workspace_id: str, *, actor: str = "system") -> Workspace:
        """Close a workspace. A normal close (from PENDING_SYNC) enqueues a kb-refresh; the ANALYSIS
        Existing-class short-circuit closes without a refresh (nothing was delivered)."""
        prior = await self.get(workspace_id)
        ws = await self.transition(workspace_id, WorkspaceState.CLOSED.value, actor=actor)
        if prior.state is WorkspaceState.PENDING_SYNC:
            job_id = await self._refresh.enqueue(workspace_id, gear_id=ws.gear_id)
            await self._audit_event(actor, "workspace.refresh_enqueued", ws, before=None, after={"job_id": job_id})
        return ws

    # ── artifacts ────────────────────────────────────────────────────────────────
    async def attach_artifact(
        self,
        workspace_id: str,
        *,
        kind: str,
        content: str | None = None,
        s3_uri: str | None = None,
        template_id: str | None = None,
        template_version: str | None = None,
        grounding_score: float | None = None,
        actor: str = "system",
        artifact_id: str | None = None,
        # Machine provenance (migration 0009) — optional; sourced from ContextVars when omitted.
        run_id: str | None = None,
        workflow_run_id: str | None = None,
        agent_run_id: str | None = None,
        model: str | None = None,
        plugin: str | None = None,
        plugin_version: str | None = None,
    ) -> dict[str, Any]:
        from app.utils.request_context import get_workflow_run_id, get_agent_run_id  # noqa: PLC0415
        await self.get(workspace_id)  # 404 if the workspace does not exist
        aid = artifact_id or f"art-{uuid.uuid4().hex[:12]}"
        # Provenance — Triggered By = the real user (request context) + the owning persona (from actor).
        trig_user, trig_name = current_actor()
        persona_id = actor[len("persona:"):] if actor.startswith("persona:") else None
        trig_persona = _PERSONA_AGENT.get((persona_id or "").lower(), persona_id)
        row = await self._art.add(
            artifact_id=aid,
            workspace_id=workspace_id,
            kind=kind,
            template_id=template_id,
            template_version=template_version,
            content=content,
            s3_uri=s3_uri,
            grounding_score=grounding_score,
            triggered_by_user=trig_user,
            triggered_by_name=trig_name,
            triggered_by_persona=trig_persona,
            run_id=run_id,
            workflow_run_id=workflow_run_id or get_workflow_run_id() or None,
            agent_run_id=agent_run_id or get_agent_run_id() or None,
            model=model,
            plugin=plugin,
            plugin_version=plugin_version,
        )
        await self._audit_event(
            actor,
            "workspace.artifact",
            None,
            before=None,
            after={"artifact_id": aid, "kind": kind},
            resource_id=aid,
            workspace_id=workspace_id,
        )
        return row

    async def list_artifacts(self, workspace_id: str) -> list[dict[str, Any]]:
        """All artifacts attached to a workspace (404 if the workspace does not exist)."""
        await self.get(workspace_id)
        return await self._art.list_for_workspace(workspace_id)

    async def latest_artifact(self, workspace_id: str, kind: str) -> dict[str, Any] | None:
        """The newest artifact of one kind (with content), or None. O(1) — the stage GET fast path."""
        await self.get(workspace_id)
        return await self._art.latest_for_kind(workspace_id, kind)

    # ── audit helper ─────────────────────────────────────────────────────────────
    async def _audit_event(
        self,
        actor: str,
        action: str,
        ws: Workspace | None,
        *,
        before: dict[str, Any] | None,
        after: dict[str, Any] | None,
        resource_id: str | None = None,
        workspace_id: str | None = None,
    ) -> None:
        """Best-effort audit — never fail the operation because the audit write failed."""
        wid = workspace_id or (ws.workspace_id if ws else None)
        try:
            await self._audit.append(
                actor=actor,
                action=action,
                resource_id=resource_id or wid,
                workspace_id=wid,
                before_state=before,
                after_state=after,
            )
        except Exception as exc:  # noqa: BLE001 — audit must not break the user operation
            log.warning(f"[workspace] audit append failed for {action} ({wid}): {exc}")
