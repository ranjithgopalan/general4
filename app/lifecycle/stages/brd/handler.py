"""BRD stage handler — thin orchestrator.

Pipeline: **load FSD** (prerequisite) → **build BRDContext** (deterministic, from FSD artifact) →
**OPEN ReAct reasoning** (grounded, tool-using; business-language BA persona) → **assemble**
BRDDocument → **grounding gate** (whitelist; BLOCK on business_case → deterministic fallback) →
**persist** + GROUNDS.  Accept advances to STORIES.  Export renders the SAME artifact to
official AIG .docx (with AIG logo, version table, sign-off strip).

Infrastructure shared with FSD and analysis lives in app/lifecycle/common/handler_base.py.
Prerequisite: the FSD stage must be complete (accepted FSD artifact must exist in S3 or DB).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from app.lifecycle.common.handler_base import StageHandlerBase, _now_iso
from app.lifecycle.render.docx import render_brd_docx
from app.lifecycle.stages.brd.agent.react import run_react
from app.lifecycle.stages.brd.match.context import BRDContext
from app.lifecycle.stages.brd.schema import BRDDocument
from app.lifecycle.stages.brd.sections.assemble import assemble_brd
from app.lifecycle.stages.brd.sections.reasoned import fallback_business_case
from app.lifecycle.stages.fsd.handler import FSDService
from app.models.workspace import Workspace, WorkspaceState
from app.utils.exceptions import ValidationError
from app.utils.logging import log


class BRDService(StageHandlerBase):
    """Config-driven grounded BRD stage (FSD artifact → OPEN ReAct → grounding gate).

    Inherits from StageHandlerBase:
      - _s3_key() → ``<workspace>/brd/brd.json``
      - get()     → S3-first → DB-fallback
      - export_docx() → get() + ResourceNotFoundError + _render_docx()
      - _ground_field() → verify → BLOCK → fallback → re-verify → (text, score)

    Adds one extra constructor param: ``fsd`` (FSDService) — reads the prerequisite
    FSD artifact.  The 8 shared params are forwarded to StageHandlerBase.__init__.
    """

    _CAPABILITY = "fe.generate.brd"
    _KIND = "brd"
    _STAGE_FOLDER = "brd"
    _TEMPLATE_NAME = "brd"

    def __init__(
        self,
        *,
        workspace,
        kb_query,
        graph,
        personas,
        trace,
        fsd: FSDService,
        spine=None,
        model=None,
        store=None,
        template_version: str | None = None,
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
        self._fsd = fsd  # reads the prerequisite FSD artifact

    # ── abstract implementations ───────────────────────────────────────────────────

    def _parse(self, workspace_id: str, text: str, *, source: str) -> BRDDocument | None:
        try:
            return BRDDocument.model_validate_json(text)
        except Exception as exc:  # noqa: BLE001
            log.warning(f"[brd] {source} BRD for {workspace_id} is not parseable: {exc}")
            return None

    def _render_docx(self, artifact: BRDDocument) -> bytes:
        return render_brd_docx(artifact, self._template)

    # ── public API ─────────────────────────────────────────────────────────────────

    async def generate_stream(
        self,
        workspace_id: str,
        *,
        persona: str,
        stakeholder_input: str | None = None,
    ) -> AsyncIterator[tuple[str, Any]]:
        """Async generator: yields ``('status', {...})`` progress, then ``('result', BRDDocument)``.

        Prerequisite: the FSD stage must be accepted (FSD artifact must exist in S3 or DB).
        Raises 409 (via ValidationError) if the FSD is missing.

        Args:
            workspace_id: the workspace to generate the BRD for.
            persona: the requesting persona (must have fe.generate.brd capability).
            stakeholder_input: optional BA-provided text (additional context beyond the FSD).
        """
        self._personas.require_capability(persona, self._CAPABILITY)

        # Prerequisite check BEFORE any state change: a premature call must NOT corrupt the workspace
        # state. Verify the FSD exists, THEN advance FSD → BRD.
        yield ("status", {"stage": "loading", "detail": "reading accepted FSD artifact"})
        fsd_doc = await self._fsd.get(workspace_id)
        if fsd_doc is None:
            raise ValidationError(
                "FSD stage must be accepted before generating the BRD",
                {"workspace_id": workspace_id, "required_stage": "FSD"},
            )

        # Advance FSD → BRD on first generation (only now that the prerequisite is confirmed).
        ws = await self._workspace.get(workspace_id)
        if ws.state is WorkspaceState.FSD:
            await self._workspace.advance(workspace_id, actor=f"persona:{persona}")

        yield ("status", {"stage": "building", "detail": "building BRD context from FSD"})
        fsd_artifact_id = await self._get_fsd_artifact_id(workspace_id)

        # Fetch card bodies for all KB cards the FSD grounded to (BR/FR/DOM/ROLE).
        # Best-effort: returns {} in fixture/dev mode → builders fall back to label.
        all_card_ids = [c.id for c in fsd_doc.references]
        card_bodies = await self._kb.read_many(all_card_ids) if all_card_ids else {}

        context = BRDContext.build(
            fsd_doc,
            stakeholder_input=stakeholder_input,
            card_bodies=card_bodies,
            artifact_id=fsd_artifact_id,
        )
        yield (
            "status",
            {
                "stage": "building",
                "detail": (
                    f"{len(context.br_matched)} BR · {len(context.fr_matched)} FR · "
                    f"{len(context.dom_matched)} DOM · {len(context.role_matched)} ROLE"
                ),
            },
        )

        yield ("status", {"stage": "reasoning", "detail": "running the grounded BRD agent"})
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

        yield ("status", {"stage": "grounding", "detail": "verifying citations (cite-or-abstain)"})
        brd = assemble_brd(
            workspace_id=workspace_id,
            kb_version=context.kb_version,
            persona=persona,
            template=self._template,
            context=context,
            enriched=enriched,
            allowed_ids=whitelist,
            generated_at=_now_iso(),
        )
        brd = self._ground(brd, context, whitelist)

        yield ("status", {"stage": "persisting", "detail": "saving BRD artifact + GROUNDS links"})
        try:
            await self._persist(workspace_id, persona, brd, context)
        except Exception as exc:  # noqa: BLE001 — persist is best-effort; always yield the result
            log.warning(f"[brd] persist failed for {workspace_id} (result still returned): {exc}")
        yield ("result", brd)

    async def accept(self, workspace_id: str, *, persona: str) -> Workspace:
        """Accept the BRD → advance to STORIES stage."""
        self._personas.require_capability(persona, self._CAPABILITY)
        return await self._workspace.advance(workspace_id, actor=f"persona:{persona}")

    # ── pipeline steps ────────────────────────────────────────────────────────────

    def _ground(
        self, brd: BRDDocument, context: BRDContext, whitelist: set[str]
    ) -> BRDDocument:
        """Grounding gate on business_case — block on hallucinated ids → deterministic fallback."""
        text, score = self._ground_field(
            brd.business_case,
            whitelist,
            lambda: fallback_business_case(context),
        )
        return brd.model_copy(update={"business_case": text, "grounding_score": score})

    async def _persist(
        self,
        workspace_id: str,
        persona: str,
        brd: BRDDocument,
        context: BRDContext,
    ) -> None:
        """S3 write + DB mirror + GROUNDS links — ALL operations are best-effort (never raises).

        S3: single overwritten object per workspace (re-run overwrites same key).
        DB: mirrors the S3 content as audit trail + read fallback.
        DERIVES_FROM: BRD → FSD (FSD is the prerequisite; FSD → Analysis already exists transitively).
        GROUNDS: per AC-row (BRD artifact → KB cards cited in acceptance_criteria).
        """
        content = brd.model_dump_json()

        # 1. S3 write
        s3_uri: str | None = None
        try:
            s3_uri = await self._store.put_json(self._s3_key(workspace_id), brd.model_dump())
        except Exception as exc:  # noqa: BLE001
            log.warning(f"[brd] S3 write failed for {workspace_id}: {exc}")

        # 2. DB mirror
        artifact_id: str | None = None
        try:
            artifact = await self._workspace.attach_artifact(
                workspace_id,
                kind="brd",
                content=content,
                s3_uri=s3_uri,
                grounding_score=brd.grounding_score,
                template_id=brd.template_id,
                template_version=brd.template_version,
                actor=f"persona:{persona}",
            )
            artifact_id = artifact.get("artifact_id")
        except Exception as exc:  # noqa: BLE001
            log.warning(f"[brd] DB artifact write failed for {workspace_id}: {exc}")

        if not artifact_id:
            return

        # 3. Auto-link to previous stage (creates artifact relationship in fe_artifact_relationships)
        try:
            await self._trace.auto_link_to_previous(
                workspace_id=workspace_id,
                current_artifact_id=artifact_id,
                current_kind="brd",
            )
        except Exception as exc:  # noqa: BLE001
            log.warning(f"[brd] auto_link_to_previous failed for {workspace_id}: {exc}")

        # 3a. DERIVES_FROM link → FSD artifact (BRD was built from the accepted FSD) [LEGACY]
        if context.fsd_ref:
            try:
                await self._trace.add_derives(
                    workspace_id=workspace_id,
                    from_artifact_id=artifact_id,
                    to_artifact_id=context.fsd_ref,
                    stage="BRD",
                    reason="BRD derived from FSD artifact",
                )
            except Exception as exc:  # noqa: BLE001
                log.warning(f"[brd] DERIVES_FROM link failed for {workspace_id}: {exc}")

        # 3b. GROUNDS links per KB card cited (acceptance_criteria rows)
        for row in brd.acceptance_criteria:
            try:
                await self._trace.add_grounds(
                    workspace_id=workspace_id,
                    from_artifact_id=artifact_id,
                    persona=persona,
                    artifact_kind="brd",
                    to_kb_card_id=row.kb_id,
                    kb_version=brd.kb_version,
                    source_locus=row.source_locus,
                    stage="BRD",
                    applied_because=row.link_type,
                )
            except Exception as exc:  # noqa: BLE001
                log.warning(f"[brd] GROUNDS link failed for {workspace_id} → {row.kb_id}: {exc}")

        # 4. Save BRD metrics to fe_workspace_analysis table for UI traceability tab
        try:
            dao = WorkspaceAnalysisDAO()

            # Extract systems from BRD (from acceptance criteria and business rules)
            systems = []
            for ac in brd.acceptance_criteria:
                if ac.kb_id and ac.kb_id.startswith("SYS-"):
                    systems.append({
                        "system_id": ac.kb_id,
                        "system_name": ac.title or ac.kb_id,
                        "impact_level": "medium",
                        "components_changed": [],
                        "confidence": 0.9,
                    })

            # Extract scope from BRD
            scope = {
                "in_scope": [
                    {"item_id": f"brd-{i}", "item_description": br.title or br.rule_text[:100], "scope_category": "IN_SCOPE"}
                    for i, br in enumerate(brd.business_rules or [])
                ],
                "out_of_scope": [],
                "deferred": [],
            }

            # Extract metrics
            metrics = {
                "total_artifacts": len(brd.acceptance_criteria or []),
                "total_relationships": len([ac for ac in brd.acceptance_criteria if ac.kb_id]),
                "coverage_pct": (len([ac for ac in brd.acceptance_criteria if ac.kb_id]) / max(len(brd.acceptance_criteria or []), 1)) * 100,
                "orphan_count": len([ac for ac in brd.acceptance_criteria if not ac.kb_id]),
                "systems_affected": len(set(s["system_id"] for s in systems)),
            }

            # Save to database
            if systems:
                await dao.update_systems(workspace_id, systems)
            await dao.update_scope(workspace_id, scope)
            await dao.update_metrics(workspace_id, metrics)

            log.info(f"[brd] Saved BRD data for {workspace_id}: {len(systems)} systems, {len(scope['in_scope'])} business rules")
        except Exception as exc:  # noqa: BLE001
            log.warning(f"[brd] Failed to save BRD data to traceability table for {workspace_id}: {exc}")

    async def _get_fsd_artifact_id(self, workspace_id: str) -> str | None:
        """Retrieve the artifact_id of the most recent FSD artifact (for DERIVES_FROM link)."""
        try:
            artifacts = await self._workspace.list_artifacts(workspace_id)
            fsds = [a for a in artifacts if a.get("kind") == "fsd"]
            return fsds[-1].get("artifact_id") if fsds else None
        except Exception as exc:  # noqa: BLE001
            log.warning(f"[brd] could not retrieve FSD artifact_id for {workspace_id}: {exc}")
            return None
