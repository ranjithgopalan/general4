"""FSD stage handler — thin orchestrator.

Pipeline: **load analysis** (prerequisite) → **build FSDContext** (deterministic, from analysis
artifact) → **OPEN ReAct reasoning** (grounded, tool-using; same 3 tools as analysis) → **assemble**
FSDDocument → **grounding gate** (whitelist; BLOCK on context_summary → deterministic fallback) →
**persist** + GROUNDS. Accept advances to BRD (or CLOSE for Existing-class). Export renders the
SAME artifact to official AIG .docx.

Infrastructure shared with analysis and future stages lives in app/lifecycle/common/handler_base.py.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from app.lifecycle.common.handler_base import StageHandlerBase, _now_iso
from app.lifecycle.render.docx import render_fsd_business_docx, render_fsd_docx
from app.lifecycle.stages.analysis.handler import ImpactAnalysisService
from app.lifecycle.traceability import TraceabilityService
from app.lifecycle.traceability.analysis_dao import WorkspaceAnalysisDAO
from app.lifecycle.traceability.justification_builder import RelationshipJustification
from app.lifecycle.stages.fsd.agent.react import run_react
from app.lifecycle.stages.fsd.match.context import FSDContext
from app.lifecycle.stages.fsd.schema import FSDDocument
from app.lifecycle.stages.fsd.sections.assemble import assemble_fsd
from app.lifecycle.stages.fsd.sections.business import BusinessFsdView, build_business_fsd
from app.lifecycle.stages.fsd.sections.reasoned import fallback_context_summary
from app.lifecycle.templates.registry import get_template
from app.models.workspace import Workspace, WorkspaceState
from app.utils.exceptions import ResourceNotFoundError, ValidationError
from app.utils.logging import log


class FSDService(StageHandlerBase):
    """Config-driven grounded FSD stage (analysis artifact → OPEN ReAct → grounding gate).

    Inherits from StageHandlerBase:
      - _s3_key() → ``<workspace>/fsd/fsd.json``
      - get()     → S3-first → DB-fallback
      - export_docx() → get() + ResourceNotFoundError + _render_docx()
      - _ground_field() → verify → BLOCK → fallback → re-verify → (text, score)

    Adds one extra constructor param: ``impact`` (ImpactAnalysisService) — reads the prerequisite
    analysis artifact.  The 8 shared params are forwarded to StageHandlerBase.__init__.
    """

    _CAPABILITY = "fe.generate.fsd"
    _KIND = "fsd"
    _STAGE_FOLDER = "fsd"
    _TEMPLATE_NAME = "fsd"

    def __init__(
        self,
        *,
        workspace,
        kb_query,
        graph,
        personas,
        trace,
        impact: ImpactAnalysisService,
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
        self._impact = impact  # reads the prerequisite analysis artifact
        self._traceability = traceability_service

    # ── abstract implementations ───────────────────────────────────────────────────

    def _parse(self, workspace_id: str, text: str, *, source: str) -> FSDDocument | None:
        try:
            return FSDDocument.model_validate_json(text)
        except Exception as exc:  # noqa: BLE001
            log.warning(f"[fsd] {source} FSD for {workspace_id} is not parseable: {exc}")
            return None

    def _render_docx(self, artifact: FSDDocument) -> bytes:
        return render_fsd_docx(artifact, self._template)

    async def get_business_fsd(self, workspace_id: str) -> BusinessFsdView:
        """Plain-language business view of the persisted FSD (the UI JSON). 404 if none yet."""
        fsd = await self.get(workspace_id)
        if fsd is None:
            raise ResourceNotFoundError(
                f"no {self._KIND} artifact for workspace {workspace_id}", {"workspace_id": workspace_id}
            )
        return build_business_fsd(fsd)

    async def export_business_docx(self, workspace_id: str) -> bytes:
        """Render the persisted FSD to the plain-language BUSINESS .docx — the UI download.

        The stored FSDDocument stays technical (downstream BRD/Stories + AC-3 traceability keep their
        KB-id grounding); the front end only ever receives this business-worded document.
        """
        view = await self.get_business_fsd(workspace_id)
        return render_fsd_business_docx(view, get_template("fsd-business"))

    # ── public API ─────────────────────────────────────────────────────────────────

    async def generate_stream(
        self, workspace_id: str, *, persona: str
    ) -> AsyncIterator[tuple[str, Any]]:
        """Async generator: yields ``('status', {...})`` node-progress, then ``('result', FSDDocument)``.

        Prerequisite: the ANALYSIS stage must be complete (analysis artifact must exist in S3 or DB).
        Raises 409 (via ValidationError) if the analysis is missing.
        """
        self._personas.require_capability(persona, self._CAPABILITY)

        # Check the analysis prerequisite BEFORE advancing so a premature call can't corrupt state.
        yield ("status", {"stage": "load_analysis", "detail": "impact analysis"})
        analysis = await self._impact.get(workspace_id)
        if analysis is None:
            raise ValidationError(
                "ANALYSIS stage must be complete before generating the FSD",
                {"workspace_id": workspace_id, "required_stage": "ANALYSIS"},
            )

        # Advance ANALYSIS → FSD on first generation (only now that the prerequisite is confirmed).
        ws = await self._workspace.get(workspace_id)
        if ws.state is WorkspaceState.ANALYSIS:
            await self._workspace.advance(workspace_id, actor=f"persona:{persona}")

        yield ("status", {"stage": "building", "detail": "building PRD context from analysis"})
        # Retrieve the analysis artifact_id for traceability (derives_from link)
        analysis_artifact_id = await self._get_analysis_artifact_id(workspace_id)

        # Fetch full card bodies for matched + affected IDs so section builders
        # can populate as_is / rule_text / purpose with real prose, not just labels.
        # Best-effort: returns {} in fixture/dev mode → builders fall back to label.
        all_card_ids = [c.id for c in analysis.matched] + [n.id for n in analysis.affected]
        card_bodies = await self._kb.read_many(all_card_ids) if all_card_ids else {}

        context = FSDContext.build(analysis, artifact_id=analysis_artifact_id, card_bodies=card_bodies)
        yield (
            "status",
            {
                "stage": "building",
                "detail": (
                    f"{len(context.fr_matched)} FR · {len(context.br_matched)} BR · "
                    f"{len(context.scr_matched)} SCR · {len(context.proc_matched + context.wf_matched)} Proc/WF"
                ),
            },
        )

        yield ("status", {"stage": "reasoning", "detail": "running the grounded PRD agent"})
        enriched: dict | None = None
        whitelist: set[str] = set(context.allowed_ids)
        async for kind, payload in run_react(
            model=self._model, kb=self._kb, graph=self._graph, persona=persona, context=context
        ):
            if kind == "step":
                yield ("step", payload)  # live agent tool-call → SSE
            elif kind == "result":
                enriched, whitelist = payload

        yield ("status", {"stage": "grounding", "detail": "verifying citations (cite-or-abstain)"})
        fsd = assemble_fsd(
            workspace_id=workspace_id,
            kb_version=context.kb_version,
            persona=persona,
            template=self._template,
            context=context,
            enriched=enriched,
            allowed_ids=whitelist,
            generated_at=_now_iso(),
        )
        fsd = self._ground(fsd, context, whitelist)

        yield ("status", {"stage": "persisting", "detail": "saving PRD artifact + GROUNDS links"})
        try:
            await self._persist(workspace_id, persona, fsd, context)
        except Exception as exc:  # noqa: BLE001 — persist is best-effort; always yield the result
            log.warning(f"[fsd] persist failed for {workspace_id} (result still returned): {exc}")
        yield ("result", fsd)

    async def accept(self, workspace_id: str, *, persona: str) -> Workspace:
        """Accept the FSD → advance to BRD stage."""
        self._personas.require_capability(persona, self._CAPABILITY)
        return await self._workspace.advance(workspace_id, actor=f"persona:{persona}")

    # ── pipeline steps ────────────────────────────────────────────────────────────

    def _ground(self, fsd: FSDDocument, context: FSDContext, whitelist: set[str]) -> FSDDocument:
        """Grounding gate on context_summary — block on hallucinated ids → deterministic fallback."""
        text, score = self._ground_field(
            fsd.context_summary,
            whitelist,
            lambda: fallback_context_summary(context),
        )
        return fsd.model_copy(update={"context_summary": text, "grounding_score": score})

    async def _persist(
        self, workspace_id: str, persona: str, fsd: FSDDocument, context: FSDContext
    ) -> None:
        """S3 write + DB mirror + GROUNDS links — ALL operations are best-effort (never raises).

        S3: single overwritten object per workspace (re-run overwrites same key).
        DB: mirrors the S3 content as audit trail + read fallback.
        GROUNDS: AC-3 traceability rows (FSD artifact → KB cards cited).
        """
        content = fsd.model_dump_json()

        # 1. S3 write
        s3_uri: str | None = None
        try:
            s3_uri = await self._store.put_json(self._s3_key(workspace_id), fsd.model_dump())
        except Exception as exc:  # noqa: BLE001
            log.warning(f"[fsd] S3 write failed for {workspace_id}: {exc}")

        # 2. DB mirror (with cleanup of previous FSD artifacts on re-run)
        # On re-run: clean up previous FSD artifacts + their relationships for this workspace
        try:
            await self._trace.cleanup_stage_artifacts(workspace_id, kind="fsd")
        except Exception as exc:  # noqa: BLE001
            log.debug(f"[fsd] Cleanup of previous FSD artifacts: {exc}")  # best-effort

        artifact_id: str | None = None
        try:
            artifact = await self._workspace.attach_artifact(
                workspace_id,
                kind="fsd",
                content=content,
                s3_uri=s3_uri,
                grounding_score=fsd.grounding_score,
                template_id=fsd.template_id,
                template_version=fsd.template_version,
                actor=f"persona:{persona}",
            )
            artifact_id = artifact.get("artifact_id")
        except Exception as exc:  # noqa: BLE001
            log.warning(f"[fsd] DB artifact write failed for {workspace_id}: {exc}")

        if not artifact_id:
            return

        # 3. Auto-link to previous stage with rich justification
        try:
            # Safe default for systems_affected (FSDDocument may not have this field)
            systems_for_link = []

            justification = RelationshipJustification.build_fsd_from_analysis(
                analysis_matched_count=len(context.matched) if hasattr(context, 'matched') else 0,
                analysis_affected_count=0,  # TODO: extract from prior analysis if available
                fsd_acceptance_criteria_count=len(fsd.acceptance_criteria) if fsd.acceptance_criteria else 0,
                fsd_grounded_ac_count=sum(1 for ac in (fsd.acceptance_criteria or []) if ac.kb_id),
                systems_affected=systems_for_link,  # Safe default: empty list
                screen_specs_count=len(fsd.screen_specs) if hasattr(fsd, 'screen_specs') else 0,
                coverage_pct=fsd.coverage.coverage_pct if hasattr(fsd, 'coverage') and fsd.coverage else 0.0,
            )

            await self._trace.auto_link_to_previous(
                workspace_id=workspace_id,
                current_artifact_id=artifact_id,
                current_kind="fsd",
                justification=justification,
            )
            log.info(f"[fsd] auto-linked to previous stage for {workspace_id}")
        except Exception as exc:  # noqa: BLE001
            log.warning(f"[fsd] auto_link_to_previous failed for {workspace_id}: {exc}")

        # 3a. DERIVES_FROM link → analysis artifact (FSD was built from the analysis) [LEGACY]
        if context.analysis_ref:
            try:
                await self._trace.add_derives(
                    workspace_id=workspace_id,
                    from_artifact_id=artifact_id,
                    to_artifact_id=context.analysis_ref,
                    stage="FSD",
                    reason="FSD derived from ANALYSIS artifact",
                )
            except Exception as exc:  # noqa: BLE001
                log.warning(f"[fsd] DERIVES_FROM link failed for {workspace_id}: {exc}")

        # 3b. GROUNDS links per KB card cited (AC-3 matrix rows)
        # ``applied_because`` captures the semantic link type (IMPLEMENTS / GOVERNED_BY / SCREEN_OF).
        for row in fsd.acceptance_criteria:
            if not row.kb_id:
                continue  # proposed AC row (net-new) — no KB card to ground against
            try:
                await self._trace.add_grounds(
                    workspace_id=workspace_id,
                    from_artifact_id=artifact_id,
                    persona=persona,
                    artifact_kind="fsd",
                    to_kb_card_id=row.kb_id,
                    kb_version=fsd.kb_version,
                    source_locus=row.source_locus,
                    stage="FSD",
                    applied_because=row.link_type,  # IMPLEMENTS | GOVERNED_BY | SCREEN_OF | DEPENDS_ON
                )
            except Exception as exc:  # noqa: BLE001
                log.warning(f"[fsd] GROUNDS link failed for {workspace_id} → {row.kb_id}: {exc}")

        # 4. Save FSD metrics to fe_workspace_analysis table for UI traceability tab
        try:
            dao = WorkspaceAnalysisDAO()

            # Extract systems touched by FSD (from acceptance criteria and screen specs)
            systems = []
            for ac in fsd.acceptance_criteria:
                if ac.kb_id and ac.kb_id.startswith("SYS-"):
                    systems.append({
                        "system_id": ac.kb_id,
                        "system_name": ac.title or ac.kb_id,
                        "impact_level": "medium",
                        "components_changed": [],
                        "confidence": 0.9,
                    })

            # Extract scope from FSD
            scope = {
                "in_scope": [
                    {"item_id": f"fsd-{i}", "item_description": sc.title or sc.description, "scope_category": "IN_SCOPE"}
                    for i, sc in enumerate(fsd.screen_specs or [])
                ],
                "out_of_scope": [],
                "deferred": [],
            }

            # Extract metrics
            metrics = {
                "total_artifacts": len(fsd.acceptance_criteria or []),
                "total_relationships": len([ac for ac in fsd.acceptance_criteria if ac.kb_id]),
                "coverage_pct": (len([ac for ac in fsd.acceptance_criteria if ac.kb_id]) / max(len(fsd.acceptance_criteria or []), 1)) * 100,
                "orphan_count": len([ac for ac in fsd.acceptance_criteria if not ac.kb_id]),
                "systems_affected": len(set(s["system_id"] for s in systems)),
            }

            # Save to database
            if systems:
                await dao.update_systems(workspace_id, systems)
            await dao.update_scope(workspace_id, scope)
            await dao.update_metrics(workspace_id, metrics)

            log.info(f"[fsd] Saved FSD data for {workspace_id}: {len(systems)} systems, {len(scope['in_scope'])} in-scope items")
        except Exception as exc:  # noqa: BLE001
            log.warning(f"[fsd] Failed to save FSD data to traceability table for {workspace_id}: {exc}")

    async def _get_analysis_artifact_id(self, workspace_id: str) -> str | None:
        """Retrieve the artifact_id of the most recent analysis artifact (for derives_from link)."""
        try:
            artifacts = await self._workspace.list_artifacts(workspace_id)
            analyses = [a for a in artifacts if a.get("kind") == "analysis"]
            return analyses[-1].get("artifact_id") if analyses else None
        except Exception as exc:  # noqa: BLE001
            log.warning(f"[fsd] could not retrieve analysis artifact_id for {workspace_id}: {exc}")
            return None
