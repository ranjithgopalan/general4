"""
Chatbot orchestration (docs/20 §4) — guardrails → intake → query-PII-mask → kb.query → session.

A thin async pipeline over the tested spine. No LangGraph is needed for the deterministic path; the
LangGraph StateGraph + durable checkpointer are the deferred seam (blocked on the DBA-pending
checkpointer tables, docs/09 §3.7). Session persistence writes ``fe_chat_history`` best-effort. Every
answerable turn still passes through kb.query's grounding gate — cite-or-abstain holds.

Session naming: the first turn of a new session sets ``session_name`` to the first 50 characters of
the user query (trimmed). Subsequent turns leave it NULL so the name is never overwritten. The sessions
list endpoint surfaces it for the UI to display as the conversation title.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from functools import lru_cache

from app.config.settings import Settings, get_settings
from app.dao import chat_history_dao
from app.models.kb import KbAnswer
from app.services.followup_resolver import FollowupResolver, get_followup_resolver
from app.services.guardrails import Guardrails, get_guardrails
from app.services.intake import IntakeClassifier, get_intake_classifier
from app.services.kb_query import KbQueryService
from app.services.personas import get_persona_registry
from app.services.pii_mask import PiiMasker, get_pii_masker
from app.utils.logging import log

_SESSION_NAME_MAX = 50

# docs/20 §CLARIFY — shown when intake flags a question too vague to retrieve on (no abstain, no guess).
_CLARIFY_MSG = (
    "Could you be a bit more specific? Name the system, screen, process, workflow, endpoint, rule, "
    "or entity you're asking about — then I can give a grounded, detailed answer with citations."
)


def _pool_or_none():
    """The Postgres pool if initialized, else None (session persistence is then a no-op)."""
    try:
        from app.dao.postgres import get_pool

        return get_pool()
    except Exception:
        return None


def _truncate(text: str) -> str:
    """First 50 chars of text, stripped — used as the session name on turn 1."""
    return text.strip()[:_SESSION_NAME_MAX]


class ChatService:
    """Orchestrates the chatbot turn: screen → route → mask → ground → persist."""

    def __init__(
        self,
        kb: KbQueryService | None = None,
        guardrails: Guardrails | None = None,
        masker: PiiMasker | None = None,
        intake: IntakeClassifier | None = None,
        resolver: FollowupResolver | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._kb = kb or KbQueryService()
        self._guardrails = guardrails or get_guardrails()
        self._masker = masker or get_pii_masker()
        self._intake = intake or get_intake_classifier()
        self._resolver = resolver or get_followup_resolver()
        self._settings = settings or get_settings()

    async def answer(
        self,
        *,
        question: str,
        persona: str,
        session_id: str | None = None,
        user_id: str | None = None,
        category: str | None = None,
        top_k: int | None = None,
        hybrid: bool | None = None,
    ) -> KbAnswer:
        get_persona_registry().get(persona)  # 404 for an unknown persona, up front

        early, intake = await self._screen_and_route(question, persona)
        if early is not None:  # guardrail block · DIRECT (greeting/meta/OOS) · needs_clarify
            await self._save_if_session(session_id, user_id, persona, question, early)
            return early

        log.info(f"[chat] turn started (session={'set' if session_id else 'None'} persona={persona})")
        # Follow-up: rewrite an elliptical question into a standalone query (retrieval-side) using history.
        resolved = await self._resolver.resolve(question=question, session_id=session_id, user_id=user_id)
        masked, _found = self._masker.mask(resolved)  # mask PII before the LLM/log

        history: list[dict] | None = None
        if session_id:
            pool = _pool_or_none()
            if pool is not None:
                try:
                    history = await chat_history_dao.recent_turns(
                        pool, session_id, self._settings.CHAT_HISTORY_MAX_TURNS, user_id
                    )
                except Exception as exc:  # noqa: BLE001 — history fetch never blocks the answer
                    log.info(f"[chat] history fetch skipped ({exc})")

        kb_kwargs: dict = {
            "question": masked, "persona": persona,
            "top_k": top_k or self._settings.CHAT_RETRIEVAL_TOP_K, "category": category,
            "hybrid": hybrid, "all_kinds": self._settings.CHAT_RETRIEVAL_ALL_KINDS,
            "domains": intake.domains,  # intent-aware retrieval boost
        }
        if history:
            kb_kwargs["history"] = history
        answer = await self._kb.query(**kb_kwargs)
        if answer.abstained:
            log.info(f"[chat] answer abstained (source={answer.source} kb_version={answer.kb_version})")
        if session_id:
            # history is [] on the first turn (recent_turns returned nothing), None when fetch failed.
            # Treat both as "we don't know" — None is safe to name (is_first_turn=True) because
            # if the fetch failed, we still want to attempt setting the name on this turn.
            is_first = not history  # [] → True (first turn); [..] → False; None → True (safe)
            await self._save(
                session_id=session_id, user_id=user_id, persona=persona,
                question=question, answer=answer, is_first_turn=is_first,
            )
        return answer

    async def answer_stream(
        self,
        *,
        question: str,
        persona: str,
        session_id: str | None = None,
        user_id: str | None = None,
        category: str | None = None,
        top_k: int | None = None,
        hybrid: bool | None = None,
    ) -> AsyncIterator[tuple[str, object]]:
        """Streaming twin of :meth:`answer` — same screen→intake→resolve→ground→persist spine, but
        yields live events for the chatbot SSE: ``("stage", {...})`` · ``("token", str)`` ·
        ``("answer", KbAnswer)``. Persists the final turn after the stream completes."""
        get_persona_registry().get(persona)  # 404 for an unknown persona, up front

        early, intake = await self._screen_and_route(question, persona)
        if early is not None:  # guardrail block · DIRECT · needs_clarify — stream the reply, no retrieval
            for word in (early.answer or "").split():  # word-by-word for a consistent UX
                yield ("token", word + " ")
            await self._save_if_session(session_id, user_id, persona, question, early)
            yield ("answer", early)
            return

        # Follow-up: rewrite an elliptical question into a standalone query using history.
        resolved = await self._resolver.resolve(question=question, session_id=session_id, user_id=user_id)
        masked, _found = self._masker.mask(resolved)  # mask PII before the LLM/log

        history: list[dict] | None = None
        if session_id:
            pool = _pool_or_none()
            if pool is not None:
                try:
                    history = await chat_history_dao.recent_turns(
                        pool, session_id, self._settings.CHAT_HISTORY_MAX_TURNS, user_id
                    )
                except Exception as exc:  # noqa: BLE001 — history fetch never blocks the answer
                    log.info(f"[chat] history fetch skipped ({exc})")

        kb_kwargs: dict = {
            "question": masked, "persona": persona,
            "top_k": top_k or self._settings.CHAT_RETRIEVAL_TOP_K, "category": category,
            "hybrid": hybrid, "all_kinds": self._settings.CHAT_RETRIEVAL_ALL_KINDS,
            "domains": intake.domains,  # intent-aware retrieval boost
        }
        if history:
            kb_kwargs["history"] = history

        # First trace step — what the router understood (intent domains) before it searches.
        doms = ", ".join(intake.domains) if intake.domains else "general"
        yield ("stage", {"name": "understand", "detail": f"focus: {doms}"})

        final: KbAnswer | None = None
        async for kind, payload in self._kb.query_stream(**kb_kwargs):
            if kind == "answer":
                final = payload  # type: ignore[assignment]
            yield (kind, payload)  # forward stage · token · answer to the SSE layer

        if final is not None and session_id:
            # [] → first turn; None → fetch failed (safe to name); [..] → not first.
            await self._save(
                session_id=session_id, user_id=user_id, persona=persona,
                question=question, answer=final, is_first_turn=not history,
            )

    async def _save_if_session(
        self, session_id: str | None, user_id: str | None, persona: str,
        question: str, answer: KbAnswer,
    ) -> None:
        """Persist an early-return turn (guardrail / DIRECT) — checks first-turn via the DB."""
        if not session_id:
            return
        is_first = await self._is_first_turn(session_id, user_id)
        await self._save(
            session_id=session_id, user_id=user_id, persona=persona,
            question=question, answer=answer, is_first_turn=is_first,
        )

    def _direct_or_clarify(self, intake, question: str, persona: str) -> KbAnswer | None:
        """Early no-retrieval reply: DIRECT (greeting/meta/out_of_scope) or needs_clarify — else None."""
        if intake.route == "DIRECT":
            return KbAnswer(question=question, persona=persona, answer=intake.direct_message or "")
        if intake.intent == "needs_clarify":  # (4) too vague — ask, don't guess or abstain
            return KbAnswer(question=question, persona=persona, answer=_CLARIFY_MSG)
        return None

    async def _screen_and_route(self, question: str, persona: str):
        """Guardrail screen + intake classify. Returns ``(early_answer | None, intake | None)``:
        a non-None early answer short-circuits retrieval (blocked / DIRECT / clarify)."""
        verdict = self._guardrails.screen(question)
        if not verdict.allowed:
            log.info(f"[chat] guardrail blocked ({verdict.category})")
            return KbAnswer(question=question, persona=persona, answer=verdict.reason, abstained=True), None
        intake = await self._intake.classify_async(question)
        return self._direct_or_clarify(intake, question, persona), intake

    async def _is_first_turn(self, session_id: str, user_id: str | None) -> bool:
        """Check the DB to see if any turns exist for this session (used on early-return paths)."""
        pool = _pool_or_none()
        if pool is None:
            return True  # assume first turn when pool is unavailable
        try:
            return not await chat_history_dao.has_turns(pool, session_id, user_id)
        except Exception:  # noqa: BLE001
            return True  # safe default: set session_name (idempotent if name already set)

    async def _save(
        self,
        *,
        session_id: str,
        user_id: str | None,
        persona: str,
        question: str,
        answer: KbAnswer,
        is_first_turn: bool = False,
    ) -> None:
        pool = _pool_or_none()
        if pool is None:
            log.warning("[chat] session save skipped — Postgres pool not available")
            return
        session_name = _truncate(question) if is_first_turn else None
        try:
            await chat_history_dao.save_turn(
                pool,
                session_id=session_id,
                user_id=user_id,
                persona=persona,
                query=question,
                answer=answer.answer,
                citations=[c.model_dump() for c in answer.citations],
                confidence=answer.confidence,
                kb_version=answer.kb_version,
                session_name=session_name,
            )
            log.info(
                f"[chat] session saved (session={session_id} persona={persona}"
                + (f" name={session_name!r}" if session_name else "")
                + ")"
            )
        except Exception as exc:  # noqa: BLE001 — session logging never breaks the answer
            log.warning(f"[chat] session save failed (session={session_id}): {exc}")


@lru_cache
def get_chat_service() -> ChatService:
    """Process-wide singleton ChatService (cached)."""
    return ChatService()
