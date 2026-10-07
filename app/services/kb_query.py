"""kb.query — persona-filtered grounded retrieval + cite-or-abstain synthesis (docs/20).

Pipeline: persona ``includeKinds`` filter → :class:`HybridRetriever` (graph + dense + BM25 → RRF) →
constrained LLM synthesis (cite-or-abstain) → serve-time ``re_anchor`` grounding gate. In fixture/dev
mode (no ACTIVE KB in the DB) synthesis is a deterministic stub; against the real KB it uses the routed
chat model (Sonnet). The moat holds in every mode: the persona filter, abstain-on-empty, and the
grounding gate (fabricated citation / failed re_anchor → ABSTAIN).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass

from app.models.kb import AnswerGrounding, KbAnswer, KbCitation
from app.services.grounding_gate import GroundingGate, get_grounding_gate
from app.services.personas import PersonaRegistry, get_persona_registry
from app.services.query_decompose import decompose
from app.services.retrieval import Candidate, HybridRetriever, RetrievalResult, get_hybrid_retriever
from app.services.synthesis import Synthesizer, get_synthesizer, is_no_answer
from app.utils.logging import log


@dataclass
class _Prepared:
    """Result of retrieve + persona/SME/DISPUTED guarding — shared by query() and query_stream()."""

    early: KbAnswer | None = None                      # non-None → emit immediately (abstain path)
    candidates: list | None = None                     # safe candidates feeding synthesis
    citations: list[KbCitation] | None = None
    disputed_ids: list[str] | None = None
    result: RetrievalResult | None = None


async def _pinned_version(workspace_id: str | None) -> str | None:
    """Return pinned_kb_version for a workspace, or None (Gap 6). Swallows all errors."""
    if not workspace_id:
        return None
    try:
        from app.dao.postgres import get_pool
        from app.dao import graph_dao
        pool = get_pool()
        return await graph_dao.pinned_version_for_workspace(pool, workspace_id)
    except Exception:  # noqa: BLE001
        return None


_KIND_LABELS = {
    "Component": "code component", "CMP": "code component", "ApiOp": "API endpoint", "API": "API endpoint",
    "System": "system", "SYS": "system", "Integration": "integration", "INT": "integration",
    "Entity": "data entity", "ENT": "data entity", "Screen": "screen", "SCR": "screen",
    "BusinessRule": "business rule", "BR": "business rule", "FunctionalReq": "functional req", "FR": "functional req",
    "Process": "process", "PROC": "process", "Workflow": "workflow", "WF": "workflow",
    "Sequence": "sequence", "SEQ": "sequence", "Role": "role", "Term": "term",
}


def _kind_label(kind: str) -> str:
    """Friendly plural-ish label for a KB kind in the agent trace ('Component' → 'code component')."""
    return _KIND_LABELS.get(kind, kind or "card")


def _stub_answer(question: str, citations: list[KbCitation]) -> str:
    """Deterministic dev/fallback synthesis — names the grounded cards (no LLM)."""
    cited = ", ".join(c.id for c in citations)
    return f"(deterministic synthesis) grounded in: {cited}"


def _confidence(top_score: float, grounding: AnswerGrounding | None = None) -> float:
    """Retrieval-strength heuristic, downgraded when the gate flags ungrounded numbers."""
    base = top_score / (top_score + 2.0)
    if grounding is not None and grounding.ungrounded_numbers:
        base *= 0.5
    return round(min(1.0, base), 3)


class KbQueryService:
    """Grounded retrieval + cite-or-abstain synthesis, scoped to a persona's allowed kinds (docs/20)."""

    def __init__(
        self,
        retriever: HybridRetriever | None = None,
        personas: PersonaRegistry | None = None,
        provider=None,
        synthesizer: Synthesizer | None = None,
        gate: GroundingGate | None = None,
    ) -> None:
        # ``provider=`` is a back-compat convenience (tests/fixtures): wrap it in a HybridRetriever so
        # the graph lane uses the given provider. Prefer passing ``retriever`` explicitly in new code.
        if retriever is None:
            retriever = HybridRetriever(provider=provider) if provider is not None else get_hybrid_retriever()
        self._retriever = retriever
        self._personas = personas or get_persona_registry()
        self._synth = synthesizer or get_synthesizer()
        self._gate = gate or get_grounding_gate()

    async def _retrieve_maybe_multi(self, question: str, retrieve_kwargs: dict) -> RetrievalResult:
        """Compound-question decomposition: split the turn into sub-questions, retrieve each (+ the
        whole question) concurrently, and merge the candidates (dedup by id, keep the best score) so
        one grounded synthesis can cover every part. Single-ask questions take the plain path."""
        subs = decompose(question)
        if len(subs) < 2:
            return await self._retriever.retrieve(**retrieve_kwargs)

        queries = [question, *(s for s in subs if s != question)]

        async def _one(qq: str) -> RetrievalResult:
            kw = {**retrieve_kwargs, "query": qq}
            return await self._retriever.retrieve(**kw)

        results = await asyncio.gather(*(_one(q) for q in queries), return_exceptions=True)
        ok = [r for r in results if isinstance(r, RetrievalResult)]
        if not ok:
            return await self._retriever.retrieve(**retrieve_kwargs)

        best: dict[str, Candidate] = {}
        for r in ok:
            for c in r.candidates:
                if c.id not in best or c.score > best[c.id].score:
                    best[c.id] = c
        top_k = retrieve_kwargs.get("top_k") or len(best)
        merged = sorted(best.values(), key=lambda c: (-c.score, c.id))[: top_k * 2]  # comprehensive, bounded
        log.info(f"[kb.query] compound decomposition: {len(subs)} sub-questions -> {len(merged)} merged candidates")
        return RetrievalResult(candidates=merged, kb_version=ok[0].kb_version, source=ok[0].source)

    async def _retrieve_and_guard(
        self,
        *,
        question: str,
        persona: str,
        top_k: int | None,
        category: str | None,
        hybrid: bool | None,
        workspace_id: str | None,
        all_kinds: bool = False,
        domains: list[str] | None = None,
    ) -> _Prepared:
        """Retrieve + apply the persona/empty/SME/DISPUTED guards (shared by both query paths).

        ``all_kinds=True`` (chatbot assistant) retrieves across every kind so it can answer any
        question; the persona is still validated (for identity/audit) but does not narrow the read."""
        kinds = self._personas.filter_kinds(persona)  # raises for an unknown persona (validation)

        # Gap 6: use workspace-pinned version when available, else fall through to ACTIVE.
        kb_version_override = await _pinned_version(workspace_id)
        retrieve_kwargs: dict = {
            "query": question or "", "persona_kinds": kinds, "all_kinds": all_kinds,
            "category": category, "top_k": top_k, "hybrid": hybrid, "domains": domains,
        }
        if kb_version_override:
            retrieve_kwargs["kb_version_override"] = kb_version_override

        result = await self._retrieve_maybe_multi(question or "", retrieve_kwargs)
        if not result.candidates:
            log.info(f"[kb.query] no candidates retrieved (source={result.source} version={result.kb_version})")
            return _Prepared(early=KbAnswer(
                question=question, persona=persona, abstained=True,
                answer="No matching content was found in the knowledge base for this question.",
                source=result.source, kb_version=result.kb_version,
            ))

        # Gap 4: suppress needs-ja-sme cards — they must not feed synthesis until SME-verified.
        safe = [c for c in result.candidates if c.review_state != "needs-ja-sme"]
        sme_only = not safe and any(c.review_state == "needs-ja-sme" for c in result.candidates)
        if sme_only:
            log.info("[kb.query] only needs-ja-sme candidates — abstaining with SME caveat")
            return _Prepared(early=KbAnswer(
                question=question, persona=persona, abstained=True,
                answer=(
                    "This question relates to Japanese-language source documents pending SME review. "
                    "A verified answer will be available after review is complete."
                ),
                source=result.source, kb_version=result.kb_version,
            ))
        candidates = safe if safe else result.candidates

        # Gap 5: detect DISPUTED cards so the answer can carry a caveat.
        disputed_ids = [c.id for c in candidates if c.review_state == "DISPUTED"]
        citations = [
            KbCitation(id=c.id, kind=c.kind, label=c.label, source_locus=c.source_locus)
            for c in candidates
        ]
        return _Prepared(
            early=None, candidates=candidates, citations=citations,
            disputed_ids=disputed_ids, result=result,
        )

    def _abstain(self, question: str, persona: str, result: RetrievalResult, msg: str) -> KbAnswer:
        return KbAnswer(question=question, persona=persona, abstained=True, answer=msg,
                        source=result.source, kb_version=result.kb_version)

    def _final_answer(self, question: str, persona: str, prep: _Prepared, answer_text: str,
                      grounding: AnswerGrounding) -> KbAnswer:
        return KbAnswer(
            question=question, persona=persona, answer=answer_text, citations=prep.citations,
            confidence=_confidence(prep.candidates[0].score, grounding),
            source=prep.result.source, kb_version=prep.result.kb_version,
            disputed_ids=prep.disputed_ids,  # Gap 5: propagate so SSE/UI can show caveat
        )

    async def query(
        self,
        *,
        question: str,
        persona: str,
        top_k: int | None = None,
        category: str | None = None,
        hybrid: bool | None = None,
        workspace_id: str | None = None,  # Gap 6: resolve pinned KB version for workspace queries
        history: list[dict] | None = None,  # prior turns from fe_chat_history for multi-turn context
        all_kinds: bool = False,  # chatbot assistant: read across all kinds (answer any question)
        domains: list[str] | None = None,  # intake domains → intent-aware retrieval boost
        task: str = "synthesize",  # Task name for token-limit routing (analyze=8k, synthesize=40k)
    ) -> KbAnswer:
        prep = await self._retrieve_and_guard(
            question=question, persona=persona, top_k=top_k, category=category,
            hybrid=hybrid, workspace_id=workspace_id, all_kinds=all_kinds, domains=domains,
        )
        if prep.early is not None:
            return prep.early

        answer_text = await self._compose(
            question=question, persona=persona, citations=prep.citations, result=prep.result,
            disputed_ids=prep.disputed_ids, history=history, task=task,
        )
        if is_no_answer(answer_text):
            return self._abstain(question, persona, prep.result,
                                 "The knowledge base does not contain enough information to answer this question.")

        grounding = await self._gate.verify(
            answer=answer_text, citations=prep.citations, kb_version=prep.result.kb_version)
        if grounding.verdict in ("BLOCK", "ABSTAIN"):
            log.info(f"[kb.query] grounding gate {grounding.verdict} — abstaining "
                     f"(unresolved={grounding.unresolved_ids} unverified={grounding.unverified_ids})")
            return self._abstain(question, persona, prep.result,
                                 "Relevant content was found but could not be verified against the source documents.")

        return self._final_answer(question, persona, prep, answer_text, grounding)

    async def query_stream(
        self,
        *,
        question: str,
        persona: str,
        top_k: int | None = None,
        category: str | None = None,
        hybrid: bool | None = None,
        workspace_id: str | None = None,
        history: list[dict] | None = None,
        all_kinds: bool = False,  # chatbot assistant: read across all kinds (answer any question)
        domains: list[str] | None = None,  # intake domains → intent-aware retrieval boost
        task: str = "synthesize",  # Task name for token-limit routing (analyze=8k, synthesize=40k)
    ) -> AsyncIterator[tuple[str, object]]:
        """Streaming twin of :meth:`query` — yields events for real word-by-word UX (docs/20).

        Event tuples: ``("stage", {name, detail})`` progress · ``("token", str)`` real synthesis
        deltas · ``("answer", KbAnswer)`` the final grounded answer (or abstain). The tokens are the
        drafted answer; the grounding gate runs on the accumulated text before the final ``answer``,
        so a post-stream ABSTAIN cleanly supersedes the draft (moat preserved)."""
        yield ("stage", {"name": "retrieve", "detail": "knowledge graph + vector/keyword search"})
        prep = await self._retrieve_and_guard(
            question=question, persona=persona, top_k=top_k, category=category,
            hybrid=hybrid, workspace_id=workspace_id, all_kinds=all_kinds, domains=domains,
        )
        if prep.early is not None:
            yield ("answer", prep.early)
            return

        # Show WHAT was found (kind breakdown) — Component/Integration/System/ApiOp/… — not just a count.
        counts: dict[str, int] = {}
        for c in prep.candidates:
            counts[c.kind] = counts.get(c.kind, 0) + 1
        breakdown = " · ".join(f"{n} {_kind_label(k)}" for k, n in
                               sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))
        yield ("stage", {"name": "retrieved", "detail": f"{len(prep.candidates)} cards — {breakdown}"})
        yield ("stage", {"name": "rank", "detail": f"fused + reranked to the top {len(prep.candidates)}"})
        yield ("stage", {"name": "synthesize", "detail": "composing a grounded answer"})

        parts: list[str] = []
        async for delta in self._compose_stream(
            question=question, persona=persona, citations=prep.citations, result=prep.result,
            disputed_ids=prep.disputed_ids, history=history, task=task,
        ):
            parts.append(delta)
            yield ("token", delta)
        answer_text = "".join(parts)

        if is_no_answer(answer_text):
            yield ("answer", self._abstain(
                question, persona, prep.result,
                "The knowledge base does not contain enough information to answer this question."))
            return

        yield ("stage", {"name": "ground", "detail": "verifying every citation against source"})
        grounding = await self._gate.verify(
            answer=answer_text, citations=prep.citations, kb_version=prep.result.kb_version)
        if grounding.verdict in ("BLOCK", "ABSTAIN"):
            log.info(f"[kb.query] grounding gate {grounding.verdict} — abstaining (stream)")
            yield ("answer", self._abstain(
                question, persona, prep.result,
                "Relevant content was found but could not be verified against the source documents."))
            return

        yield ("answer", self._final_answer(question, persona, prep, answer_text, grounding))

    async def read_many(self, ids: list[str]) -> dict[str, dict]:
        """Fetch full card info for a known list of IDs (post-retrieval enrichment).

        This is NOT a search — it fetches by stable ID for cards already resolved by ANALYSIS.
        Used by the FSD stage to hydrate section content and the LLM prompt with real card
        data (prose, source_locus, text_en) rather than bare labels.

        Returns ``{id: {prose, text_en, source_locus, kind, label}}``.
        Missing cards are absent from the dict; callers fall back to label on miss.
        Always returns ``{}`` on any error (best-effort — never fails the stage).
        """
        if not ids:
            return {}
        try:
            raw = await self._retriever.content(ids)
            # Pass the full card dict through; callers pick the fields they need.
            return {cid: card for cid, card in raw.items() if card}
        except Exception as exc:  # noqa: BLE001 — never fail the stage on a content fetch error
            log.warning(f"[kb.query] read_many failed for {len(ids)} ids: {exc}")
            return {}

    async def _compose(
        self,
        *,
        question: str,
        persona: str,
        citations: list[KbCitation],
        result: RetrievalResult,
        disputed_ids: list[str],
        history: list[dict] | None = None,
        task: str = "synthesize",  # Task name for token-limit routing
    ) -> str:
        """LLM synthesis against the real KB; deterministic stub in fixture/dev mode or on LLM failure."""
        if result.source != "db":
            return _stub_answer(question, citations)
        try:
            cards = await self._retriever.content([c.id for c in citations])
            synth_kwargs: dict = dict(
                question=question, persona=persona, citations=citations, cards=cards, task=task
            )
            if history:
                synth_kwargs["history"] = history
            # Gap 5: only pass disputed_ids when present to keep the interface backward-compatible
            # with test mocks that don't accept extra kwargs.
            if disputed_ids:
                synth_kwargs["disputed_ids"] = disputed_ids
            text = await self._synth.synthesize(**synth_kwargs)
            return text or _stub_answer(question, citations)
        except Exception as exc:  # noqa: BLE001 — never fail the query on an LLM/synthesis error
            log.info(f"[kb.query] LLM synthesis unavailable ({exc}); deterministic fallback")
            return _stub_answer(question, citations)

    async def _compose_stream(
        self,
        *,
        question: str,
        persona: str,
        citations: list[KbCitation],
        result: RetrievalResult,
        disputed_ids: list[str],
        history: list[dict] | None = None,
        task: str = "synthesize",  # Task name for token-limit routing
    ) -> AsyncIterator[str]:
        """Streaming twin of :meth:`_compose`. Real astream tokens against the DB-backed KB; in
        fixture/dev mode (or on LLM failure) it word-streams the deterministic stub so the UX is
        consistent everywhere."""
        if result.source != "db":
            for word in _stub_answer(question, citations).split():
                yield word + " "
            return
        try:
            cards = await self._retriever.content([c.id for c in citations])
            synth_kwargs: dict = {
                "question": question, "persona": persona, "citations": citations, "cards": cards, "task": task,
            }
            if history:
                synth_kwargs["history"] = history
            if disputed_ids:
                synth_kwargs["disputed_ids"] = disputed_ids
            emitted = False
            async for delta in self._synth.synthesize_stream(**synth_kwargs):
                emitted = True
                yield delta
            if not emitted:  # model produced nothing → deterministic fallback (still streamed)
                for word in _stub_answer(question, citations).split():
                    yield word + " "
        except Exception as exc:  # noqa: BLE001 — never fail the query on an LLM/synthesis error
            log.info(f"[kb.query] streaming synthesis unavailable ({exc}); deterministic fallback")
            for word in _stub_answer(question, citations).split():
                yield word + " "
