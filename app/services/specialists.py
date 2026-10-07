"""Persona specialists — grounded stage workers driven by the conductor (docs/04 §2.1a/§6, docs/19).

ONE config-driven runner serves every persona (they differ by ``includeKinds`` / ``capabilities`` /
model-tier in ``personas.json``, not by bespoke code). For a stage it: enforces the **capability guard**
-> runs **kb.query** (persona-filtered) -> produces the stage artifact -> emits **GROUNDS** traceability
for each cited card. The conductor advances the workspace state around it.

LLM seam: at runtime, with a Bedrock chat model, the artifact CONTENT is synthesized by an **open ReAct
agent** (``build_react_agent`` over the kb.query + fe.generate tools) and verified by the grounding
spine. With no model (dev / test / no-Bedrock) the specialist writes a deterministic, cited stub — but
the enforced flow (persona filter · capability guard · cite-or-abstain · GROUNDS links) is identical.
"""

from __future__ import annotations

from typing import Any

from app.models.kb import KbAnswer, KbCitation
from app.services.grounding import GroundingSpine
from app.services.kb_query import KbQueryService
from app.services.personas import PersonaRegistry
from app.services.traceability_service import TraceabilityService
from app.services.workspace_service import WorkspaceService
from app.utils.exceptions import GroundingError
from app.utils.logging import log

# artifact_kind -> the persona capability required to write it (the capability guard).
_STAGE_CAPABILITY = {
    "FSD": "fe.generate.fsd",
    "BRD": "fe.generate.brd",
    "stories": "fe.generate.stories",
    "SRD": "fe.generate.srd",
    "test-plan": "fe.generate.test-plan",
}


def build_react_agent(model: Any, tools: list[Any], prompt: str) -> Any:  # pragma: no cover — needs Bedrock
    """LLM seam: an open ReAct specialist over the kb.query + fe.generate tools (``create_react_agent``).

    Wired at deploy with a tool-calling Bedrock model; not exercised on the fixture / no-Bedrock path.
    The five non-Developer personas use this; the Developer node MAY swap in the SDK booster below.
    """
    from langgraph.prebuilt import create_react_agent

    return create_react_agent(model, tools=tools, prompt=prompt)


def build_developer_agent(tools: list[Any]) -> Any:  # pragma: no cover — needs the Claude Agent SDK runtime
    """Developer autonomy booster (docs/04 §2.1a): the Developer node MAY run the **Claude Agent SDK**
    (``query()`` with Read/Write/Edit/Bash + the MCP tools) for real multi-file code-gen. It stays inside
    the LangGraph conductor — checkpoint at the node boundary, HITL ``interrupt()`` wraps it, it grounds
    via the same tools + ``re_anchor``, and it is semaphore-bounded (Bedrock via ``CLAUDE_CODE_USE_BEDROCK``,
    creds from Vault). **Swappable:** absent the SDK it falls back to the generic ``PersonaSpecialist``.
    """
    from claude_agent_sdk import ClaudeAgentOptions, query  # noqa: F401 — deploy-time; not installed here

    raise NotImplementedError("Claude Agent SDK booster wires in at deploy (claude-agent-sdk runtime)")


