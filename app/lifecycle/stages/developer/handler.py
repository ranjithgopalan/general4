"""Developer stage handler — thin orchestrator.

Pipeline: **load SRD** (prerequisite) → **build DevContext** → **OPEN ReAct reasoning**
(Developer persona, lightweight — notes + gaps only) → **assemble_dev** (deterministic +
reasoned) → **persist** + DERIVES_FROM(SRD).  Accept advances to QA_TESTING.

Infrastructure shared with all stage handlers lives in app/lifecycle/common/handler_base.py.
Prerequisite: the Architecture stage must be accepted (SRDDocument must exist in S3 or DB).

IMPORTANT: Developer stage uses dedicated routes at /ws/{id}/dev/* — it does NOT go through
the fe.generate dispatcher. Reason: Developer persona has capabilities=["kb.query", "kb.read"]
with no fe.generate.* capability. The dedicated routes use _CAPABILITY = "kb.query".

Codegen (actual .ts/.java file writing) is a SEPARATE Claude CLI subprocess route:
  POST /ws/{id}/dev/codegen/stream → agent/codegen.py.run_codegen()
This is NOT part of generate_stream(). It runs independently and writes files to
outputs/codegen/{workspace_id}/. The caller updates DevDocument.codegen_files afterward.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from app.lifecycle.common.handler_base import StageHandlerBase, _now_iso
from app.lifecycle.render.docx import render_dev_docx
from app.lifecycle.traceability import TraceabilityService
from app.lifecycle.traceability.analysis_dao import WorkspaceAnalysisDAO
from app.lifecycle.traceability.justification_builder import RelationshipJustification
from app.lifecycle.stages.architecture.handler import ArchitectureService
from app.lifecycle.stages.architecture.schema import SRDDocument
from app.lifecycle.stages.developer.agent.react import run_react
from app.lifecycle.stages.developer.match.context import DevContext
from app.lifecycle.stages.developer.schema import DevDocument, PrSyncRequest, PrSyncResult
from app.lifecycle.stages.developer.sections.assemble import assemble_dev
from app.lifecycle.stages.fsd.handler import FSDService
from app.lifecycle.stages.stories.handler import StoriesService
from app.models.workspace import Workspace, WorkspaceState
from app.utils.exceptions import ValidationError
from app.utils.logging import log


class DeveloperService(StageHandlerBase):
    """Config-driven grounded Developer stage (SRDDocument → OPEN ReAct → assemble_dev).

    Inherits from StageHandlerBase:
      - _s3_key()     → ``<workspace>/dev/dev.json``
      - get()         → S3-first → DB-fallback
      - export_docx() → get() + ResourceNotFoundError + _render_docx()
      - _ground_field() → inherited (not used here — no prose sections to ground)

    Constructor params:
      srd (required)      — reads the accepted SRDDocument.
      stories (optional)  — P1: reads StoriesDocument for Gherkin AC + story_text + DoD.
      fsd (optional)      — P2: reads FSDDocument for FR delta (as-is→to-be) + BR summaries.
    No graph walks needed — the SRD already contains diagrams + component design.
    """

    # Developer persona: capability = "kb.query" (no fe.generate.* in personas.json)
    _CAPABILITY = "kb.query"
    _KIND = "dev"
    _STAGE_FOLDER = "dev"
    _TEMPLATE_NAME = "developer"

    def __init__(
        self,
        *,
        workspace,
        kb_query,
        graph,
        personas,
        trace,
        srd: ArchitectureService,
        stories: StoriesService | None = None,
        fsd: FSDService | None = None,
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
        self._srd = srd
        self._stories = stories   # P1 — optional
        self._fsd = fsd           # P2 — optional (best-effort)
        self._traceability = traceability_service

    # ── abstract implementations ───────────────────────────────────────────────

    def _parse(self, workspace_id: str, text: str, *, source: str) -> DevDocument | None:
        try:
            return DevDocument.model_validate_json(text)
        except Exception as exc:  # noqa: BLE001
            log.warning(f"[dev] {source} DevDocument for {workspace_id} is not parseable: {exc}")
            return None

    def _render_docx(self, artifact: DevDocument) -> bytes:
        return render_dev_docx(artifact, self._template)

    # ── public API ─────────────────────────────────────────────────────────────

    async def generate_stream(
        self,
        workspace_id: str,
        *,
        persona: str,
    ) -> AsyncIterator[tuple[str, Any]]:
        """Async generator: yields ``('status', {...})`` progress, then ``('result', DevDocument)``.

        Prerequisite: the Architecture stage must be accepted (SRDDocument must exist).
        Raises 409 (via ValidationError) if SRD is missing.
        """
        self._personas.require_capability(persona, self._CAPABILITY)

        # ── Load SRD (required prerequisite) — check BEFORE advancing so a premature call can't corrupt state ─
        yield ("status", {"stage": "load_srd", "detail": "design + component specs"})
        srd_doc = await self._srd.get(workspace_id)
        if srd_doc is None:
            raise ValidationError(
                "Architecture stage (SRD) must be accepted before generating the Dev document",
                {"workspace_id": workspace_id, "required_stage": "ARCHITECTURE"},
            )

        # Advance STORIES → DEVELOPMENT on first generation (design-first order: Stories now precedes
        # Development; the SRD read above was produced earlier by Architecture and still exists).
        ws = await self._workspace.get(workspace_id)
        if ws.state is WorkspaceState.STORIES:
            await self._workspace.advance(workspace_id, actor=f"persona:{persona}")

        # ── SRD artifact ID for DERIVES_FROM traceability ─────────────────────
        srd_artifact_id = await self._get_artifact_id(workspace_id, "srd")

        # ── P1: Load StoriesDocument (best-effort — never raises) ─────────────
        stories_doc = None
        if self._stories is not None:
            try:
                yield ("status", {"stage": "load_stories", "detail": "user stories + Gherkin AC"})
                stories_doc = await self._stories.get(workspace_id)
                if stories_doc:
                    log.info(f"[dev] loaded StoriesDocument — {len(stories_doc.stories)} stories")
                else:
                    log.info(f"[dev] StoriesDocument not found for {workspace_id} (non-fatal)")
            except Exception as exc:  # noqa: BLE001 — optional
                log.warning(f"[dev] StoriesDocument load failed (non-fatal): {exc}")

        # ── P2: Load FSDDocument (best-effort — never raises) ─────────────────
        fsd_doc = None
        fsd_load_failed = False
        if self._fsd is not None:
            try:
                yield ("status", {"stage": "load_frd", "detail": "PRD — FR delta + business rules"})
                fsd_doc = await self._fsd.get(workspace_id)
                if fsd_doc:
                    log.info(f"[dev] loaded FSDDocument — {len(fsd_doc.functional_requirements)} FRs, {len(fsd_doc.business_rules)} BRs")
                else:
                    log.info(f"[dev] FSDDocument not found for {workspace_id} (non-fatal)")
                    fsd_load_failed = True
            except Exception as exc:  # noqa: BLE001 — optional
                log.warning(f"[dev] FSDDocument load failed (non-fatal): {exc}")
                fsd_load_failed = True

        # ── Collect developer-visible KB IDs from SRD ─────────────────────────
        yield ("status", {"stage": "building", "detail": "assembling the developer context"})
        context = DevContext.build(srd_doc, srd_ref=srd_artifact_id,
                                   stories_doc=stories_doc, fsd_doc=fsd_doc)

        # ── Fetch KB card bodies (CMP-*/API-*/INT-*/SYS-*/SCR-*/FR-*) ─────────
        all_ids = list(context.allowed_ids)
        card_bodies = await self._kb.read_many(all_ids) if all_ids else {}
        context = DevContext.build(srd_doc, card_bodies=card_bodies, srd_ref=srd_artifact_id,
                                   stories_doc=stories_doc, fsd_doc=fsd_doc)

        # Option 1 (docs/29): screens are not yet graph-linked to their implementing code, so the
        # Developer would only see abstract proposed components. Point it at the ACTUAL files by
        # matching code cards to the change's screen/requirement tokens — the real file:line then
        # drives a genuine "File to edit → CHANGE HERE" stub instead of a "new file" scaffold.
        await self._attach_code_targets(context)

        yield (
            "status",
            {
                "stage": "building",
                "detail": (
                    f"{len(context.cmp_cards)} CMP · {len(context.api_cards)} API · "
                    f"{len(context.int_cards)} INT · {len(context.story_refs)} stories"
                ),
            },
        )

        # ── ReAct agent (lightweight — notes + gaps only) ─────────────────────
        yield ("status", {"stage": "reasoning", "detail": "running the grounded Developer agent"})
        enriched, whitelist = await run_react(
            model=self._model,
            kb=self._kb,
            graph=self._graph,
            persona=persona,
            context=context,
        )

        # ── Assemble DevDocument ───────────────────────────────────────────────
        yield ("status", {"stage": "grounding", "detail": "assembling DevDocument"})
        dev_doc = assemble_dev(
            workspace_id=workspace_id,
            kb_version=context.kb_version,
            persona=persona,
            template=self._template,
            context=context,
            enriched=enriched,
            allowed_ids=whitelist,
            fsd_load_failed=fsd_load_failed,
            generated_at=_now_iso(),
        )

        # ── Persist ────────────────────────────────────────────────────────────
        yield ("status", {"stage": "persisting", "detail": "saving DevDocument + DERIVES_FROM links"})
        try:
            await self._persist(workspace_id, persona, dev_doc, context)
        except Exception as exc:  # noqa: BLE001 — persist is best-effort; always yield result
            log.warning(f"[dev] persist failed for {workspace_id} (result still returned): {exc}")

        yield ("result", dev_doc)

    async def _attach_code_targets(self, context) -> None:
        """Attach the REAL code files the change touches (Option 1, docs/29).

        Screens are not yet graph-linked to their implementing code, so match code cards (Component/
        ApiOp with a real file:line) to the requirement + affected-screen tokens and add them as
        grounded components. ``build_code_stubs`` then emits a genuine 'File to edit → CHANGE HERE'
        stub. Best-effort: never raises; a miss just leaves the deterministic sections unchanged."""
        from app.lifecycle.stages.analysis.handler import _named_targets
        from app.lifecycle.stages.architecture.schema import SystemComponent

        # Focus the match on the NAMED target (e.g. "Basic Information") + the affected screens —
        # NOT the whole requirement, whose generic words (agency, policy, quote, reporting) pull in
        # unrelated code files and crowd out the actual screen's component.
        terms: list[str] = list(_named_targets(context.requirement or ""))
        terms += [c.label for c in context.scr_cards if getattr(c, "label", "")]
        if not terms:
            terms = [context.requirement or ""]
        try:
            hits = await self._graph.code_cards_for(terms, limit=6)
        except Exception as exc:  # noqa: BLE001 — recall boost is best-effort
            log.warning(f"[dev] code-target match failed (non-fatal): {exc}")
            return
        existing = {c.id for c in context.cmp_cards} | {c.id for c in context.api_cards}
        added = 0
        for h in hits:
            if h["id"] in existing:
                continue
            context.cmp_cards.append(SystemComponent(
                id=h["id"], label=h["label"], kind="Component",
                responsibility="Existing implementation file impacted by this change — edit here.",
                source_locus=h["source_locus"], source_type="kb_explicit",
            ))
            context.allowed_ids.add(h["id"])
            added += 1
        # When we found the REAL files to edit, drop the abstract PROP-CMP-* "new component" scaffolds
        # (they're the extractive-empty fallback and only add noise once real code targets exist).
        if added:
            prop_ids = {c.id for c in context.cmp_cards if c.id.startswith("PROP-")}
            if prop_ids:
                context.cmp_cards = [c for c in context.cmp_cards if not c.id.startswith("PROP-")]
                context.allowed_ids -= prop_ids

    async def codegen_stream(
        self,
        workspace_id: str,
    ) -> AsyncIterator[tuple[str, Any]]:
        """Async generator: run the ReAct codegen agent and yield SSE tuples.

        Reads the persisted DevDocument → passes code_stubs + impl_plan to the
        LangGraph ReAct codegen loop (read_source_file / write_output_file tools).

        Events: ``status`` → ``result`` ({written_files, output_dir}) or ``error``.
        Prerequisite: DevDocument must exist (call generate_stream first).
        """
        from app.lifecycle.stages.developer.agent.codegen import run_codegen

        doc = await self.get(workspace_id)
        if doc is None:
            yield (
                "error",
                {
                    "detail": (
                        f"No DevDocument found for {workspace_id}. "
                        "Run /dev/generate/stream first to create a DevDocument."
                    )
                },
            )
            return

        async for event, payload in run_codegen(
            doc,
            workspace_id=workspace_id,
            model=self._model,
        ):
            yield event, payload

    async def accept(self, workspace_id: str, *, persona: str) -> Workspace:
        """Accept the Dev document → advance to QA_TESTING stage."""
        self._personas.require_capability(persona, self._CAPABILITY)
        return await self._workspace.advance(workspace_id, actor=f"persona:{persona}")

    async def record_pr(self, workspace_id: str, *, persona: str, pr: PrSyncRequest) -> PrSyncResult:
        """Sync a PR raised by the local Developer plugin back to central (docs/27 §6.1 P2).

        Records the PR as a ``dev-pr`` provenance artifact, then (when ``pr.advance`` and the workspace
        is in DEVELOPMENT) fires the guarded DEVELOPMENT → QA_TESTING transition. The state model is
        unchanged — the plugin is just another actor writing the DB (docs/27 §4).
        """
        self._personas.require_capability(persona, self._CAPABILITY)
        ws = await self._workspace.get(workspace_id)  # 404 if the workspace does not exist

        row = await self._workspace.attach_artifact(
            workspace_id,
            kind="dev-pr",
            content=pr.model_dump_json(),
            actor=f"persona:{persona}",
        )
        artifact_id = (row or {}).get("artifact_id")

        drift_recorded = await self._record_drift(workspace_id, ws.pinned_kb_version, pr, actor=f"persona:{persona}")

        advanced = False
        if pr.advance and ws.state is WorkspaceState.DEVELOPMENT:
            ws = await self._workspace.advance(workspace_id, actor=f"persona:{persona}")
            advanced = True

        return PrSyncResult(
            workspace_id=workspace_id,
            pr_url=pr.pr_url,
            artifact_id=artifact_id,
            state=str(ws.state),
            advanced=advanced,
            drift_recorded=drift_recorded,
        )

    async def _record_drift(
        self, workspace_id: str, pinned_kb_version: str | None, pr: PrSyncRequest, *, actor: str
    ) -> int:
        """File each KB-drift note as an OPEN review item (docs/27 §7). Never edits the KB (moat: SME
        adjudicates → kb-refresh). Degrades to 0 if the review table is absent or no version resolves."""
        if not pr.kb_drift:
            return 0
        from app.dao import graph_dao, review_dao
        from app.dao.postgres import get_pool

        pool = get_pool()
        kb_version = pinned_kb_version or await graph_dao.active_version(pool, self._workspace_gear_id())
        if not kb_version:
            log.warning(f"[dev] {workspace_id}: {len(pr.kb_drift)} drift note(s) not filed — no KB version resolved")
            return 0
        recorded = 0
        for i, note in enumerate(pr.kb_drift):
            gap_id = f"drift-{workspace_id}-{i}"
            body = f"[{', '.join(note.kb_ids) or 'no-kb-id'}] {note.file} — expected: {note.expected} | actual: {note.actual}"
            try:
                await review_dao.set_disposition(
                    pool, kb_version=kb_version, gap_id=gap_id, gap_type="kb-drift", severity="medium",
                    status="OPEN", note=body, by=actor, artifact_ref=pr.pr_url,
                )
                recorded += 1
            except Exception as exc:  # noqa: BLE001
                log.warning(f"[dev] {workspace_id}: could not file drift note {gap_id}: {type(exc).__name__}: {exc}")
        log.info(f"[dev] {workspace_id}: filed {recorded}/{len(pr.kb_drift)} KB-drift review item(s) @ {kb_version}")
        return recorded

    @staticmethod
    def _workspace_gear_id() -> str:
        """GEAR id for the active-version lookup (config-driven; used only when a workspace is unpinned)."""
        from app.config import get_settings

        return get_settings().GEAR_ID

    # ── pipeline steps ─────────────────────────────────────────────────────────

    async def _persist(
        self,
        workspace_id: str,
        persona: str,
        dev: DevDocument,
        context: DevContext,
    ) -> None:
        """S3 write + DB mirror + GROUNDS + DERIVES_FROM — all operations best-effort."""
        content = dev.model_dump_json()

        s3_uri: str | None = None
        try:
            s3_uri = await self._store.put_json(self._s3_key(workspace_id), dev.model_dump())
        except Exception as exc:  # noqa: BLE001
            log.warning(f"[dev] S3 write failed for {workspace_id}: {exc}")

        # Cleanup previous Developer artifacts on re-run
        try:
            await self._trace.cleanup_stage_artifacts(workspace_id, kind="dev")
        except Exception as exc:  # noqa: BLE001
            log.debug(f"[dev] Cleanup of previous Developer artifacts: {exc}")

        artifact_id: str | None = None
        try:
            artifact = await self._workspace.attach_artifact(
                workspace_id,
                kind="dev",
                content=content,
                s3_uri=s3_uri,
                grounding_score=dev.grounding_score,
                template_id=dev.template_id,
                template_version=dev.template_version,
                actor=f"persona:{persona}",
            )
            artifact_id = artifact.get("artifact_id")
        except Exception as exc:  # noqa: BLE001
            log.warning(f"[dev] DB artifact write failed for {workspace_id}: {exc}")

        if not artifact_id:
            return

        # Auto-link to previous stage with rich justification
        try:
            justification = RelationshipJustification.build_developer_from_stories(
                stories_count=0,  # TODO: fetch from prior STORIES
                files_modified_count=len(context.files_changed) if hasattr(context, 'files_changed') else 0,
                systems_affected=[{"system_name": s.get("system_name", s.get("system_id", "?")), "system_id": s.get("system_id", "?")} for s in (getattr(context, "systems_affected", None) or [])],
                components_implemented=[c.id for c in (context.cmp_cards or [])],
                apis_implemented=[api.id for api in (context.api_cards or [])],
                coverage_pct=dev.coverage.coverage_pct if hasattr(dev, 'coverage') and dev.coverage else 0.0,
            )

            await self._trace.auto_link_to_previous(
                workspace_id=workspace_id,
                current_artifact_id=artifact_id,
                current_kind="code",
                justification=justification,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning(f"[dev] auto_link_to_previous failed for {workspace_id}: {exc}")

        # DERIVES_FROM → SRD (immediate prerequisite) [LEGACY]
        if context.srd_ref:
            try:
                await self._trace.add_derives(
                    workspace_id=workspace_id,
                    from_artifact_id=artifact_id,
                    to_artifact_id=context.srd_ref,
                    stage="DEVELOPMENT",
                    reason="Development artifact derived from SRD",
                )
            except Exception as exc:  # noqa: BLE001
                log.warning(f"[dev] DERIVES_FROM(srd) link failed for {workspace_id}: {exc}")

        # GROUNDS links per component (CMP-*/API-* cards)
        for comp in context.cmp_cards + context.api_cards:
            try:
                await self._trace.add_grounds(
                    workspace_id=workspace_id,
                    from_artifact_id=artifact_id,
                    persona=persona,
                    artifact_kind="dev",
                    to_kb_card_id=comp.id,
                    kb_version=dev.kb_version,
                    source_locus=comp.source_locus,
                    stage="DEVELOPMENT",
                    applied_because="IMPLEMENTS",
                )
            except Exception as exc:  # noqa: BLE001
                log.warning(f"[dev] GROUNDS link failed for {workspace_id} → {comp.id}: {exc}")

        # Save Dev metrics to fe_workspace_analysis table for UI traceability tab
        try:
            dao = WorkspaceAnalysisDAO()

            # Extract systems from developer context (CMP and API components)
            systems = [
                {
                    "system_id": comp.id,
                    "system_name": comp.label or comp.id,
                    "impact_level": "medium",
                    "components_changed": [],
                    "confidence": 0.9,
                }
                for comp in (context.cmp_cards + context.api_cards) if comp.id.startswith("SYS-")
            ]

            # Extract scope from developer (all components being developed)
            scope = {
                "in_scope": [
                    {"item_id": c.id or f"comp-{i}", "item_description": c.label or c.description[:100], "scope_category": "IN_SCOPE"}
                    for i, c in enumerate(context.cmp_cards + context.api_cards)
                ],
                "out_of_scope": [],
                "deferred": [],
            }

            # Extract metrics
            metrics = {
                "total_artifacts": len(context.cmp_cards + context.api_cards),
                "total_relationships": 0,
                "coverage_pct": 100.0 if (context.cmp_cards + context.api_cards) else 0.0,
                "orphan_count": 0,
                "systems_affected": len(systems),
            }

            # Save to database
            if systems:
                await dao.update_systems(workspace_id, systems)
            await dao.update_scope(workspace_id, scope)
            await dao.update_metrics(workspace_id, metrics)

            log.info(f"[dev] Saved Dev data for {workspace_id}: {len(scope['in_scope'])} components")
        except Exception as exc:  # noqa: BLE001
            log.warning(f"[dev] Failed to save Dev data to traceability table for {workspace_id}: {exc}")

    async def _get_artifact_id(self, workspace_id: str, kind: str) -> str | None:
        """Retrieve the artifact_id of the most recent artifact of the given kind."""
        try:
            artifacts = await self._workspace.list_artifacts(workspace_id)
            matching = [a for a in artifacts if a.get("kind") == kind]
            return matching[-1].get("artifact_id") if matching else None
        except Exception as exc:  # noqa: BLE001
            log.warning(f"[dev] could not retrieve {kind} artifact_id for {workspace_id}: {exc}")
            return None
