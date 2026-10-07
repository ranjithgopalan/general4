"""
Serve-time grounding gate (docs/20 §10) — the anti-hallucination moat over a synthesized answer.

Extends the build-time ``re_anchor`` discipline to answer time. Deterministic, no LLM. Three checks:

  1. **Citation resolution** — every ``[ID]`` in the answer must be a card the retriever actually
     returned; a fabricated id is a hard ``BLOCK``.
  2. **re_anchor grounding** — each cited card must carry a verdict that is not ``FABRICATED`` (and,
     in strict mode, at least one ``VERIFIED``). Cards that fail are dropped; if none survive the
     answer ``ABSTAIN``s.
  3. **Numeric fidelity** (UW/s3 fold-in) — numbers in the answer must appear in the cited sources,
     catching a transposed value inside an otherwise-grounded sentence. Hard ``BLOCK`` in strict
     mode, else a flag that downgrades confidence.

The gate never fabricates or edits facts — it only certifies, drops, or abstains. Strictness is
config-driven (``GROUNDING_REQUIRE_VERIFIED`` / ``GROUNDING_STRICT_NUMERIC``) so it can run lenient
over seed data and strict once the build-time re_anchor has stamped verdicts.
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Any

from app.config.settings import Settings, get_settings
from app.models.kb import AnswerGrounding, KbCitation

_ID_PREFIXES = (
    "ENT", "REL", "PROC", "WF", "SEQ", "STM", "SCR", "BR", "FR",
    "SYS", "INT", "ROLE", "TERM", "DOM", "ALIAS", "EXT", "API", "CMP",
    "SRV",  # component ids as the RED JSON numbers them (SRV-<APP>-NNN)
)  # fmt: skip
_ID_RE = re.compile(r"\b(?:" + "|".join(_ID_PREFIXES) + r")-[A-Za-z0-9][A-Za-z0-9-]*")
# Bracket-enclosed citations only: [ID] — the convention the cite-or-abstain system prompt enforces.
# Non-capturing group for the prefix alternation so findall() returns the full ID, not just the prefix.
_ID_CITE_RE = re.compile(r"\[((?:" + "|".join(_ID_PREFIXES) + r")-[A-Za-z0-9][A-Za-z0-9-]*)\]")
_NUM_RE = re.compile(r"\d[\d,]*(?:\.\d+)?%?")


def harvest_kb_ids(text: str) -> list[str]:
    """Ordered, de-duped KB IDs found anywhere in *text* (bare-ID scan).

    Use this to extract explicit KB references from user-authored requirement text,
    not from LLM answers (use ``_harvest_ids`` for those, which is bracket-aware).
    """
    return list(dict.fromkeys(_ID_RE.findall(text or "")))


def _harvest_ids(text: str) -> list[str]:
    """Ordered, de-duped KB IDs *cited* in an LLM answer (bracket-format citations only).

    The cite-or-abstain system prompt instructs the LLM to write citations as ``[ID]``
    (square brackets).  IDs that appear *bare* — without brackets — are gap / NO_ANSWER
    statements, not positive citations, and must not be counted as fabricated references.

    Strategy:
    - Primary: harvest ``[ID]``-bracketed tokens (real in-text citations).
    - Fallback to bare-ID scan ONLY when the answer contains no bracketed citations at all
      (stub / fixture answers that never use bracket notation).
    """
    bracketed = list(dict.fromkeys(_ID_CITE_RE.findall(text or "")))
    if bracketed:
        return bracketed
    # Fallback: stub/fixture answers that use bare IDs throughout (not gap statements).
    return list(dict.fromkeys(_ID_RE.findall(text or "")))


def _numbers(text: str) -> set[str]:
    """Normalized numeric tokens (commas stripped) in a text."""
    return {m.replace(",", "") for m in _NUM_RE.findall(text or "")}


def _source_text(citations: list[KbCitation], evidence_by_card: dict[str, list[dict[str, Any]]]) -> str:
    """Concatenated grounding surface — cited card labels + their evidence snippets."""
    parts = [c.label or "" for c in citations]
    for evs in evidence_by_card.values():
        parts.extend((e.get("snippet") or "") for e in evs)
    return " ".join(parts)


def _card_grounded(evs: list[dict[str, Any]], require_verified: bool) -> bool:
    """A cited card is grounded unless FABRICATED (lenient); strict also requires a VERIFIED verdict."""
    verdicts = {e.get("anchor_verdict") for e in evs}
    if "FABRICATED" in verdicts:
        return False
    if not require_verified:
        return True
    return (not evs) or ("VERIFIED" in verdicts)


class GroundingGate:
    """Deterministic serve-time grounding certification over a synthesized answer."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    def check(
        self,
        answer: str,
        citations: list[KbCitation],
        evidence_by_card: dict[str, list[dict[str, Any]]] | None = None,
    ) -> AnswerGrounding:
        """Certify a synthesized answer against its citations + their re_anchor evidence."""
        evidence_by_card = evidence_by_card or {}
        require_verified = self._settings.GROUNDING_REQUIRE_VERIFIED
        cited = _harvest_ids(answer)
        allowed = {c.id for c in citations}
        unresolved = [i for i in cited if i not in allowed]
        resolved = [i for i in cited if i in allowed]
        unverified = [i for i in resolved if not _card_grounded(evidence_by_card.get(i, []), require_verified)]
        grounded_ids = [i for i in resolved if i not in unverified]
        # Strip id tokens before number extraction so "BR-JAUTO-002" doesn't leak "002" as a claim.
        answer_numbers = _numbers(_ID_RE.sub(" ", answer))
        ungrounded_numbers = sorted(answer_numbers - _numbers(_source_text(citations, evidence_by_card)))

        verdict = self._verdict(cited, unresolved, grounded_ids, ungrounded_numbers)
        coverage = round(len(resolved) / len(cited), 3) if cited else 1.0
        return AnswerGrounding(
            verdict=verdict,
            grounded=verdict == "PASS",
            cited_ids=cited,
            resolved_ids=resolved,
            unresolved_ids=unresolved,
            unverified_ids=unverified,
            ungrounded_numbers=ungrounded_numbers,
            coverage=coverage,
        )

    def _verdict(
        self, cited: list[str], unresolved: list[str], grounded_ids: list[str], ungrounded_numbers: list[str]
    ) -> str:
        if unresolved:
            return "BLOCK"  # fabricated citation
        if cited and not grounded_ids:
            return "ABSTAIN"  # every cited card failed re_anchor
        if ungrounded_numbers and self._settings.GROUNDING_STRICT_NUMERIC:
            return "BLOCK"  # transposed / invented number
        return "PASS"

    async def verify(self, *, answer: str, citations: list[KbCitation], kb_version: str) -> AnswerGrounding:
        """Fetch each cited card's re_anchor evidence from the DB, then certify (serve path)."""
        evidence_by_card: dict[str, list[dict[str, Any]]] = {}
        pool = _pool_or_none()
        if pool is not None:
            from app.dao import graph_dao

            for citation in citations:
                try:
                    evidence_by_card[citation.id] = await graph_dao.fetch_evidence(pool, kb_version, citation.id)
                except Exception:  # noqa: BLE001 — missing evidence is handled by the gate itself
                    evidence_by_card[citation.id] = []
        return self.check(answer, citations, evidence_by_card)


def _pool_or_none():
    """The Postgres pool if initialized, else None."""
    try:
        from app.dao.postgres import get_pool

        return get_pool()
    except Exception:
        return None


@lru_cache
def get_grounding_gate() -> GroundingGate:
    """Process-wide singleton GroundingGate (cached)."""
    return GroundingGate()
