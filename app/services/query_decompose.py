"""Compound-question decomposition (chatbot Phase 1) — deterministic, no LLM.

A single user turn often bundles several asks — e.g. *"give me the /calculatepremium flow **with code
snippets** **and explain** the business logic"*. One retrieval with a fixed top_k can't cover all of
them well, so we split the turn into sub-questions, retrieve each, and merge the candidates before a
single grounded synthesis. Deterministic (regex on strong ask-separators) so it's reproducible and
free — the LLM only synthesizes, per the docs/20 deterministic-before-LLM invariant.

Conservative on purpose: it splits ONLY on separators that clearly introduce a NEW ask (``?``, ``;``,
or ``and/then <ask-verb>``), never on a plain ``and`` inside a phrase ("premium and coverage"). When
nothing clearly splits, it returns the original question unchanged.
"""

from __future__ import annotations

import re

# " and/then <ask-verb>" (a second ask), a "?" boundary, or ";" — the only split points.
_ASK_VERB = r"(?:give|show|explain|list|describe|tell|provide|walk|what|how|why|which|where|who|when)"
_SPLIT = re.compile(rf"\?\s+|\s*;\s*|\s+(?:and|then|also)\s+(?=(?:me\s+|us\s+)?{_ASK_VERB}\b)", re.I)

_MAX_SUBQUESTIONS = 4
_MIN_SUB_LEN = 8  # chars — drop fragments too short to retrieve on


def decompose(question: str) -> list[str]:
    """Split *question* into sub-questions, or return ``[question]`` when it isn't compound.

    Never returns an empty list. Deduplicates, preserves order, caps at ``_MAX_SUBQUESTIONS``."""
    q = (question or "").strip()
    if not q:
        return [q]
    parts = [p.strip(" \t.-") for p in _SPLIT.split(q) if p and p.strip(" \t.-")]
    subs = [p for p in parts if len(p) >= _MIN_SUB_LEN]
    if len(subs) < 2:
        return [q]  # not meaningfully compound → retrieve on the whole question
    seen: set[str] = set()
    out: list[str] = []
    for s in subs:
        key = s.lower()
        if key not in seen:
            seen.add(key)
            out.append(s)
    return out[:_MAX_SUBQUESTIONS] or [q]
