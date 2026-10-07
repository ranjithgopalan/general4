"""
Constrained answer synthesis (docs/20 §10) — cite-or-abstain LLM phrasing over retrieved cards.

The LLM only *phrases* facts that are already in the retrieved CARDS; it may not add anything else.
Every claim must carry its card id inline (``[BR-JAUTO-002]``); if the cards don't answer the
question it must emit the exact ``NO_ANSWER`` sentinel. Temperature is 0 (deterministic) and the
model id resolves from the registry (``synthesize`` → sonnet) — never hardcoded. The output is then
handed to the grounding gate (``grounding_gate``), which is the actual enforcement — this prompt is
the first line, the gate is the guarantee.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from functools import lru_cache
from typing import Any

from app.config.settings import Settings, get_settings
from app.models.kb import KbCitation

NO_ANSWER = "NO_ANSWER: not covered by the knowledge base."

_SYSTEM = (
    "You are a grounded assistant for the {gear} knowledge base. Answer ONLY using the provided CARDS.\n"
    "- Use no fact that is not stated in the CARDS. Never guess or use outside knowledge.\n"
    "- Cite every claim inline with its card id in square brackets, e.g. [BR-JAUTO-002].\n"
    "- If the CARDS do not answer the question, reply with EXACTLY: {no_answer}\n"
    "- Be THOROUGH and DETAILED: use ALL relevant CARDS, don't omit anything the CARDS cover.\n"
    "- Structure the answer in Markdown: a short overview, then '##' sections, bullet lists, and a\n"
    "  step-by-step flow when the question is about a process/endpoint/end-to-end.\n"
    "- When the CARDS contain code, endpoints, schemas or payloads, include them in fenced code blocks\n"
    "  (```lang) with the language tagged.\n"
    "- Be precise and neutral; answer in the language of the question. Elaborate is better than terse,\n"
    "  but every sentence must stay grounded in the CARDS (cite-or-omit)."
)


def _format_history(history: list[dict[str, Any]]) -> str:
    """Render prior turns as a PRIOR CONVERSATION block prepended to the user message."""
    if not history:
        return ""
    lines: list[str] = []
    for turn in history:
        lines.append(f"User: {turn.get('query', '')}")
        lines.append(f"Assistant: {turn.get('answer', '')}")
    return "PRIOR CONVERSATION:\n" + "\n".join(lines) + "\n\n"


class Synthesizer:
    """Turn retrieved cards into a cited, grounded answer via the routed chat model."""

    def __init__(self, router: Any | None = None, settings: Settings | None = None) -> None:
        self._router = router
        self._settings = settings or get_settings()

    def _get_router(self) -> Any:
        if self._router is None:
            from app.services.model_router import get_model_router

            self._router = get_model_router()
        return self._router

    def _build_messages(
        self,
        *,
        question: str,
        citations: list[KbCitation],
        cards: dict[str, dict[str, Any]],
        history: list[dict[str, Any]] | None,
        disputed_ids: list[str] | None,
    ) -> list[tuple[str, str]]:
        """Build the (system, human) message pair — shared by buffered + streaming synthesis."""
        system = _SYSTEM.format(gear=self._settings.GEAR_ID, no_answer=NO_ANSWER)
        history_block = _format_history(history) if history else ""
        user = f"{history_block}QUESTION: {question}\n\nCARDS:\n{self._context(citations, cards)}"
        if disputed_ids:
            user += (
                f"\n\nNOTE: The following cards have DISPUTED status (conflicting sources, SME "
                f"resolution pending): {', '.join(disputed_ids)}. Flag this conflict in your answer."
            )
        return [("system", system), ("human", user)]

    async def synthesize(
        self,
        *,
        question: str,
        persona: str,
        citations: list[KbCitation],
        cards: dict[str, dict[str, Any]],
        history: list[dict[str, Any]] | None = None,
        disputed_ids: list[str] | None = None,  # Gap 5: signal the model to flag conflicting cards
        task: str = "synthesize",  # Task name for token-limit routing
    ) -> str:
        """Compose a cited answer; returns the ``NO_ANSWER`` sentinel if the model abstains."""
        llm = self._get_router().get_llm(task)  # Use task-specific token limits
        messages = self._build_messages(
            question=question, citations=citations, cards=cards, history=history, disputed_ids=disputed_ids,
        )
        response = await llm.ainvoke(messages)
        return _content_text(response)

    async def synthesize_stream(
        self,
        *,
        question: str,
        persona: str,
        citations: list[KbCitation],
        cards: dict[str, dict[str, Any]],
        history: list[dict[str, Any]] | None = None,
        disputed_ids: list[str] | None = None,
        task: str = "synthesize",  # Task name for token-limit routing
    ) -> AsyncIterator[str]:
        """Stream the cited answer token-by-token via the model's ``astream`` (real word-by-word).

        Yields incremental text deltas. The caller accumulates them into the full answer and then
        runs the grounding gate on the accumulated text (streaming is presentation; the gate is the
        guarantee — a post-stream ABSTAIN replaces the drafted text with the caveat)."""
        llm = self._get_router().get_llm(task)  # Use task-specific token limits
        messages = self._build_messages(
            question=question, citations=citations, cards=cards, history=history, disputed_ids=disputed_ids,
        )
        async for chunk in llm.astream(messages):
            # NOTE: use the RAW chunk text — never _content_text() here. That helper .strip()s, which
            # on a per-delta stream eats the leading space/newline of each chunk and glues words +
            # markdown lines together ("flowspansAngularUI"). Preserve whitespace verbatim.
            delta = _chunk_text(chunk)
            if delta:
                yield delta

    @staticmethod
    def _context(citations: list[KbCitation], cards: dict[str, dict[str, Any]]) -> str:
        """Render one grounding block per cited card (id · kind · label + body)."""
        blocks: list[str] = []
        for citation in citations:
            card = cards.get(citation.id, {})
            body = card.get("prose") or card.get("text_en") or citation.label or ""
            blocks.append(f"[{citation.id}] ({citation.kind} · {citation.label})\n{body}")
        return "\n\n".join(blocks)


def _chunk_text(chunk: Any) -> str:
    """Raw text of a streaming delta — NO strip, NO block-join spaces (preserve exact whitespace).

    Unlike :func:`_content_text` (used on a whole buffered response), a per-chunk stream must keep the
    leading/trailing spaces and newlines of each delta, or words and markdown lines glue together."""
    content = getattr(chunk, "content", "")
    if isinstance(content, list):
        return "".join(
            str(b.get("text", "")) if isinstance(b, dict) else str(b) for b in content
        )
    return content or ""


def _content_text(response: Any) -> str:
    """Normalize a chat-model response to plain text.

    Claude (via Bedrock) returns ``response.content`` as a LIST of content blocks
    (``[{"type": "text", "text": "…"}, …]``), not a string — so ``(content or "").strip()`` used to
    raise ``'list' object has no attribute 'strip'`` and every synthesis silently fell back to the
    deterministic stub. Join the text blocks when content is a list; strip when it is already a string.
    """
    content = getattr(response, "content", "")
    if isinstance(content, list):
        return " ".join(
            str(b.get("text", "")) if isinstance(b, dict) else str(b) for b in content
        ).strip()
    return (content or "").strip()


def is_no_answer(text: str) -> bool:
    """True if the model emitted the abstain sentinel."""
    return (text or "").strip().upper().startswith("NO_ANSWER")


@lru_cache
def get_synthesizer() -> Synthesizer:
    """Process-wide singleton Synthesizer (cached)."""
    return Synthesizer()
