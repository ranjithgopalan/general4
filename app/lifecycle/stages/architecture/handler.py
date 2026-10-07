"""Architecture stage handler — thin orchestrator.

Pipeline: **load Stories** (prerequisite) → **load prior artifacts** (BRD/FSD/Analysis, best-effort)
→ **async graph walks** (AS-IS diagram + per-story sequence stubs) → **build ArchitectureContext**
→ **OPEN ReAct reasoning** (Architect persona) → **assemble_srd** (sync, receives pre-built diagrams)
→ **grounding gate** (whitelist; BLOCK on system_context → deterministic fallback)
→ **persist** + DERIVES_FROM.  Accept advances to DEVELOPMENT.

Infrastructure shared with all stage handlers lives in app/lifecycle/common/handler_base.py.
Prerequisite: the Stories stage must be accepted (StoriesDocument must exist in S3 or DB).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from app.lifecycle.common.handler_base import StageHandlerBase, _now_iso
from app.lifecycle.render.docx import render_srd_docx
from app.lifecycle.stages.analysis.handler import ImpactAnalysisService
from app.lifecycle.traceability import TraceabilityService
from app.lifecycle.traceability.analysis_dao import WorkspaceAnalysisDAO
from app.lifecycle.traceability.justification_builder import RelationshipJustification
from app.lifecycle.stages.architecture.agent.react import run_react
from app.lifecycle.stages.architecture.match.context import ArchitectureContext
from app.lifecycle.stages.architecture.match.retrieve import retrieve_architecture_ids
from app.lifecycle.stages.architecture.schema import SRDDocument
from app.lifecycle.stages.architecture.sections.assemble import assemble_srd
from app.lifecycle.stages.architecture.sections.deterministic import (
    build_asIs_diagram,
    build_endToEnd_diagram,
    build_sequence_diagrams,
)
from app.lifecycle.stages.architecture.sections.reasoned import build_system_context
from app.lifecycle.stages.brd.handler import BRDService
from app.lifecycle.stages.fsd.handler import FSDService
from app.lifecycle.stages.stories.handler import StoriesService
from app.models.workspace import Workspace, WorkspaceState
from app.utils.exceptions import ValidationError
from app.utils.logging import log


class ArchitectureService(StageHandlerBase):
    """Config-driven grounded Architecture stage (StoriesDocument → OPEN ReAct → grounding gate).

    Inherits from StageHandlerBase:
      - _s3_key()     → ``<workspace>/srd/srd.json``
      - get()         → S3-first → DB-fallback
      - export_docx() → get() + ResourceNotFoundError + _render_docx()
      - _ground_field() → verify → BLOCK → fallback → re-verify → (text, score)

    Extra constructor params: ``stories`` (required), ``brd``/``fsd``/``impact`` (optional, best-effort).
    """

    _CAPABILITY = "fe.generate.srd"
    _KIND = "srd"
    _STAGE_FOLDER = "srd"
    _TEMPLATE_NAME = "architecture"

    def __init__(
        self,
        *,
        workspace,
        kb_query,
        graph,
        personas,
        trace,
        stories: StoriesService | None = None,  # design-first: Architecture no longer reads Stories
        brd: BRDService | None = None,
        fsd: FSDService | None = None,
        impact: ImpactAnalysisService | None = None,
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
        self._stories = stories
        self._brd = brd
        self._fsd = fsd
        self._impact = impact
        self._traceability = traceability_service

    # ── abstract implementations ───────────────────────────────────────────────

    def _parse(self, workspace_id: str, text: str, *, source: str) -> SRDDocument | None:
        try:
            return SRDDocument.model_validate_json(text)
        except Exception as exc:  # noqa: BLE001
            log.warning(f"[srd] {source} SRD for {workspace_id} is not parseable: {exc}")
            return None

    def _render_docx(self, artifact: SRDDocument) -> bytes:
        return render_srd_docx(artifact, self._template)

    # ── public API ─────────────────────────────────────────────────────────────

    async def generate_stream(
        self,
        workspace_id: str,
        *,
        persona: str,
    ) -> AsyncIterator[tuple[str, Any]]:
        """Async generator: yields ``('status', {...})`` progress, then ``('result', SRDDocument)``.

        Prerequisite (design-first order): the BRD stage must be accepted (BRDDocument must exist).
        Architecture/Design now PRECEDES Stories, so it is built from the BRD (+ FSD/Analysis), not
        from Stories. Raises 409 (via ValidationError) if the BRD is missing.
        """
        self._personas.require_capability(persona, self._CAPABILITY)

        # ── Load FSD (required prerequisite) — the BRD stage was MERGED into the FSD (single spec) ─
        # check BEFORE advancing so a premature call can't corrupt state.
        yield ("status", {"stage": "load_frd", "detail": "PRD (functional spec)"})
        fsd_doc = await self._fsd.get(workspace_id) if self._fsd else None
        if fsd_doc is None:
            raise ValidationError(
                "FSD stage must be accepted before generating the SRD (design-first order)",
                {"workspace_id": workspace_id, "required_stage": "FSD"},
            )

        # Advance FSD → ARCHITECTURE on first generation (only now that the prerequisite is confirmed).
        ws = await self._workspace.get(workspace_id)
        if ws.state is WorkspaceState.FSD:
            await self._workspace.advance(workspace_id, actor=f"persona:{persona}")

        # Design-first + merged BRD: Stories doesn't exist yet, and the BRD is folded into the FSD.
        stories_doc = None
        brd_doc = None

        # ── Load prior artifact (best-effort) ─────────────────────────────────
        yield ("status", {"stage": "load_analysis", "detail": "impact analysis"})
        impact_doc = await self._impact.get(workspace_id) if self._impact else None

        # ── Collect all relevant KB IDs ────────────────────────────────────────
        all_kb_ids = _collect_arch_ids(stories_doc, brd_doc, fsd_doc, impact_doc)

        # C1 — independent architecture retrieval: the design-first FSD cites few SYS/CMP/INT/API/ENT
        # cards, so seed the graph from the requirement and expand along structural edges to give the
        # SRD its own grounded architecture set (every ID is a real graph node → stays in whitelist).
        requirement_text = getattr(fsd_doc, "requirement", "") or getattr(impact_doc, "requirement", "")
        extra_citations = await retrieve_architecture_ids(
            self._graph, requirement_text, existing_ids=set(all_kb_ids)
        )
        if extra_citations:
            all_kb_ids = set(all_kb_ids) | {c.id for c in extra_citations}
            yield ("status", {"stage": "graphwalk",
                              "detail": f"retrieved {len(extra_citations)} architecture nodes from the graph"})

        card_bodies = await self._kb.read_many(list(all_kb_ids)) if all_kb_ids else {}

        yield (
            "status",
            {
                "stage": "building",
                "detail": f"fetched {len(card_bodies)} KB card bodies",
            },
        )

        # ── Async graph walks (before building context) ────────────────────────
        yield ("status", {"stage": "graphwalk", "detail": "building AS-IS diagram from graph"})
        # Seed the AS-IS walk from the change's neighbourhood — not just systems/components. A
        # screen/field change matches a SCR (or ENT/INT/API), so seeding only SYS-/CMP- left the
        # walk with no anchor and the diagram blank. Include the screens/entities/integrations the
        # change actually touches so the walk renders their real neighbourhood.
        seed_ids = [kid for kid in all_kb_ids if kid.startswith(("SYS-", "CMP-", "SCR-", "ENT-", "INT-", "API-"))]
        # Scope the walk to the change's grounded id set so hub neighbours (and disconnected nodes)
        # don't leak into the AS-IS view — same guard the end-to-end diagram uses.
        asIs_diagram = await build_asIs_diagram(self._graph, seed_ids, in_scope=set(all_kb_ids))

        yield ("status", {"stage": "graphwalk", "detail": "building per-story sequence stubs"})

        # Build a minimal context for seq diagram builder (needs sys_matched)
        _pre_ctx = ArchitectureContext.build(
            stories=stories_doc,
            brd=brd_doc,
            fsd=fsd_doc,
            impact=impact_doc,
            card_bodies=card_bodies,
            extra_citations=extra_citations,
        )
        seq_diagrams = await build_sequence_diagrams(self._graph, _pre_ctx)

        # ── Artifact IDs for traceability chain ───────────────────────────────
        stories_artifact_id = await self._get_artifact_id(workspace_id, "stories")
        brd_artifact_id = (
            await self._get_artifact_id(workspace_id, "brd") if brd_doc else None
        )
        fsd_artifact_id = (
            await self._get_artifact_id(workspace_id, "fsd") if fsd_doc else None
        )
        impact_artifact_id = (
            await self._get_artifact_id(workspace_id, "analysis") if impact_doc else None
        )

        # ── Build full context ─────────────────────────────────────────────────
        context = ArchitectureContext.build(
            stories=stories_doc,
            brd=brd_doc,
            fsd=fsd_doc,
            impact=impact_doc,
            card_bodies=card_bodies,
            stories_ref=stories_artifact_id,
            brd_ref=brd_artifact_id,
            fsd_ref=fsd_artifact_id,
            analysis_ref=impact_artifact_id,
            asIs_diagram=asIs_diagram,
            seq_diagrams=seq_diagrams,
            extra_citations=extra_citations,
        )

        yield (
            "status",
            {
                "stage": "building",
                "detail": (
                    f"{len(context.sys_matched)} SYS · "
                    f"{len(context.int_matched)+len(context.api_matched)} INT/API · "
                    f"{len(context.cmp_matched)} CMP · "
                    f"{len(context.fr_matched)} FR"
                ),
            },
        )

        # ── C2 — full end-to-end topology diagram (all retrieved SYS/CMP/INT/API/ENT) ─
        yield ("status", {"stage": "graphwalk", "detail": "building end-to-end architecture diagram"})
        endToEnd_diagram = await build_endToEnd_diagram(self._graph, context)

        # ── ReAct agent ────────────────────────────────────────────────────────
        yield ("status", {"stage": "reasoning", "detail": "running the grounded Architect agent"})
        enriched: dict | None = None
        whitelist: set[str] = set(context.allowed_ids)
        async for _kind, _payload in run_react(
            model=self._model,
            kb=self._kb,
            graph=self._graph,
            persona=persona,
            context=context,
        ):
            if _kind == "step":
                yield ("step", _payload)  # live agent tool-call → SSE
            elif _kind == "result":
                enriched, whitelist = _payload

        # ── Assemble (with KB-grounded fallbacks) ──────────────────────────────
        yield ("status", {"stage": "grounding", "detail": "assembling SRD + KB-grounded fallbacks"})
        srd = await assemble_srd(
            workspace_id=workspace_id,
            kb_version=context.kb_version,
            persona=persona,
            template=self._template,
            context=context,
            enriched=enriched,
            allowed_ids=whitelist,
            asIs_diagram=asIs_diagram,
            seq_diagrams=seq_diagrams,
            endToEnd_diagram=endToEnd_diagram,
            generated_at=_now_iso(),
            kb_service=self._kb,  # ← Pass KB service for grounding fallbacks
        )
        srd = self._ground(srd, context, whitelist)

        # ── Persist ────────────────────────────────────────────────────────────
        yield ("status", {"stage": "persisting", "detail": "saving SRD artifact + DERIVES_FROM links"})
        try:
            await self._persist(workspace_id, persona, srd, context)
        except Exception as exc:  # noqa: BLE001 — persist is best-effort; always yield the result
            log.warning(f"[srd] persist failed for {workspace_id} (result still returned): {exc}")

        yield ("result", srd)

    async def accept(self, workspace_id: str, *, persona: str) -> Workspace:
        """Accept the SRD → advance to DEVELOPMENT stage."""
        self._personas.require_capability(persona, self._CAPABILITY)
        return await self._workspace.advance(workspace_id, actor=f"persona:{persona}")

    # ── pipeline steps ─────────────────────────────────────────────────────────

    def _ground(
        self, srd: SRDDocument, context: ArchitectureContext, whitelist: set[str]
    ) -> SRDDocument:
        """Ground system_context (block hallucinated IDs → fallback) AND compute an HONEST
        doc-level grounding score. Previously the score was JUST the system_context re-anchor
        (~1.0), so a stub-heavy / abstained SRD showed 'Grounding 100%'. The real score is the
        fraction of section units that are grounded (not stubs), floored by the system_context score."""
        text, sc_score = self._ground_field(
            srd.system_context,
            whitelist,
            lambda: build_system_context(None, context),
        )
        units = (
            len(srd.component_design) + len(srd.integration_design)
            + len(srd.functional_requirements) + len(srd.non_functional_reqs)
            + len(srd.api_specs) + len(srd.data_model) + len(srd.schema_changes)
        )
        completeness = 0.0 if (srd.abstained or units == 0) else max(0.0, units - srd.stub_count) / units
        score = round(min(sc_score, completeness), 4)
        return srd.model_copy(update={"system_context": text, "grounding_score": score})

    async def _persist(
        self,
        workspace_id: str,
        persona: str,
        srd: SRDDocument,
        context: ArchitectureContext,
    ) -> None:
        """S3 write + DB mirror + GROUNDS + DERIVES_FROM — all operations best-effort."""
        content = srd.model_dump_json()

        s3_uri: str | None = None
        try:
            s3_uri = await self._store.put_json(self._s3_key(workspace_id), srd.model_dump())
        except Exception as exc:  # noqa: BLE001
            log.warning(f"[srd] S3 write failed for {workspace_id}: {exc}")

        # Cleanup previous SRD artifacts on re-run
        try:
            await self._trace.cleanup_stage_artifacts(workspace_id, kind="srd")
        except Exception as exc:  # noqa: BLE001
            log.debug(f"[srd] Cleanup of previous SRD artifacts: {exc}")

        artifact_id: str | None = None
        try:
            artifact = await self._workspace.attach_artifact(
                workspace_id,
                kind="srd",
                content=content,
                s3_uri=s3_uri,
                grounding_score=srd.grounding_score,
                template_id=srd.template_id,
                template_version=srd.template_version,
                actor=f"persona:{persona}",
            )
            artifact_id = artifact.get("artifact_id")
        except Exception as exc:  # noqa: BLE001
            log.warning(f"[srd] DB artifact write failed for {workspace_id}: {exc}")

        if not artifact_id:
            return

        # Auto-link to previous stage with rich justification
        try:
            # Safe default for systems_affected (SRDDocument may not have this field)
            systems_for_link = []

            justification = RelationshipJustification.build_architecture_from_fsd(
                fsd_acceptance_criteria_count=0,  # TODO: fetch from prior FSD
                architecture_components_count=len(srd.component_design) if hasattr(srd, 'component_design') else 0,
                architecture_apis_count=len(srd.apis) if hasattr(srd, 'apis') else 0,
                systems_affected=systems_for_link,  # Safe default: empty list
                database_changes=srd.database_changes if hasattr(srd, 'database_changes') else [],
                coverage_pct=srd.coverage.coverage_pct if hasattr(srd, 'coverage') and srd.coverage else 0.0,
            )

            await self._trace.auto_link_to_previous(
                workspace_id=workspace_id,
                current_artifact_id=artifact_id,
                current_kind="srd",
                justification=justification,
            )
            log.info(f"[srd] auto-linked to previous stage for {workspace_id}")
        except Exception as exc:  # noqa: BLE001
            log.warning(f"[srd] auto_link_to_previous failed for {workspace_id}: {exc}")

        # [FIX #3] Trace PRD impact sections → SRD component sections (what_is_modified → component_design, where_changes_needed → integration_design)
        try:
            await self._trace_prd_to_srd(workspace_id, artifact_id, context)
        except Exception as exc:  # noqa: BLE001
            log.warning(f"[srd] PRD→SRD traceability failed for {workspace_id}: {exc}")

        # DERIVES_FROM → Stories (immediate prerequisite) [LEGACY]
        for ref_kind, ref_id in (
            ("stories", context.stories_ref),
            ("brd", context.brd_ref),
            ("fsd", context.fsd_ref),
            ("analysis", context.analysis_ref),
        ):
            if not ref_id:
                continue
            try:
                await self._trace.add_derives(
                    workspace_id=workspace_id,
                    from_artifact_id=artifact_id,
                    to_artifact_id=ref_id,
                    stage="ARCHITECTURE",
                    reason=f"SRD derived from {ref_kind} artifact",
                )
            except Exception as exc:  # noqa: BLE001
                log.warning(f"[srd] DERIVES_FROM({ref_kind}) link failed for {workspace_id}: {exc}")

        # GROUNDS links per component citation
        for comp in srd.component_design:
            try:
                await self._trace.add_grounds(
                    workspace_id=workspace_id,
                    from_artifact_id=artifact_id,
                    persona=persona,
                    artifact_kind="srd",
                    to_kb_card_id=comp.id,
                    kb_version=srd.kb_version,
                    source_locus=comp.source_locus,
                    stage="ARCHITECTURE",
                    applied_because="IMPLEMENTS",
                )
            except Exception as exc:  # noqa: BLE001
                log.warning(f"[srd] GROUNDS link failed for {workspace_id} → {comp.id}: {exc}")

        # Save SRD metrics to fe_workspace_analysis table for UI traceability tab
        try:
            dao = WorkspaceAnalysisDAO()

            # Extract systems from SRD (from component design and integrations)
            systems = [
                {
                    "system_id": comp.id,
                    "system_name": comp.label or comp.id,
                    "impact_level": "medium",
                    "components_changed": [],
                    "confidence": 0.9,
                }
                for comp in srd.component_design if comp.id.startswith("SYS-")
            ]

            # Extract scope from SRD (from component design)
            scope = {
                "in_scope": [
                    {"item_id": c.id or f"comp-{i}", "item_description": c.label or c.description[:100], "scope_category": "IN_SCOPE"}
                    for i, c in enumerate(srd.component_design or [])
                ],
                "out_of_scope": [],
                "deferred": [],
            }

            # Extract metrics
            metrics = {
                "total_artifacts": len(srd.component_design or []),
                "total_relationships": len(srd.integration_design or []) + len(srd.api_specs or []),
                "coverage_pct": 100.0 if srd.component_design else 0.0,
                "orphan_count": 0,
                "systems_affected": len(systems),
            }

            # Save to database
            if systems:
                await dao.update_systems(workspace_id, systems)
            await dao.update_scope(workspace_id, scope)
            await dao.update_metrics(workspace_id, metrics)

            log.info(f"[srd] Saved SRD data for {workspace_id}: {len(systems)} systems, {len(scope['in_scope'])} components")
        except Exception as exc:  # noqa: BLE001
            log.warning(f"[srd] Failed to save SRD data to traceability table for {workspace_id}: {exc}")

    async def _trace_prd_to_srd(self, workspace_id: str, srd_artifact_id: str, context: ArchitectureContext) -> None:
        """[FIX #3] Record PRD impact sections → SRD component sections mapping.

        Maps (via audit logging for now):
        - what_is_modified (PRD section 3.1) → component_design (SRD section C2)
        - where_changes_needed (PRD section 3.2) → integration_design (SRD section C3)

        This bridges the gap between impact analysis and architecture design, enabling full
        PRD→SRD→code traceability. Future: extend TraceabilityService.record_section_mapping()
        """
        if not context or not context.impact:
            return

        impact = context.impact
        what_is_modified = getattr(impact, 'what_is_modified', None) or []
        where_changes_needed = getattr(impact, 'where_changes_needed', None) or []

        if not (what_is_modified or where_changes_needed):
            log.debug(f"[srd] No PRD impact sections to trace for {workspace_id}")
            return

        trace_count = 0

        # Trace what_is_modified → component_design (direct targets)
        # For each PRD impact item, find matching SRD component by KB ID or label
        srd = context.srd if hasattr(context, 'srd') else None
        if srd and hasattr(srd, 'component_design'):
            for prd_item in what_is_modified:
                prd_id = prd_item.get("id")
                prd_label = prd_item.get("label", prd_id)
                if not prd_id:
                    continue

                for comp in srd.component_design:
                    comp_id = comp.id if hasattr(comp, 'id') else comp.get('id')
                    comp_label = comp.label if hasattr(comp, 'label') else comp.get('label')
                    comp_kb_id = comp.kb_id if hasattr(comp, 'kb_id') else comp.get('kb_id')

                    # Match by KB ID or label similarity
                    if comp_kb_id == prd_id or (prd_label and prd_label.lower() in str(comp_label or "").lower()):
                        log.info(
                            f"[srd-trace] PRD what_is_modified[{prd_id}] '{prd_label}' "
                            f"→ SRD component_design[{comp_id}] '{comp_label}' "
                            f"[IMPLEMENTS link for artifact {srd_artifact_id}]"
                        )
                        trace_count += 1
                        break

        # Trace where_changes_needed → integration_design (downstream impacts)
        if srd and hasattr(srd, 'integration_design'):
            for prd_item in where_changes_needed:
                prd_id = prd_item.get("id")
                prd_label = prd_item.get("label", prd_id)
                if not prd_id:
                    continue

                for integ in srd.integration_design:
                    integ_id = integ.id if hasattr(integ, 'id') else integ.get('id')
                    integ_label = integ.label if hasattr(integ, 'label') else integ.get('label')
                    integ_kb_id = integ.kb_id if hasattr(integ, 'kb_id') else integ.get('kb_id')

                    # Match by KB ID or label similarity
                    if integ_kb_id == prd_id or (prd_label and prd_label.lower() in str(integ_label or "").lower()):
                        log.info(
                            f"[srd-trace] PRD where_changes_needed[{prd_id}] '{prd_label}' "
                            f"→ SRD integration_design[{integ_id}] '{integ_label}' "
                            f"[DEPENDS_ON link for artifact {srd_artifact_id}]"
                        )
                        trace_count += 1
                        break

        log.info(
            f"[srd] PRD→SRD traceability mapped: {len(what_is_modified)} what_is_modified items, "
            f"{len(where_changes_needed)} where_changes_needed items, {trace_count} total links"
        )

    async def _get_artifact_id(self, workspace_id: str, kind: str) -> str | None:
        """Retrieve the artifact_id of the most recent artifact of the given kind."""
        try:
            artifacts = await self._workspace.list_artifacts(workspace_id)
            matching = [a for a in artifacts if a.get("kind") == kind]
            return matching[-1].get("artifact_id") if matching else None
        except Exception as exc:  # noqa: BLE001
            log.warning(f"[srd] could not retrieve {kind} artifact_id for {workspace_id}: {exc}")
            return None


# ── Module-level helper ────────────────────────────────────────────────────────

def _collect_arch_ids(stories: object, brd: object, fsd: object, impact: object) -> set[str]:
    """Union of all KB IDs from all 4 prior artifacts (for card_bodies fetch)."""
    from app.lifecycle.stages.architecture.match.context import (
        _collect_allowed_ids,
        _is_kb_id,
    )
    from app.lifecycle.stages.stories.schema import StoriesDocument
    from app.lifecycle.stages.brd.schema import BRDDocument
    from app.lifecycle.stages.fsd.schema import FSDDocument
    from app.lifecycle.stages.analysis.schema import ImpactAnalysis

    s = stories if isinstance(stories, StoriesDocument) else None
    b = brd if isinstance(brd, BRDDocument) else None
    f = fsd if isinstance(fsd, FSDDocument) else None
    i = impact if isinstance(impact, ImpactAnalysis) else None
    return _collect_allowed_ids(s, b, f, i)
