"""
Follow-up query resolver (docs/20 §12) — rewrite an elliptical follow-up into a standalone question.

Uses the session's recent turns (``fe_chat_history``) + one LLM call (``classify`` tier = Sonnet) to
resolve pronouns / ellipsis, so a follow-up like "what about AUW?" is retrieved *and* answered in
context. Safe by construction: it only rewrites the **query** (never adds facts), and the rewritten
query still passes the grounding gate. Degrades to the original question when disabled, when there is
no session or history, or on any error.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from app.config.settings import Settings, get_settings
from app.dao import chat_history_dao
from app.utils.logging import log

_SYSTEM = (
    "You rewrite a user's follow-up message into a single standalone question using ONLY the prior "
    "conversation. Resolve pronouns and ellipsis by substituting the specific subject from the "
    "conversation. Do NOT expand acronyms, add definitions or parentheticals, or introduce any term "
    "not already present in the conversation — preserve the user's exact wording otherwise. If the "
    "message is already standalone, return it unchanged. Do NOT answer it. Output ONLY the rewritten "
    "question."
)


def _pool_or_none():
    """The Postgres pool if initialized, else None (no history → no resolution)."""
    try:
        from app.dao.postgres import get_pool

        return get_pool()
    except Exception:
        return None


class FollowupResolver:
    """Rewrite an elliptical follow-up into a standalone query using the session's recent turns."""

    def __init__(self, router: Any | None = None, settings: Settings | None = None) -> None:
        self._router = router
        self._settings = settings or get_settings()

    def _get_router(self) -> Any:
        if self._router is None:
            from app.services.model_router import get_model_router

            self._router = get_model_router()
        return self._router

    async def resolve(self, *, question: str, session_id: str | None, user_id: str | None) -> str:
        """Return a standalone question (rewritten if it's an in-context follow-up), else the original.

        History is scoped to ``user_id`` so a follow-up only ever resolves against the caller's own turns.
        """
        if not self._settings.FOLLOWUP_RESOLVER_ENABLED or not session_id:
            return question
        pool = _pool_or_none()
        if pool is None:
            return question
        try:
            history = await chat_history_dao.recent_turns(
                pool, session_id, self._settings.CHAT_HISTORY_MAX_TURNS, user_id
            )
        except Exception:  # noqa: BLE001
            return question
        if not history:
            return question  # first turn — nothing to resolve against
        return await self._rewrite(question, history)

    async def _rewrite(self, question: str, history: list[dict[str, Any]]) -> str:
        try:
            llm = self._get_router().get_llm("classify")
            resp = await llm.ainvoke([("system", _SYSTEM), ("human", self._prompt(question, history))])
            rewritten = (getattr(resp, "content", "") or "").strip()
            return rewritten or question
        except Exception as exc:  # noqa: BLE001 — never fail the turn on a resolver error
            log.info(f"[followup] resolve failed ({exc}); using the original question")
            return question

    @staticmethod
    def _prompt(question: str, history: list[dict[str, Any]]) -> str:
        turns = "\n".join(
            f"User: {t.get('query', '')}\nAssistant: {(t.get('answer') or '')[:400]}" for t in history
        )
        return f"Conversation so far:\n{turns}\n\nFollow-up message: {question}\n\nStandalone question:"


@lru_cache
def get_followup_resolver() -> FollowupResolver:
    """Process-wide singleton FollowupResolver (cached)."""
    return FollowupResolver()
