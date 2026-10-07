"""fe.generate — the single FE artifact-generation dispatcher (docs/11 §2.3).

Routes an ``artifact_type`` to its stage handler, enforces the persona capability, and returns the
uniform ``fe.generate`` contract (``artifact`` + AC-3 ``trace`` + ``grounding_score``). The generated
artifact is persisted by the stage handler (the only FE write path). New stages register here — no
controller change. ``artifact_type="analysis"`` runs the ANALYSIS stage (docs/22).

Capability map reconciles docs/10 (ANALYSIS = ``workspace.analysis``) with docs/11 (``fe.generate.*``).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from app.lifecycle.stages.analysis.handler import ImpactAnalysisService
from app.lifecycle.stages.architecture.handler import ArchitectureService
from app.lifecycle.stages.brd.handler import BRDService
from app.lifecycle.stages.fsd.handler import FSDService
from app.lifecycle.stages.qa.handler import QAService
from app.lifecycle.stages.stories.handler import StoriesService
from app.models.kb import FeGenerateResult, TraceRow
from app.services.personas import PersonaRegistry
from app.utils.exceptions import ValidationError

# artifact_type -> the persona capability required to generate it.
_CAPABILITY_FOR = {
    "analysis": "workspace.analysis",
    "stories": "fe.generate.stories",
    "fsd": "fe.generate.fsd",
    "brd": "fe.generate.brd",
    "srd": "fe.generate.srd",
    "test-plan": "fe.generate.test-plan",
}
# artifact_types wired today (others are declared but not yet implemented — v2 stages).
_IMPLEMENTED = frozenset({"analysis", "fsd", "brd", "stories", "srd", "test-plan"})

# docs/24 §E — PRD is a display/identity alias of the FSD stage (rename-in-place, zero data
# migration): "prd" normalizes to "fsd" everywhere, so it uses fe.generate.fsd + kind="fsd".
_ARTIFACT_ALIAS = {"prd": "fsd"}


def _canonical(artifact_type: str) -> str:
    """Resolve display aliases (e.g. 'prd' → 'fsd') to the canonical stage artifact_type."""
    return _ARTIFACT_ALIAS.get(artifact_type, artifact_type)


class FeGenerateService:
    """Dispatch fe.generate(workspace_id, artifact_type, persona) to the owning stage handler."""

    def __init__(
        self,
        *,
        personas: PersonaRegistry,
        impact: ImpactAnalysisService,
        fsd: FSDService | None = None,
        brd: BRDService | None = None,
        stories: StoriesService | None = None,
        architecture: ArchitectureService | None = None,
        qa: QAService | None = None,
    ) -> None:
        self._personas = personas
        self._impact = impact
        self._fsd = fsd
        self._brd = brd
        self._stories = stories
        self._architecture = architecture
        self._qa = qa

    def check(self, *, artifact_type: str, persona: str) -> None:
        """Validate the artifact_type + persona capability up front (raises 422 / 403 before any work)."""
        artifact_type = _canonical(artifact_type)  # docs/24 §E — 'prd' → 'fsd'
        capability = _CAPABILITY_FOR.get(artifact_type)
        if capability is None:
            raise ValidationError(
                f"unknown artifact_type: {artifact_type!r}",
                {"artifact_type": artifact_type, "allowed": sorted(_CAPABILITY_FOR)},
            )
        self._personas.require_capability(persona, capability)  # 403 if not granted
        if artifact_type not in _IMPLEMENTED:
            raise ValidationError(
                f"artifact_type {artifact_type!r} is not implemented yet (v2 stage)",
                {"artifact_type": artifact_type, "implemented": sorted(_IMPLEMENTED)},
            )

    async def generate(self, workspace_id: str, *, artifact_type: str, persona: str) -> FeGenerateResult:
        """Buffered generate — validate, run the stage, return the fe.generate contract result."""
        self.check(artifact_type=artifact_type, persona=persona)
        artifact_type = _canonical(artifact_type)  # docs/24 §E — 'prd' → 'fsd'
        if artifact_type == "test-plan":
            if self._qa is None:
                raise ValidationError("QA stage is not wired (qa service not injected)", {"artifact_type": "test-plan"})
            return await self._generate_test_plan(workspace_id, persona)
        if artifact_type == "fsd":
            if self._fsd is None:
                raise ValidationError("FSD stage is not wired (fsd service not injected)", {"artifact_type": "fsd"})
            return await self._generate_fsd(workspace_id, persona)
        if artifact_type == "brd":
            if self._brd is None:
                raise ValidationError("BRD stage is not wired (brd service not injected)", {"artifact_type": "brd"})
            return await self._generate_brd(workspace_id, persona)
        if artifact_type == "stories":
            if self._stories is None:
                raise ValidationError("Stories stage is not wired (stories service not injected)", {"artifact_type": "stories"})
            return await self._generate_stories(workspace_id, persona)
        if artifact_type == "srd":
            if self._architecture is None:
                raise ValidationError("Architecture stage is not wired (architecture service not injected)", {"artifact_type": "srd"})
            return await self._generate_srd(workspace_id, persona)
        return await self._generate_analysis(workspace_id, persona)

    async def generate_stream(
        self,
        workspace_id: str,
        *,
        artifact_type: str,
        persona: str,
        requirement_text: str | None = None,
    ) -> AsyncIterator[tuple[str, Any]]:
        """Streaming generate (SSE): yields ('status', {...}) node-progress then ('artifact', FeGenerateResult).

        Assumes ``check()`` already passed (the route validates first so 422/403 are real HTTP errors
        before the stream opens). Stages: ``analysis``, ``fsd``, and ``brd`` stream today; new stages
        plug in here.

        Args:
            requirement_text: Optional override for workspace requirement (used when rerunning analysis).
        """
        artifact_type = _canonical(artifact_type)  # docs/24 §E — 'prd' → 'fsd'
        if artifact_type == "test-plan":
            if self._qa is None:
                raise ValidationError("QA stage is not wired (qa service not injected)", {"artifact_type": "test-plan"})
            async for event, payload in self._qa.generate_stream(workspace_id, persona=persona):
                if event == "status":
                    yield ("status", payload)
                elif event == "result":
                    plan = payload
                    trace = [
                        TraceRow(kb_ids=[tc.source_ref], source_loci=[])
                        for tc in plan.story_test_cases
                        if tc.source_ref
                    ]
                    result = FeGenerateResult(
                        workspace_id=workspace_id,
                        artifact_type="test-plan",
                        artifact=plan.model_dump_json(),
                        trace=trace,
                        grounding_score=plan.grounding_score,
                    )
                    yield ("artifact", result.model_dump())
            return

        if artifact_type == "srd":
            if self._architecture is None:
                raise ValidationError("Architecture stage is not wired (architecture service not injected)", {"artifact_type": "srd"})
            async for event, payload in self._architecture.generate_stream(workspace_id, persona=persona):
                if event == "status":
                    yield ("status", payload)
                elif event == "step":
                    yield ("step", payload)
                elif event == "result":
                    srd = payload
                    trace = [
                        TraceRow(kb_ids=[comp.id], source_loci=[comp.source_locus] if comp.source_locus else [])
                        for comp in srd.component_design
                    ]
                    result = FeGenerateResult(
                        workspace_id=workspace_id,
                        artifact_type="srd",
                        artifact=srd.model_dump_json(),
                        trace=trace,
                        grounding_score=srd.grounding_score,
                    )
                    yield ("artifact", result.model_dump())
            return

        if artifact_type == "stories":
            if self._stories is None:
                raise ValidationError("Stories stage is not wired (stories service not injected)", {"artifact_type": "stories"})
            async for event, payload in self._stories.generate_stream(workspace_id, persona=persona):
                if event == "status":
                    yield ("status", payload)
                elif event == "step":
                    yield ("step", payload)
                elif event == "result":
                    stories_doc = payload
                    trace = [
                        TraceRow(kb_ids=row.kb_ids, source_loci=row.source_loci)
                        for row in stories_doc.traceability
                    ]
                    result = FeGenerateResult(
                        workspace_id=workspace_id,
                        artifact_type="stories",
                        artifact=stories_doc.model_dump_json(),
                        trace=trace,
                        grounding_score=stories_doc.grounding_score,
                    )
                    yield ("artifact", result.model_dump())
            return

        if artifact_type == "brd":
            if self._brd is None:
                raise ValidationError("BRD stage is not wired (brd service not injected)", {"artifact_type": "brd"})
            async for event, payload in self._brd.generate_stream(workspace_id, persona=persona):
                if event == "status":
                    yield ("status", payload)
                elif event == "step":
                    yield ("step", payload)
                elif event == "result":
                    brd = payload
                    trace = [
                        TraceRow(kb_ids=[row.kb_id], source_loci=[row.source_locus] if row.source_locus else [])
                        for row in brd.acceptance_criteria
                    ]
                    result = FeGenerateResult(
                        workspace_id=workspace_id,
                        artifact_type="brd",
                        artifact=brd.model_dump_json(),
                        trace=trace,
                        grounding_score=brd.grounding_score,
                    )
                    yield ("artifact", result.model_dump())
            return

        if artifact_type == "fsd":
            if self._fsd is None:
                raise ValidationError("FSD stage is not wired (fsd service not injected)", {"artifact_type": "fsd"})
            async for event, payload in self._fsd.generate_stream(workspace_id, persona=persona):
                if event == "status":
                    yield ("status", payload)
                elif event == "step":
                    yield ("step", payload)
                elif event == "result":
                    fsd = payload
                    trace = [
                        TraceRow(kb_ids=[row.kb_id], source_loci=[row.source_locus] if row.source_locus else [])
                        for row in fsd.acceptance_criteria
                    ]
                    result = FeGenerateResult(
                        workspace_id=workspace_id,
                        artifact_type="fsd",
                        artifact=fsd.model_dump_json(),
                        trace=trace,
                        grounding_score=fsd.grounding_score,
                    )
                    yield ("artifact", result.model_dump())
            return

        async for event, payload in self._impact.analyze_stream(
            workspace_id, persona=persona, requirement_override=requirement_text
        ):
            if event == "status":
                yield ("status", payload)
            elif event == "step":  # live agent steps (tool calls) — forwarded to the SSE client
                yield ("step", payload)
            elif event == "result":
                analysis = payload
                trace = [
                    TraceRow(kb_ids=[c.id], source_loci=[c.source_locus] if c.source_locus else [])
                    for c in analysis.matched
                ]
                result = FeGenerateResult(
                    workspace_id=workspace_id,
                    artifact_type="analysis",
                    artifact=analysis.model_dump_json(),
                    trace=trace,
                    grounding_score=analysis.grounding_score,
                )
                yield ("artifact", result.model_dump())

    async def _generate_test_plan(self, workspace_id: str, persona: str) -> FeGenerateResult:
        plan = None
        async for event, payload in self._qa.generate_stream(workspace_id, persona=persona):
            if event == "result":
                plan = payload
        if plan is None:
            raise ValidationError(
                "QA test plan generation produced no document — check DevDocument and re-run",
                {"artifact_type": "test-plan", "workspace_id": workspace_id},
            )
        trace = [
            TraceRow(kb_ids=[tc.source_ref], source_loci=[])
            for tc in plan.story_test_cases
            if tc.source_ref
        ]
        return FeGenerateResult(
            workspace_id=workspace_id,
            artifact_type="test-plan",
            artifact=plan.model_dump_json(),
            trace=trace,
            grounding_score=plan.grounding_score,
        )

    async def _generate_analysis(self, workspace_id: str, persona: str) -> FeGenerateResult:
        analysis = await self._impact.analyze(workspace_id, persona=persona)
        trace = [
            TraceRow(kb_ids=[c.id], source_loci=[c.source_locus] if c.source_locus else []) for c in analysis.matched
        ]
        return FeGenerateResult(
            workspace_id=workspace_id,
            artifact_type="analysis",
            artifact=analysis.model_dump_json(),
            trace=trace,
            grounding_score=analysis.grounding_score,
        )

    async def _generate_fsd(self, workspace_id: str, persona: str) -> FeGenerateResult:
        fsd_doc = None
        async for event, payload in self._fsd.generate_stream(workspace_id, persona=persona):
            if event == "result":
                fsd_doc = payload
        if fsd_doc is None:
            raise ValidationError(
                "FSD generation produced no document — check KB retrieval and re-run",
                {"artifact_type": "fsd", "workspace_id": workspace_id},
            )
        trace = [
            TraceRow(kb_ids=[row.kb_id], source_loci=[row.source_locus] if row.source_locus else [])
            for row in fsd_doc.acceptance_criteria
        ]
        return FeGenerateResult(
            workspace_id=workspace_id,
            artifact_type="fsd",
            artifact=fsd_doc.model_dump_json(),
            trace=trace,
            grounding_score=fsd_doc.grounding_score,
        )

    async def _generate_brd(self, workspace_id: str, persona: str) -> FeGenerateResult:
        brd_doc = None
        async for event, payload in self._brd.generate_stream(workspace_id, persona=persona):
            if event == "result":
                brd_doc = payload
        if brd_doc is None:
            raise ValidationError(
                "BRD generation produced no document — check FSD artifact and re-run",
                {"artifact_type": "brd", "workspace_id": workspace_id},
            )
        trace = [
            TraceRow(kb_ids=[row.kb_id], source_loci=[row.source_locus] if row.source_locus else [])
            for row in brd_doc.acceptance_criteria
        ]
        return FeGenerateResult(
            workspace_id=workspace_id,
            artifact_type="brd",
            artifact=brd_doc.model_dump_json(),
            trace=trace,
            grounding_score=brd_doc.grounding_score,
        )

    async def _generate_srd(self, workspace_id: str, persona: str) -> FeGenerateResult:
        srd_doc = None
        async for event, payload in self._architecture.generate_stream(workspace_id, persona=persona):
            if event == "result":
                srd_doc = payload
        if srd_doc is None:
            raise ValidationError(
                "SRD generation produced no document — check Stories artifact and re-run",
                {"artifact_type": "srd", "workspace_id": workspace_id},
            )
        trace = [
            TraceRow(kb_ids=[comp.id], source_loci=[comp.source_locus] if comp.source_locus else [])
            for comp in srd_doc.component_design
        ]
        return FeGenerateResult(
            workspace_id=workspace_id,
            artifact_type="srd",
            artifact=srd_doc.model_dump_json(),
            trace=trace,
            grounding_score=srd_doc.grounding_score,
        )

    async def _generate_stories(self, workspace_id: str, persona: str) -> FeGenerateResult:
        stories_doc = None
        async for event, payload in self._stories.generate_stream(workspace_id, persona=persona):
            if event == "result":
                stories_doc = payload
        if stories_doc is None:
            raise ValidationError(
                "Stories generation produced no document — check BRD artifact and re-run",
                {"artifact_type": "stories", "workspace_id": workspace_id},
            )
        trace = [
            TraceRow(kb_ids=row.kb_ids, source_loci=row.source_loci)
            for row in stories_doc.traceability
        ]
        return FeGenerateResult(
            workspace_id=workspace_id,
            artifact_type="stories",
            artifact=stories_doc.model_dump_json(),
            trace=trace,
            grounding_score=stories_doc.grounding_score,
        )
