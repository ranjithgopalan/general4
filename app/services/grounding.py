"""FE grounding spine (docs/04 §7) — the forward moat over generated artifacts.

Verifies that every KB id cited in a generated artifact **resolves to a card kb.query actually
retrieved** — catching fabricated / hallucinated citations before the artifact is accepted
(cite-or-abstain on the forward side). Deterministic; no LLM. char-span ``re_anchor`` against the
source is the seam for the real KB; against the fixture we enforce citation **resolution** (no
fabricated ids) + coverage. A ``BLOCK`` verdict is HIGH severity — the caller must not ship the artifact.
"""

from __future__ import annotations

import re

from app.models.kb import GroundingReport, KbCitation

_ID_PREFIXES = (
    "ENT",
    "REL",
    "PROC",
    "WF",
    "SEQ",
    "STM",
    "SCR",
    "BR",
    "FR",
    "SYS",
    "INT",
    "ROLE",
    "TERM",
    "DOM",
    "ALIAS",
    "EXT",
    "API",
    "CMP",
    "SRV",  # component ids as the RED JSON numbers them (SRV-<APP>-NNN)
)
_ID_RE = re.compile(r"\b(?:" + "|".join(_ID_PREFIXES) + r")-[A-Za-z0-9][A-Za-z0-9-]*")


class GroundingSpine:
    """Deterministic citation-resolution gate over generated artifact content."""

    def verify(self, content: str, citations: list[KbCitation]) -> GroundingReport:
        allowed = {c.id for c in citations}
        cited = list(dict.fromkeys(_ID_RE.findall(content or "")))  # ordered, de-duped
        resolved = [i for i in cited if i in allowed]
        unresolved = [i for i in cited if i not in allowed]
        coverage = round(len(resolved) / len(cited), 3) if cited else 1.0
        verdict = "BLOCK" if unresolved else "PASS"
        return GroundingReport(
            verdict=verdict, cited_ids=cited, resolved=resolved, unresolved=unresolved, coverage=coverage
        )