class PersonaSpecialist:
    """Config-driven grounded stage worker for any persona (no bespoke per-persona code)."""

    def __init__(
        self,
        *,
        service: WorkspaceService,
        kb_query: KbQueryService,
        trace: TraceabilityService,
        personas: PersonaRegistry,
        model: Any = None,
        spine: GroundingSpine | None = None,
    ) -> None:
        self._svc = service
        self._kb = kb_query
        self._trace = trace
        self._personas = personas
        self._model = model
        self._spine = spine or GroundingSpine()

    async def run_stage(self, workspace_id: str, *, persona: str, stage: str, artifact_kind: str) -> dict[str, Any]:
        """Produce a stage's artifact for its persona — grounded + traced. Does NOT advance the workspace."""
        capability = _STAGE_CAPABILITY.get(artifact_kind)
        if capability is not None:
            self._personas.require_capability(persona, capability)  # capability guard (403 if not allowed)
        ws = await self._svc.get(workspace_id)
        question = ws.requirement_text or f"{artifact_kind} for workspace {workspace_id}"
        answer = await self._kb.query(question=question, persona=persona)
        # ``citations`` is the grounding set: the outer retrieval UNIONed with anything the ReAct
        # agent retrieved via its own kb.query tool (all real retrievals). The spine verifies + we
        # emit GROUNDS against this set; a purely fabricated id stays unresolved and still BLOCKs.
        content, citations = await self._synthesize(persona, artifact_kind, answer, question)
        report = self._spine.verify(content, citations)  # FE grounding spine (docs/04 §7)
        if report.verdict == "BLOCK":
            raise GroundingError(
                f"artifact cites unresolved KB ids: {report.unresolved}",
                {"workspace_id": workspace_id, "stage": stage, "unresolved": report.unresolved},
            )
        artifact = await self._svc.attach_artifact(
            workspace_id,
            kind=artifact_kind,
            content=content,
            grounding_score=report.coverage,
            actor=f"persona:{persona}",
        )
        # GROUNDS links reference the workspace's pinned KB snapshot (a real kb_version) so the
        # FK to kb_cards holds; fall back to the retrieval source only when the workspace is unpinned.
        kb_version = ws.pinned_kb_version or answer.source or "fixture"
        for cite in citations:
            await self._trace.add_grounds(
                workspace_id=workspace_id,
                from_artifact_id=artifact["artifact_id"],
                persona=persona,
                artifact_kind=artifact_kind,
                to_kb_card_id=cite.id,
                kb_version=kb_version,
                source_locus=cite.source_locus,
                stage=stage,
            )
        return {
            "artifact_id": artifact["artifact_id"],
            "matched_ids": [c.id for c in citations],
            "citations": len(citations),
            "abstained": answer.abstained,
            "confidence": answer.confidence,
            "kb_version": kb_version,
        }

    async def _synthesize(
        self, persona: str, artifact_kind: str, answer: KbAnswer, question: str
    ) -> tuple[str, list[KbCitation]]:
        """Artifact content + its grounding citation set. With a Bedrock model an open ReAct agent
        synthesizes the content (grounded via the kb.query tool) and the citation set unions in every
        card the agent retrieved; any failure falls back to the deterministic cited form, so a stage
        never 500s. No model (dev/test) uses the deterministic form. The cited GROUNDS ids are always
        appended so the grounding spine can resolve them."""
        if self._model is not None:
            try:
                return await self._llm_synthesize(persona, artifact_kind, answer, question)
            except Exception as exc:  # pragma: no cover — runtime Bedrock path; never fail the stage
                log.warning(f"[specialist] ReAct synthesis failed ({exc}); using deterministic content.")
        cited = ", ".join(c.id for c in answer.citations) or "(none — abstained)"
        content = (
            f"[{persona}] {artifact_kind} (deterministic, grounded in {len(answer.citations)} card(s)): "
            f"{answer.answer} | GROUNDS: {cited}"
        )
        return content, list(answer.citations)

    async def _llm_synthesize(  # pragma: no cover — requires a live Bedrock model
        self, persona: str, artifact_kind: str, answer: KbAnswer, question: str
    ) -> tuple[str, list[KbCitation]]:
        """Open ReAct agent (``create_react_agent``) over a persona-scoped kb.query tool.

        Every card the agent retrieves through its own kb_query tool is collected into the grounding
        set (unioned with the outer retrieval) so the spine treats agent-retrieved ids as resolved —
        only ids the agent never actually retrieved (fabrications) remain unresolved and BLOCK.
        """
        from langchain_core.tools import tool

        kb, persona_id = self._kb, persona
        collected: dict[str, KbCitation] = {c.id: c for c in answer.citations}

        @tool
        async def kb_query(q: str) -> str:
            """Search the KB for cards relevant to the query; returns cited card ids, kinds and labels."""
            a = await kb.query(question=q, persona=persona_id)
            for c in a.citations:
                collected.setdefault(c.id, c)
            return "\n".join(f"[{c.id}] {c.kind}: {c.label}" for c in a.citations) or "no matching cards"

        prompt = (
            f"You are the {persona} specialist producing a {artifact_kind}. Use the kb_query tool to "
            "retrieve KB cards, answer ONLY from what you retrieve, and cite every claim as [KB-ID]. "
            "If the KB does not cover it, reply 'not found in corpus'."
        )
        agent = build_react_agent(self._model, [kb_query], prompt)
        result = await agent.ainvoke({"messages": [("user", question)]})
        messages = result.get("messages", []) if isinstance(result, dict) else []
        text = getattr(messages[-1], "content", "") if messages else ""
        citations = list(collected.values())
        cited = ", ".join(collected) or "(none)"
        return f"[{persona}] {artifact_kind} (react-agent): {text} | GROUNDS: {cited}", citations
