"""Stories stage handler — thin orchestrator (plan: stories-implementation.md).

Pipeline: **load BRD** (prerequisite) → **build StoriesContext** (deterministic, from BRD) →
**OPEN ReAct reasoning** (grounded, tool-using; BA persona) → **assemble_stories** (15-step) →
**persist** + GROUNDS + DERIVES_FROM.  Accept advances to ARCHITECTURE.

Infrastructure shared with FSD, BRD, and Analysis lives in app/lifecycle/common/handler_base.py.
Prerequisite: the BRD stage must be complete (accepted BRD artifact must exist in S3 or DB).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from app.lifecycle.common.handler_base import StageHandlerBase, _now_iso
from app.lifecycle.stages.fsd.handler import FSDService
from app.lifecycle.traceability import TraceabilityService
from app.lifecycle.traceability.analysis_dao import WorkspaceAnalysisDAO
from app.lifecycle.traceability.justification_builder import RelationshipJustification
from app.lifecycle.stages.stories.agent.react import run_react
from app.lifecycle.stages.stories.match.context import StoriesContext
from app.lifecycle.stages.stories.schema import StoriesDocument
from app.lifecycle.stages.stories.sections.assemble import assemble_stories
from app.models.workspace import Workspace, WorkspaceState
from app.utils.exceptions import ValidationError
from app.utils.logging import log


class StoriesService(StageHandlerBase):
    """Config-driven grounded Stories stage (BRD artifact → OPEN ReAct → grounding → persist).

    Inherits from StageHandlerBase:
      - _s3_key() → ``<workspace>/stories/stories.json``
      - get()     → S3-first → DB-fallback
      - _ground_field() → verify → BLOCK → fallback → re-verify → (text, score)

    Adds constructor params: ``fsd`` (FSDService — grounding source; BRD was merged into the FSD) and
    ``srd`` (Architecture service — the design-first prerequisite gate).
    """

    _CAPABILITY = "fe.generate.stories"
    _KIND = "stories"
    _STAGE_FOLDER = "stories"
    _TEMPLATE_NAME = "stories"

    def __init__(
        self,
        *,
        workspace,
        kb_query,
        graph,
        personas,
        trace,
        fsd: FSDService,  # BRD merged into FSD: Stories is grounded in the FSD (references/change_class)
        srd: Any = None,  # design-first: the Architecture (SRD) service — the prerequisite gate
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
        self._fsd = fsd  # reads the FSD (merged Business & Functional Spec) for grounding context
        self._srd = srd  # reads the accepted SRD (design) — the design-first prerequisite (duck-typed .get)
        self._traceability = traceability_service

    # ── abstract implementations ───────────────────────────────────────────────────

    def _parse(self, workspace_id: str, text: str, *, source: str) -> StoriesDocument | None:
        try:
            return StoriesDocument.model_validate_json(text)
        except Exception as exc:  # noqa: BLE001
            log.warning(f"[stories] {source} stories for {workspace_id} is not parseable: {exc}")
            return None

    def _render_docx(self, artifact: StoriesDocument) -> bytes:  # type: ignore[override]
        raise NotImplementedError("Stories exports as CSV, not docx. Use export_csv() instead.")

    # ── public API ─────────────────────────────────────────────────────────────────

    async def generate_stream(
        self,
        workspace_id: str,
        *,
        persona: str,
    ) -> AsyncIterator[tuple[str, Any]]:
        """Async generator: yields ``('status', {...})`` progress, then ``('result', StoriesDocument)``.

        Prerequisite (design-first order): the ARCHITECTURE stage must be accepted (SRDDocument must
        exist) — stories are written against the design. The BRD (accepted earlier) is still read for
        context. Raises ValidationError (409) if the SRD is missing.
        """
        self._personas.require_capability(persona, self._CAPABILITY)

        # Prerequisite check BEFORE any state change: verify the SRD exists (Architecture accepted),
        # THEN advance — a premature call must not corrupt the workspace state.
        yield ("status", {"stage": "load_srd", "detail": "design + component specs"})
        srd_doc = await self._srd.get(workspace_id) if self._srd else None
        if srd_doc is None:
            raise ValidationError(
                "Architecture (SRD) stage must be accepted before generating stories (design-first order)",
                {"workspace_id": workspace_id, "required_stage": "ARCHITECTURE"},
            )
        # The FSD (Business & Functional Spec — the merged BRD) is the prior accepted artifact stories
        # are grounded in. It carries the same handles Stories needs (references, change_class).
        fsd_doc = await self._fsd.get(workspace_id) if self._fsd else None
        if fsd_doc is None:
            raise ValidationError(
                "FSD stage must be accepted before generating stories",
                {"workspace_id": workspace_id, "required_stage": "FSD"},
            )

        # Advance ARCHITECTURE → STORIES on first generation (only now that the prerequisite is confirmed).
        ws = await self._workspace.get(workspace_id)
        if ws.state is WorkspaceState.ARCHITECTURE:
            await self._workspace.advance(workspace_id, actor=f"persona:{persona}")

        yield ("status", {"stage": "building", "detail": "building Stories context from the PRD"})
        fsd_artifact_id = await self._get_artifact_id(workspace_id, "fsd")

        # Fetch card bodies for all KB cards the FSD grounded to (BR/FR/PROC/ROLE).
        all_card_ids = [c.id for c in fsd_doc.references]
        card_bodies = await self._kb.read_many(all_card_ids) if all_card_ids else {}

        # DIAGNOSTIC: Log card fetch results (debug ECS deployment issues)
        log.info(
            f"[stories] KB read: requested {len(all_card_ids)} cards, got {len(card_bodies)} cards. "
            f"IDs: {all_card_ids[:3]}{'...' if len(all_card_ids) > 3 else ''}. "
            f"Keys in response: {list(card_bodies.keys())[:3]}{'...' if len(card_bodies) > 3 else ''}"
        )

        # DIAGNOSTIC: Show exact kind values stored in fsd_doc.references before filtering.
        # This reveals whether kind is "SCR", "Screen", "" or something else entirely.
        ref_kind_dist: dict[str, int] = {}
        for c in fsd_doc.references:
            ref_kind_dist[c.kind or "(empty)"] = ref_kind_dist.get(c.kind or "(empty)", 0) + 1
        log.info(f"[stories] fsd_doc.references kind distribution: {ref_kind_dist} (total={len(fsd_doc.references)})")

        ctx = StoriesContext.build(
            fsd_doc,
            artifact_id=fsd_artifact_id,
            card_bodies=card_bodies,
        )

        log.info(
            f"[stories] Context built: FR={len(ctx.fr_matched)} BR={len(ctx.br_matched)} "
            f"PROC={len(ctx.proc_matched)} ROLE={len(ctx.role_matched)} SCR={len(ctx.scr_matched)} "
            f"matched_total={len(ctx.matched)}"
        )

        yield (
            "status",
            {
                "stage": "building",
                "detail": (
                    f"{len(ctx.fr_matched)} FR · {len(ctx.br_matched)} BR · "
                    f"{len(ctx.proc_matched)} PROC · {len(ctx.role_matched)} ROLE · "
                    f"{len(ctx.scr_matched)} SCR · "
                    f"mode={'CONTEXTUAL' if ctx.is_contextual else 'CONTENT'}"
                ),
            },
        )

        # EARLY EXIT: If NO KB cards (FR/BR/PROC/ROLE/SCR), skip ReAct entirely
        # Empty context prevents agent from generating meaningful stories; prevents retry loop.
        total_cards = len(ctx.fr_matched) + len(ctx.br_matched) + len(ctx.proc_matched) + len(ctx.role_matched) + len(ctx.scr_matched)

        # ADDITIONAL CHECK: If card_ids exist but card_bodies is empty, log warning (KB read failure in ECS?)
        if all_card_ids and not card_bodies:
            log.warning(
                f"[stories] KB READ FAILURE: {len(all_card_ids)} card IDs requested but card_bodies is empty. "
                f"This will cause agent to generate stub stories with no content. "
                f"Possible causes: KB service unavailable in ECS, credentials issue, or timeout."
            )

        if total_cards == 0:
            log.info(f"[stories] EMPTY CONTEXT: no FR/BR/PROC/ROLE/SCR cards found — abstaining (no ReAct)")
            enriched = {}  # Empty enrichment → assemble_stories will return abstained=True
        else:
            yield ("status", {"stage": "reasoning", "detail": "running the grounded Stories agent"})
            enriched = {}
            async for _kind, _payload in run_react(
                model=self._model,
                kb=self._kb,
                graph=self._graph,
                persona=persona,
                context=ctx,
            ):
                if _kind == "step":
                    yield ("step", _payload)  # live agent tool-call → SSE
                elif _kind == "result":
                    enriched, _whitelist = _payload
            log.info(
                f"[stories] ReAct returned: stories={len(enriched.get('stories', []))} "
                f"open_items={len(enriched.get('open_items', []))} "
                f"enriched_keys={list(enriched.keys())}"
            )
        yield ("status", {"stage": "assembling", "detail": "running 15-step stories pipeline"})
        doc = await assemble_stories(
            ctx,
            workspace_id=workspace_id,
            persona=persona,
            enriched=enriched,
            kb_version=ctx.kb_version,
        )

        yield ("status", {"stage": "persisting", "detail": "saving stories artifact + GROUNDS links"})
        try:
            await self._persist(workspace_id, persona, doc, ctx)
        except Exception as exc:  # noqa: BLE001
            log.warning(f"[stories] persist failed for {workspace_id} (result still returned): {exc}")
        yield ("result", doc)

    async def accept(self, workspace_id: str, *, persona: str) -> Workspace:
        """Accept stories → handoff to offline dev/QA (or advance if production).

        When FE_STORIES_HANDOFF_MODE_ENABLED=True:
          - Pause at STORIES state
          - Dev/QA work locally offline
          - They POST results via /ws/{id}/dev/submit and /ws/{id}/qa/submit

        When FE_STORIES_HANDOFF_MODE_ENABLED=False (production):
          - Auto-advance to DEVELOPMENT
          - Continue normal pipeline
        """
        from app.config.settings import get_settings

        self._personas.require_capability(persona, self._CAPABILITY)

        settings = get_settings()
        ws = await self._workspace.get(workspace_id)

        if settings.FE_STORIES_HANDOFF_MODE_ENABLED:
            # Handoff mode: pause at STORIES, let dev/QA work offline
            log.info(
                f"[stories] Accepted by {persona} for {workspace_id} — "
                f"handoff mode enabled (paused at STORIES). "
                f"Dev/QA can now work offline and POST results via "
                f"/ws/{workspace_id}/dev/submit and /ws/{workspace_id}/qa/submit"
            )
            return ws  # Return current state, NO state change
        else:
            # Production: advance normally
            log.info(f"[stories] Accepted by {persona} for {workspace_id} — auto-advancing to DEVELOPMENT")
            return await self._workspace.advance(workspace_id, actor=f"persona:{persona}")

    # ── pipeline steps ────────────────────────────────────────────────────────────

    async def _persist(
        self,
        workspace_id: str,
        persona: str,
        doc: StoriesDocument,
        ctx: StoriesContext,
    ) -> None:
        """S3 write + DB mirror + DERIVES_FROM (Stories→BRD) + GROUNDS (per story, per KB card).

        All 4 steps are best-effort (never raises). Each step is individually try/excepted.
        applied_because: IMPLEMENTS for New/Enhancement; DERIVES_FROM_RULE for Derived (xpf).
        """
        content = doc.model_dump_json()

        # 1. S3 write (overwrite on re-run — single artifact object per workspace/stage).
        s3_uri: str | None = None
        try:
            s3_uri = await self._store.put_json(self._s3_key(workspace_id), doc.model_dump())
        except Exception as exc:  # noqa: BLE001
            log.warning(f"[stories] S3 write failed for {workspace_id}: {exc}")

        # 2. DB mirror (audit trail + fallback for GET when S3 misses), with cleanup on re-run
        # Cleanup previous Stories artifacts on re-run
        try:
            await self._trace.cleanup_stage_artifacts(workspace_id, kind="stories")
        except Exception as exc:  # noqa: BLE001
            log.debug(f"[stories] Cleanup of previous Stories artifacts: {exc}")

        artifact_id: str | None = None
        try:
            artifact = await self._workspace.attach_artifact(
                workspace_id,
                kind="stories",
                content=content,
                s3_uri=s3_uri,
                grounding_score=doc.grounding_score,
                template_id=doc.template_id,
                template_version=doc.template_version,
                actor=f"persona:{persona}",
            )
            artifact_id = artifact.get("artifact_id")
        except Exception as exc:  # noqa: BLE001
            log.warning(f"[stories] DB artifact write failed for {workspace_id}: {exc}")

        if not artifact_id:
            return

        # 3. Auto-link to previous stage with rich justification
        try:
            # Safe default for systems_affected (StoriesDocument may not have this field)
            systems_for_link = []

            justification = RelationshipJustification.build_stories_from_architecture(
                architecture_components_count=0,  # TODO: fetch from prior SRD
                stories_count=len(doc.stories) if hasattr(doc, 'stories') else 0,
                total_story_points=sum(s.points for s in (doc.stories or [])),
                systems_affected=systems_for_link,  # Safe default: empty list
                coverage_pct=doc.coverage.coverage_pct if hasattr(doc, 'coverage') and doc.coverage else 0.0,
            )

            await self._trace.auto_link_to_previous(
                workspace_id=workspace_id,
                current_artifact_id=artifact_id,
                current_kind="stories",
                justification=justification,
            )
            log.info(f"[stories] auto-linked to previous stage for {workspace_id}")
        except Exception as exc:  # noqa: BLE001
            log.warning(f"[stories] auto_link_to_previous failed for {workspace_id}: {exc}")

        # 3a. DERIVES_FROM link → BRD artifact. [LEGACY]
        if ctx.brd_ref:
            try:
                await self._trace.add_derives(
                    workspace_id=workspace_id,
                    from_artifact_id=artifact_id,
                    to_artifact_id=ctx.brd_ref,
                    stage="STORIES",
                    reason="Stories derived from BRD artifact",
                )
            except Exception as exc:  # noqa: BLE001
                log.warning(f"[stories] DERIVES_FROM link failed for {workspace_id}: {exc}")

        # 4. GROUNDS links — per story, per KB card cited.
        #    DERIVES_FROM_RULE for Derived-class stories (xpf pattern).
        #    IMPLEMENTS for New/Enhancement stories.
        for story in doc.stories:
            link_type = (
                "DERIVES_FROM_RULE" if story.change_class == "Derived" else "IMPLEMENTS"
            )
            for kid in story.source_refs:
                try:
                    await self._trace.add_grounds(
                        workspace_id=workspace_id,
                        from_artifact_id=artifact_id,
                        source_item_id=story.story_id,  # item-level: story STR-* → KB card (RTM P1)
                        persona=persona,
                        artifact_kind="stories",
                        to_kb_card_id=kid,
                        kb_version=doc.kb_version,
                        source_locus=story.source_locus,
                        stage="STORIES",
                        applied_because=link_type,
                    )
                except Exception as exc:  # noqa: BLE001
                    log.warning(
                        f"[stories] GROUNDS link failed for {workspace_id} "
                        f"story {story.story_id} → {kid}: {exc}"
                    )

        # 5. Save Stories metrics to fe_workspace_analysis table for UI traceability tab
        try:
            dao = WorkspaceAnalysisDAO()

            # Extract systems from stories (from source_refs if they're SYS-*)
            systems = []
            for story in doc.stories:
                for ref in story.source_refs:
                    if ref.startswith("SYS-"):
                        systems.append({
                            "system_id": ref,
                            "system_name": ref,
                            "impact_level": "medium",
                            "components_changed": [],
                            "confidence": 0.9,
                        })

            # Extract scope from stories (New/Enhancement/Existing/Derived classifications)
            scope = {
                "in_scope": [
                    {"item_id": s.story_id or f"story-{i}", "item_description": s.title or s.narrative[:100], "scope_category": "IN_SCOPE"}
                    for i, s in enumerate([st for st in doc.stories if st.change_class in ("New", "Enhancement")])
                ],
                "out_of_scope": [
                    {"item_id": s.story_id or f"story-{i}", "item_description": s.title or s.narrative[:100], "scope_category": "OUT_OF_SCOPE"}
                    for i, s in enumerate([st for st in doc.stories if st.change_class == "Existing"])
                ],
                "deferred": [
                    {"item_id": s.story_id or f"story-{i}", "item_description": s.title or s.narrative[:100], "scope_category": "DEFERRED"}
                    for i, s in enumerate([st for st in doc.stories if st.change_class == "Derived"])
                ],
            }

            # Extract metrics
            metrics = {
                "total_artifacts": len(doc.stories or []),
                "total_relationships": len([s for st in doc.stories for s in st.source_refs]),
                "coverage_pct": (len([s for st in doc.stories for s in st.source_refs]) / max(len(doc.stories or []), 1)) * 100,
                "orphan_count": len([s for st in doc.stories if not st.source_refs]),
                "systems_affected": len(set(sys["system_id"] for sys in systems)),
            }

            # Save to database
            if systems:
                await dao.update_systems(workspace_id, systems)
            await dao.update_scope(workspace_id, scope)
            await dao.update_metrics(workspace_id, metrics)

            log.info(f"[stories] Saved Stories data for {workspace_id}: {len(doc.stories)} stories, {len(scope['in_scope'])} in-scope")
        except Exception as exc:  # noqa: BLE001
            log.warning(f"[stories] Failed to save Stories data to traceability table for {workspace_id}: {exc}")

    async def _get_artifact_id(self, workspace_id: str, kind: str) -> str | None:
        """Retrieve the artifact_id of the most recent artifact of ``kind`` (for the DERIVES_FROM link)."""
        try:
            artifacts = await self._workspace.list_artifacts(workspace_id)
            hits = [a for a in artifacts if a.get("kind") == kind]
            return hits[-1].get("artifact_id") if hits else None
        except Exception as exc:  # noqa: BLE001
            log.warning(f"[stories] could not retrieve {kind} artifact_id for {workspace_id}: {exc}")
            return None
