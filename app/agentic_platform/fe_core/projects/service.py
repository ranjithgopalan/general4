"""Building the project list, and onboarding a project.

Onboarding is deliberately small: resolve the KB application, then open its
Global Workspace. There is nothing else to create, because the pipelines are
shared templates rather than per-project copies — every project runs the same
SDLC, which is the point of having a declared pipeline at all.

The consequence worth stating: onboarding cannot succeed while the knowledge base is
unreachable, because a project whose KB application does not resolve would have
nowhere to read code knowledge, specs or schema from, and every stage would fail
at the first step having already spent tokens.
"""

from __future__ import annotations

import logging

from app.agentic_platform.fe_core.kb.library import LIBRARY_APP_ID, is_library
from app.agentic_platform.fe_core.projects.models import ProjectSource, ProjectSummary
from app.agentic_platform.fe_core.workspaces.service import WorkspaceService

logger = logging.getLogger(__name__)


class ProjectOnboardingError(RuntimeError):
    """Onboarding could not complete. Surfaced as 422 or 503."""


class ProjectService:
    #: The UI polls list_projects every few seconds, so an unreachable KB would
    #: otherwise repeat the same warning for the whole session. The degraded
    #: state already reaches the caller in the response; log it once.
    _kb_down_logged = False

    def __init__(self, store, kb, pipeline_name: str = "uw-cr-global"):
        self.store = store
        self.kb = kb
        self.pipeline_name = pipeline_name

    # -- listing ----------------------------------------------------------
    async def list_projects(self, *, lightweight: bool = False) -> tuple[list[ProjectSummary], str | None]:
        """Every project, from the knowledge base's catalogue joined with local workspaces.

        Returns `(projects, kb_warning)`. `kb_warning` is set when the knowledge base could not
        be reached: the list then contains only what has been onboarded here, and
        the caller must say so rather than implying nothing exists.
        """
        summaries: dict[str, ProjectSummary] = {}
        kb_warning: str | None = None

        # 1. The catalogue.
        try:
            for app in await self._list_kb_applications():
                summaries[app.id] = ProjectSummary(
                    kb_application_id=app.id,
                    name=app.name,
                    source=ProjectSource.KB_ONLY,
                    business_domain=getattr(app, "business_domain", None),
                    owner=getattr(app, "owner", None),
                    kb_status=str(getattr(app, "status", "") or "") or None,
                )
            ProjectService._kb_down_logged = False
        except Exception as exc:  # noqa: BLE001
            kb_warning = (
                "The knowledge base is not responding on "
                f"{getattr(self.kb, 'base_url', '') or 'its configured URL'}, so its "
                "application catalogue could not be read. Only projects already "
                "onboarded here are listed, and a new one cannot be onboarded "
                "until it is up."
            )
            if not ProjectService._kb_down_logged:
                logger.warning(
                    "Project listing degraded: %s: %s (logged once; further "
                    "occurrences at DEBUG)", type(exc).__name__, exc)
                ProjectService._kb_down_logged = True
            else:
                logger.debug(
                    "Project listing degraded: %s: %s", type(exc).__name__, exc)

        # 2. Local state, which may add projects the catalogue did not return.
        for workspace in self.store.list_workspaces():
            app_id = workspace.kb_application_id
            if is_library(app_id):
                # The global card library is not a project; it has its own entry.
                continue
            summary = summaries.get(app_id)
            if summary is None:
                summary = ProjectSummary(
                    kb_application_id=app_id,
                    name=app_id,
                    source=ProjectSource.LOCAL_ONLY,
                )
                summaries[app_id] = summary
            elif summary.source is ProjectSource.KB_ONLY:
                summary.source = ProjectSource.BOTH

            if workspace.is_global:
                summary.global_workspace_id = workspace.id
                # Recorded at onboarding; read back so greenfield/brownfield and
                # the target stack survive a restart rather than living only in
                # the onboarding response.
                summary.category = workspace.category
                summary.intake_source = workspace.intake_source
                summary.source_language = workspace.source_language
                summary.target_framework = workspace.target_framework
            else:
                summary.mini_workspace_count += 1
                if workspace.is_open:
                    summary.open_mini_count += 1
                if workspace.epic_id:
                    summary.epics.append(workspace.epic_id)

        # 3. Global-tier progress, only for projects that have one.
        # Skip when lightweight=True (list view) — avoids N queries per project.
        for summary in summaries.values():
            if summary.global_workspace_id and not lightweight:
                self._attach_progress(summary)
            summary.epics.sort()

        ordered = sorted(
            summaries.values(),
            # Onboarded first: they are what someone is actually working on.
            key=lambda s: (not s.onboarded, s.name.lower()),
        )
        return ordered, kb_warning

    async def _list_kb_applications(self):
        lister = getattr(self.kb, "list_applications", None)
        if callable(lister):
            return await lister()
        return []

    def _attach_progress(self, summary: ProjectSummary) -> None:
        from app.agentic_platform.fe_core.pipeline.eligibility import pipeline_status
        from app.agentic_platform.fe_core.pipeline.registry import PipelineNotFoundError, get_pipeline

        workspace = self.store.get_workspace(summary.global_workspace_id)
        if workspace is None:
            return
        try:
            pipeline = get_pipeline(workspace.pipeline)
        except PipelineNotFoundError:
            return
        rows = pipeline_status(self.store, pipeline, workspace.id)
        summary.global_stages = len(rows)
        summary.global_completed = sum(1 for r in rows if r["state"] == "completed")
        summary.global_awaiting_approval = sum(
            1 for r in rows if r["state"] == "waiting_for_approval")
        summary.global_ready = [r["key"] for r in rows if r["ready"]]

    # -- deletion ---------------------------------------------------------
    def delete(self, kb_application_id: str) -> int:
        """Remove all local state for a project.

        Returns the number of workspaces deleted. Projects that exist only in
        the KB catalogue (never onboarded here) have nothing to delete and
        return 0 — that is not an error.
        """
        if is_library(kb_application_id):
            raise ProjectOnboardingError(
                f"'{LIBRARY_APP_ID}' is the global card library, which every project "
                "reads from; it cannot be deleted as a project.")
        return self.store.delete_project(kb_application_id)

    # -- onboarding -------------------------------------------------------
    async def onboard(
        self,
        application_ref: str,
        *,
        intake_source: str = "existing-kb",
        intake: dict | None = None,
    ) -> ProjectSummary:
        """Open a project's Global Workspace, linking a KB application if there is one.

        Idempotent: onboarding an already-onboarded project returns it unchanged,
        because the Global Workspace id is derived from the project id rather than
        generated.

        Whether a KB application is *required* depends on where the project's
        knowledge comes from (`REQUIRES_KB_APPLICATION`). Only `existing-kb`
        requires one: it means "work against knowledge already indexed there", so
        an unresolvable name leaves nothing to read. A greenfield project brings a
        requirements document and has no legacy system, so demanding a catalogue
        entry made greenfield onboarding impossible.
        """
        from app.agentic_platform.fe_core.projects.intake import REQUIRES_KB_APPLICATION, IntakeSource
        from app.agentic_platform.fe_core.projects.models import local_project_id

        ref = (application_ref or "").strip()
        if not ref:
            raise ProjectOnboardingError("a project name is required")
        if is_library(ref):
            raise ProjectOnboardingError(
                f"'{LIBRARY_APP_ID}' is reserved for the global card library; choose another name")

        try:
            source = IntakeSource(intake_source)
        except ValueError as exc:
            raise ProjectOnboardingError(f"unknown intake source '{intake_source}'") from exc
        kb_required = source in REQUIRES_KB_APPLICATION

        # Attempted for every source, so a project that does exist in the
        # catalogue is linked to it rather than duplicated under a local id.
        app = None
        resolution_error: str | None = None
        try:
            app = await self.kb.resolve_application(ref)
        except Exception as exc:  # noqa: BLE001
            resolution_error = str(exc)
            if kb_required:
                raise ProjectOnboardingError(
                    f"'{ref}' could not be resolved: {exc} "
                    "Onboarding from knowledge already indexed needs that name to "
                    "exist, because it is where every stage reads its code "
                    "knowledge, specs and schema. To start from a requirements "
                    "document or a reverse-engineering report instead, choose that "
                    "intake source -- those bring their own knowledge and do not "
                    "need a catalogue entry."
                ) from exc
            logger.info(
                "'%s' is not in the catalogue (%s); onboarding it as a local "
                "project, which is expected for %s.",
                ref, type(exc).__name__, source.value,
            )

        project_id = app.id if app is not None else local_project_id(ref)
        name = app.name if app is not None else ref
        if is_library(project_id):
            raise ProjectOnboardingError(
                f"'{LIBRARY_APP_ID}' is reserved for the global card library; choose another name")

        workspace = WorkspaceService(self.store, self.pipeline_name).ensure_global(
            project_id, self.pipeline_name, intake={**(intake or {}),
                                                    "intake_source": source.value})
        logger.info(
            "Onboarded project '%s' (%s): Global Workspace %s",
            name, project_id, workspace.id,
        )

        summary = ProjectSummary(
            kb_application_id=project_id,
            name=name,
            source=ProjectSource.BOTH if app is not None else ProjectSource.LOCAL_ONLY,
            business_domain=getattr(app, "business_domain", None) if app else None,
            owner=getattr(app, "owner", None) if app else None,
            kb_status=str(getattr(app, "status", "") or "") or None if app else None,
            category=workspace.category,
            intake_source=workspace.intake_source,
            source_language=workspace.source_language,
            target_framework=workspace.target_framework,
            global_workspace_id=workspace.id,
        )
        if resolution_error and app is None:
            summary.kb_status = "not in the catalogue"
        for other in self.store.list_workspaces(project_id, tier="mini"):
            summary.mini_workspace_count += 1
            if other.is_open:
                summary.open_mini_count += 1
            if other.epic_id:
                summary.epics.append(other.epic_id)
        self._attach_progress(summary)
        return summary
