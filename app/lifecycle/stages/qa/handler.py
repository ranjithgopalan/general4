"""QA stage handler — thin orchestrator for QA_TESTING stage.

Pipeline: **load DevDocument** (required prerequisite) → **load prior artifacts** (best-effort)
→ **build QAContext** (two-pass: detect fallback IDs → fetch card_bodies → rebuild)
→ **OPEN ReAct reasoning** (QA persona; lightweight — test_scope + gaps + regression items only)
→ **assemble_test_plan** (deterministic + reasoned) → **persist** + DERIVES_FROM(DevDocument).
Accept advances to PENDING_SYNC (RELEASE/DevOps stage removed).

Prerequisite: the Developer stage must be accepted (DevDocument must exist in S3 or DB).

QA persona: capability = "fe.generate.test-plan" (declared in generate.py _CAPABILITY_FOR).
Routes via fe.generate dispatcher (unlike the Developer stage which uses dedicated routes).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from app.lifecycle.common.handler_base import StageHandlerBase, _now_iso
from app.lifecycle.render.docx import render_qa_docx
from app.lifecycle.traceability import TraceabilityService
from app.lifecycle.traceability.analysis_dao import WorkspaceAnalysisDAO
from app.lifecycle.traceability.justification_builder import RelationshipJustification
from app.lifecycle.stages.architecture.handler import ArchitectureService
from app.lifecycle.stages.brd.handler import BRDService
from app.lifecycle.stages.developer.handler import DeveloperService
from app.lifecycle.stages.fsd.handler import FSDService
from app.lifecycle.stages.qa.agent.react import run_react
from app.lifecycle.stages.qa.match.context import QAContext
from app.lifecycle.stages.qa.schema import TestPlanDocument
from app.lifecycle.stages.qa.sections.assemble import assemble_test_plan
from app.lifecycle.stages.stories.handler import StoriesService
from app.lifecycle.templates.registry import Template
from app.models.workspace import Workspace, WorkspaceState
from app.utils.exceptions import ValidationError
from app.utils.logging import log


class QAService(StageHandlerBase):
    """Config-driven grounded QA stage (DevDocument → OPEN ReAct → assemble_test_plan).

    Inherits from StageHandlerBase:
      - _s3_key()     → ``<workspace>/qa/test-plan.json``
      - get()         → S3-first → DB-fallback
      - export_docx() → get() + ResourceNotFoundError + _render_docx()

    Constructor params:
      dev (required)          — reads the accepted DevDocument.
      stories (optional)      — reads StoriesDocument for Gherkin AC.
      srd (optional)          — reads SRDDocument for workflow scenarios + integration tests.
      fsd (optional)          — reads FSDDocument for screen validations.
      brd (optional)          — reads BRDDocument for business rule tests.
    """

    _CAPABILITY = "fe.generate.test-plan"
    # Accept/record-execution are lifecycle actions, not generation — any persona
    # that can query the KB may participate (matches the developer stage pattern).
    _ACCEPT_CAPABILITY = "kb.query"
    _KIND = "test-plan"
    _STAGE_FOLDER = "qa"
    _TEMPLATE_NAME = "qa"

    def __init__(
        self,
        *,
        workspace,
        kb_query,
        graph,
        personas,
        trace,
        dev: DeveloperService,
        stories: StoriesService | None = None,
        srd: ArchitectureService | None = None,
        fsd: FSDService | None = None,
        brd: BRDService | None = None,
        spine=None,
        model=None,
        store=None,
        template_version: str | None = None,
        traceability_service: TraceabilityService | None = None,
    ) -> None:
        super().__init__(
            workspace=workspace,
            kb_query=kb_query,
            graph=graph,
            personas=personas,
            trace=trace,
            spine=spine,
            model=model,
            store=store,
            template_version=template_version,
        )
        self._dev = dev
        self._stories = stories
        self._srd = srd
        self._fsd = fsd
        self._brd = brd
        self._traceability = traceability_service

    # ── abstract implementations ───────────────────────────────────────────────

    def _parse(self, workspace_id: str, text: str, *, source: str) -> TestPlanDocument | None:
        try:
            return TestPlanDocument.model_validate_json(text)
        except Exception as exc:  # noqa: BLE001
            log.warning(f"[qa] {source} TestPlanDocument for {workspace_id} is not parseable: {exc}")
            return None

    def _render_docx(self, artifact: TestPlanDocument) -> bytes:
        return render_qa_docx(artifact, self._template)

    # ── public API ─────────────────────────────────────────────────────────────

    async def generate_stream(
        self,
        workspace_id: str,
        *,
        persona: str,
    ) -> AsyncIterator[tuple[str, Any]]:
        """Async generator: yields ('status', {...}) then ('result', TestPlanDocument).

        Prerequisite: Developer stage must be accepted (DevDocument must exist).
        Raises 409 (via ValidationError) if DevDocument is missing.
        """
        self._personas.require_capability(persona, self._CAPABILITY)

        # ── Load DevDocument (required prerequisite) — check BEFORE advancing so a premature call
        # can't corrupt the workspace state (leaving it at QA_TESTING with no DevDocument). ──────
        yield ("status", {"stage": "load_dev", "detail": "developer implementation doc"})
        dev_doc = await self._dev.get(workspace_id)
        if dev_doc is None:
            raise ValidationError(
                "Developer stage (DevDocument) must be accepted before generating the QA test plan",
                {"workspace_id": workspace_id, "required_stage": "DEVELOPMENT"},
            )

        # Advance DEVELOPMENT → QA_TESTING on first generation (only now that the prerequisite is confirmed).
        ws = await self._workspace.get(workspace_id)
        if ws.state is WorkspaceState.DEVELOPMENT:
            await self._workspace.advance(workspace_id, actor=f"persona:{persona}")
        dev_artifact_id = await self._get_artifact_id(workspace_id, "dev")

        # ── Load prior artifacts (best-effort — never raises) ─────────────────
        stories_doc, srd_doc, fsd_doc, brd_doc = None, None, None, None
        stories_artifact_id, srd_artifact_id, fsd_artifact_id, brd_artifact_id = None, None, None, None

        if self._stories is not None:
            try:
                yield ("status", {"stage": "load_stories", "detail": "user stories + Gherkin AC"})
                stories_doc = await self._stories.get(workspace_id)
                if stories_doc:
                    stories_artifact_id = await self._get_artifact_id(workspace_id, "stories")
                    log.info(f"[qa] loaded StoriesDocument — {len(stories_doc.stories or [])} stories")
                else:
                    log.info(f"[qa] StoriesDocument not found for {workspace_id} (non-fatal)")
            except Exception as exc:  # noqa: BLE001
                log.warning(f"[qa] StoriesDocument load failed (non-fatal): {exc}")

        if self._srd is not None:
            try:
                yield ("status", {"stage": "load_srd", "detail": "workflow + integration design"})
                srd_doc = await self._srd.get(workspace_id)
                if srd_doc:
                    srd_artifact_id = await self._get_artifact_id(workspace_id, "srd")
                    log.info(f"[qa] loaded SRDDocument — "
                             f"{len(srd_doc.sequence_diagrams or [])} sequences, "
                             f"{len(srd_doc.integration_design or [])} integrations")
                else:
                    log.info(f"[qa] SRDDocument not found for {workspace_id} (non-fatal)")
            except Exception as exc:  # noqa: BLE001
                log.warning(f"[qa] SRDDocument load failed (non-fatal): {exc}")

        if self._fsd is not None:
            try:
                yield ("status", {"stage": "load_frd", "detail": "PRD — screen specs"})
                fsd_doc = await self._fsd.get(workspace_id)
                if fsd_doc:
                    fsd_artifact_id = await self._get_artifact_id(workspace_id, "fsd")
                    log.info(f"[qa] loaded FSDDocument — {len(fsd_doc.screen_specs or [])} screen specs")
                else:
                    log.info(f"[qa] FSDDocument not found for {workspace_id} (non-fatal)")
            except Exception as exc:  # noqa: BLE001
                log.warning(f"[qa] FSDDocument load failed (non-fatal): {exc}")

        if self._brd is not None:
            try:
                yield ("status", {"stage": "load_frd", "detail": "PRD — business rules"})
                brd_doc = await self._brd.get(workspace_id)
                if brd_doc:
                    brd_artifact_id = await self._get_artifact_id(workspace_id, "brd")
                    log.info(f"[qa] loaded BRDDocument — {len(brd_doc.business_rules or [])} rules")
                else:
                    log.info(f"[qa] BRDDocument not found for {workspace_id} (non-fatal)")
            except Exception as exc:  # noqa: BLE001
                log.warning(f"[qa] BRDDocument load failed (non-fatal): {exc}")

        # ── QAContext pass 1: detect required IDs + fallback IDs ──────────────
        yield ("status", {"stage": "building", "detail": "building QAContext (pass 1)"})
        context = QAContext.build(
            dev_doc,
            stories_doc=stories_doc,
            srd_doc=srd_doc,
            fsd_doc=fsd_doc,
            brd_doc=brd_doc,
            dev_ref=dev_artifact_id,
            stories_ref=stories_artifact_id,
            srd_ref=srd_artifact_id,
            fsd_ref=fsd_artifact_id,
            brd_ref=brd_artifact_id,
        )

        # ── Fetch KB card bodies including fallback IDs ───────────────────────
        all_ids = list(context.allowed_ids)
        if not all_ids:
            card_bodies: dict = {}
        else:
            try:
                yield ("status", {"stage": "building", "detail": f"fetching {len(all_ids)} KB card bodies"})
                card_bodies = await self._kb.read_many(all_ids)
            except Exception as exc:  # noqa: BLE001
                log.warning(f"[qa] KB card_bodies fetch failed (non-fatal): {exc}")
                card_bodies = {}

        # ── QAContext pass 2: rebuild with card_bodies ─────────────────────────
        context = QAContext.build(
            dev_doc,
            stories_doc=stories_doc,
            srd_doc=srd_doc,
            fsd_doc=fsd_doc,
            brd_doc=brd_doc,
            card_bodies=card_bodies,
            dev_ref=dev_artifact_id,
            stories_ref=stories_artifact_id,
            srd_ref=srd_artifact_id,
            fsd_ref=fsd_artifact_id,
            brd_ref=brd_artifact_id,
        )

        yield (
            "status",
            {
                "stage": "building",
                "detail": (
                    f"{len(context.story_rows)} stories · "
                    f"{len(context.fsd_screens)} screens · "
                    f"{len(context.brd_rules)} rules · "
                    f"{len(context.srd_seq_diagrams)} sequences · "
                    f"{len(context.srd_int_design)} integrations · "
                    f"{len(context.impl_tasks)} impl tasks"
                ),
            },
        )

        # ── ReAct agent (lightweight — test_scope + gaps + regression) ────────
        yield ("status", {"stage": "reasoning", "detail": "running the grounded QA agent"})
        try:
            enriched, _whitelist = await run_react(
                model=self._model,
                kb=self._kb,
                graph=self._graph,
                persona=persona,
                context=context,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning(f"[qa] ReAct agent failed (non-fatal; using deterministic fallback): {exc}")
            enriched = None

        # ── Assemble TestPlanDocument ──────────────────────────────────────────
        yield ("status", {"stage": "assembling", "detail": "assembling TestPlanDocument"})
        test_plan = assemble_test_plan(
            workspace_id=workspace_id,
            kb_version=context.kb_version,
            persona=persona,
            template=self._template,
            context=context,
            enriched=enriched,
            generated_at=_now_iso(),
        )

        # ── Persist ────────────────────────────────────────────────────────────
        yield ("status", {"stage": "persisting", "detail": "saving TestPlanDocument + DERIVES_FROM links"})
        try:
            await self._persist(workspace_id, persona, test_plan, context)
        except Exception as exc:  # noqa: BLE001
            log.warning(f"[qa] persist failed for {workspace_id} (result still returned): {exc}")

        yield ("result", test_plan)

    async def accept(
        self,
        workspace_id: str,
        *,
        persona: str,
        override_justification: str | None = None,
    ) -> Workspace:
        """Accept the QA test plan → advance to PENDING_SYNC.

        Gate: blocks if any TestExecution has status FAIL|BLOCKED and no override_justification.
        When override_justification is provided, records it in the BLOCKED execution records.
        """
        self._personas.require_capability(persona, self._ACCEPT_CAPABILITY)

        test_plan = await self.get(workspace_id)
        if test_plan is not None:
            blocking = [
                ex for ex in test_plan.test_executions
                if ex.status in ("FAIL", "BLOCKED") and not ex.override_justification
            ]
            if blocking and not override_justification:
                raise ValidationError(
                    f"QA test plan has {len(blocking)} FAIL/BLOCKED execution(s) without override. "
                    "Provide override_justification to proceed despite failures.",
                    {
                        "workspace_id": workspace_id,
                        "blocking_count": len(blocking),
                        "blocking_ids": [ex.test_id for ex in blocking[:5]],
                    },
                )

        return await self._workspace.advance(workspace_id, actor=f"persona:{persona}")

    async def record_execution(
        self,
        workspace_id: str,
        *,
        persona: str,
        test_id: str,
        status: str,
        notes: str = "",
        override_justification: str | None = None,
        executed_by: str | None = None,
    ) -> TestPlanDocument:
        """Append a TestExecution record to the persisted TestPlanDocument.

        Status must be one of: TODO | PASS | FAIL | BLOCKED | SKIP.
        Persists the updated document back to S3 + DB.
        """
        self._personas.require_capability(persona, self._ACCEPT_CAPABILITY)

        if status not in ("TODO", "PASS", "FAIL", "BLOCKED", "SKIP"):
            raise ValidationError(
                f"Invalid test status {status!r}. Must be one of: TODO, PASS, FAIL, BLOCKED, SKIP.",
                {"test_id": test_id, "status": status},
            )

        test_plan = await self.get(workspace_id)
        if test_plan is None:
            raise ValidationError(
                f"No TestPlanDocument found for workspace {workspace_id}. "
                "Generate the test plan first via fe.generate.",
                {"workspace_id": workspace_id},
            )

        from app.lifecycle.stages.qa.schema import TestExecution

        execution = TestExecution(
            test_id=test_id,
            status=status,
            notes=notes,
            override_justification=override_justification,
            executed_at=_now_iso(),
            executed_by=executed_by or f"persona:{persona}",
        )

        # Replace existing execution for this test_id or append
        existing = [ex for ex in test_plan.test_executions if ex.test_id != test_id]
        test_plan = test_plan.model_copy(update={"test_executions": existing + [execution]})

        try:
            s3_uri = await self._store.put_json(self._s3_key(workspace_id), test_plan.model_dump())
            await self._workspace.attach_artifact(
                workspace_id,
                kind="test-plan",
                content=test_plan.model_dump_json(),
                s3_uri=s3_uri,
                grounding_score=test_plan.grounding_score,
                template_id=test_plan.template_id,
                template_version=test_plan.template_version,
                actor=f"persona:{persona}",
            )
        except Exception as exc:  # noqa: BLE001
            log.warning(f"[qa] persist after record_execution failed for {workspace_id}: {exc}")

        return test_plan

    # ── pipeline steps ─────────────────────────────────────────────────────────

    async def _persist(
        self,
        workspace_id: str,
        persona: str,
        plan: TestPlanDocument,
        context: QAContext,
    ) -> None:
        """S3 write + DB mirror + GROUNDS + DERIVES_FROM — all best-effort."""
        content = plan.model_dump_json()

        s3_uri: str | None = None
        try:
            s3_uri = await self._store.put_json(self._s3_key(workspace_id), plan.model_dump())
        except Exception as exc:  # noqa: BLE001
            log.warning(f"[qa] S3 write failed for {workspace_id}: {exc}")

        # Cleanup previous QA artifacts on re-run
        try:
            await self._trace.cleanup_stage_artifacts(workspace_id, kind="qa")
        except Exception as exc:  # noqa: BLE001
            log.debug(f"[qa] Cleanup of previous QA artifacts: {exc}")

        artifact_id: str | None = None
        try:
            artifact = await self._workspace.attach_artifact(
                workspace_id,
                kind="test-plan",
                content=content,
                s3_uri=s3_uri,
                grounding_score=plan.grounding_score,
                template_id=plan.template_id,
                template_version=plan.template_version,
                actor=f"persona:{persona}",
            )
            artifact_id = artifact.get("artifact_id")
        except Exception as exc:  # noqa: BLE001
            log.warning(f"[qa] DB artifact write failed for {workspace_id}: {exc}")

        if not artifact_id:
            return

        # Auto-link to previous stage with rich justification
        try:
            # Extract systems from SRD if available (QAContext doesn't have systems_tested attribute)
            systems_from_srd = []
            if context.srd_source == "prior_artifact" and hasattr(context, 'srd_int_design'):
                for integ in (context.srd_int_design or []):
                    systems_from_srd.append({
                        "system_name": getattr(integ, 'label', getattr(integ, 'id', '?')),
                        "system_id": getattr(integ, 'id', '?'),
                    })

            justification = RelationshipJustification.build_qa_from_developer(
                stories_count=0,  # TODO: fetch from prior DEVELOPER
                test_cases_count=len(plan.test_cases) if hasattr(plan, 'test_cases') else 0,
                systems_tested=systems_from_srd,
                coverage_pct=plan.coverage.coverage_pct if hasattr(plan, 'coverage') and plan.coverage else 0.0,
            )

            await self._trace.auto_link_to_previous(
                workspace_id=workspace_id,
                current_artifact_id=artifact_id,
                current_kind="qa",
                justification=justification,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning(f"[qa] auto_link_to_previous failed for {workspace_id}: {exc}")

        # DERIVES_FROM → DevDocument (immediate prerequisite) [LEGACY]
        if context.dev_ref:
            try:
                await self._trace.add_derives(
                    workspace_id=workspace_id,
                    from_artifact_id=artifact_id,
                    to_artifact_id=context.dev_ref,
                    stage="QA_TESTING",
                    reason="QA test plan derived from development artifact",
                )
            except Exception as exc:  # noqa: BLE001
                log.warning(f"[qa] DERIVES_FROM(dev) link failed for {workspace_id}: {exc}")

        # GROUNDS links per KB card (SCR-*/BR-*/FR-* that generated test content)
        for kb_id in context.allowed_ids:
            try:
                card = context.card_bodies.get(kb_id, {})
                await self._trace.add_grounds(
                    workspace_id=workspace_id,
                    from_artifact_id=artifact_id,
                    persona=persona,
                    artifact_kind="test-plan",
                    to_kb_card_id=kb_id,
                    kb_version=plan.kb_version,
                    source_locus=card.get("source_locus"),
                    stage="QA_TESTING",
                    applied_because="VALIDATES",
                )
            except Exception as exc:  # noqa: BLE001
                log.warning(f"[qa] GROUNDS link failed for {workspace_id} → {kb_id}: {exc}")

        # Save QA metrics to fe_workspace_analysis table for UI traceability tab
        try:
            dao = WorkspaceAnalysisDAO()

            # Extract systems from QA context (allowed KB IDs that are systems)
            systems = [
                {
                    "system_id": kb_id,
                    "system_name": context.card_bodies.get(kb_id, {}).get("label", kb_id),
                    "impact_level": "medium",
                    "components_changed": [],
                    "confidence": 0.9,
                }
                for kb_id in context.allowed_ids if kb_id.startswith("SYS-")
            ]

            # Extract scope from QA (from allowed_ids - all tested items)
            scope = {
                "in_scope": [
                    {"item_id": kb_id, "item_description": context.card_bodies.get(kb_id, {}).get("label", kb_id)[:100], "scope_category": "IN_SCOPE"}
                    for kb_id in context.allowed_ids if kb_id.startswith(("SCR-", "BR-", "FR-"))
                ],
                "out_of_scope": [],
                "deferred": [],
            }

            # Extract metrics
            metrics = {
                "total_artifacts": len(context.allowed_ids or []),
                "total_relationships": 0,
                "coverage_pct": 100.0 if context.allowed_ids else 0.0,
                "orphan_count": 0,
                "systems_affected": len(systems),
            }

            # Save to database
            if systems:
                await dao.update_systems(workspace_id, systems)
            await dao.update_scope(workspace_id, scope)
            await dao.update_metrics(workspace_id, metrics)

            log.info(f"[qa] Saved QA data for {workspace_id}: {len(context.allowed_ids)} tested items")
        except Exception as exc:  # noqa: BLE001
            log.warning(f"[qa] Failed to save QA data to traceability table for {workspace_id}: {exc}")

    async def _get_artifact_id(self, workspace_id: str, kind: str) -> str | None:
        """Retrieve the artifact_id of the most recent artifact of the given kind."""
        try:
            artifacts = await self._workspace.list_artifacts(workspace_id)
            matching = [a for a in artifacts if a.get("kind") == kind]
            return matching[-1].get("artifact_id") if matching else None
        except Exception as exc:  # noqa: BLE001
            log.warning(f"[qa] could not retrieve {kind} artifact_id for {workspace_id}: {exc}")
            return None
