"""BRDContext — deterministic context assembled from the completed FSD artifact.

The BRD stage does NOT re-retrieve from the KB from scratch.  It reads the already-grounded
FSDDocument (references, scope, open_items, change_class) and reorganises it by KB kind so the
section builders and the ReAct agent have a structured, ready-to-use context.

Key differences from FSDContext:
  • Input is FSDDocument (not ImpactAnalysis) — BRD is the third stage; FSD is the prerequisite.
  • Filtered kinds: BR / FR / DOM (Term/Domain) / ROLE — narrower than FSD.
  • ``stakeholder_input`` string (optional BA-provided text) is passed through to the agent.
  • ``allowed_ids`` is derived from FSD references — the grounding gate enforces this.
  • card_bodies: same shape as FSDContext (id → {prose, text_en, source_locus, kind, label}).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.lifecycle.stages.analysis.schema import ImpactCitation, ScopeDiff
from app.lifecycle.stages.fsd.schema import FSDDocument

# Per-kind cap for LLM context — BRD is BA-facing; cap keeps prompt focused.
_MAX_PER_KIND: int = 6
_PROSE_SNIPPET_LEN: int = 300

# KB kind filter sets (match Neo4j labels from CLAUDE.md §3).
_BR_KINDS = {"BusinessRule"}
_FR_KINDS = {"FunctionalReq"}
_DOM_KINDS = {"Term", "Domain"}
_ROLE_KINDS = {"Role"}


def _filter(nodes: list[ImpactCitation], kinds: set[str]) -> list[ImpactCitation]:
    return [n for n in nodes if n.kind in kinds]


@dataclass
class BRDContext:
    """Grounded context for the BRD stage — derived from the completed FSD artifact."""

    requirement: str
    kb_version: str
    change_class: str
    fsd_ref: str | None          # artifact_id of the accepted FSD
    analysis_ref: str | None     # transitively from FSD (for full GROUNDS chain)
    scope: ScopeDiff

    # Source of truth for grounding gate — all KB card ids the FSD grounded to.
    matched: list[ImpactCitation]

    # Gaps from FSD open_items (to convert into BA-TODO KeyDecision stubs).
    gaps: list[str]

    # Optional BA-provided text — fed verbatim to the agent's context block.
    stakeholder_input: str | None

    # Kind-filtered subsets for deterministic builders.
    br_matched: list[ImpactCitation] = field(default_factory=list)
    fr_matched: list[ImpactCitation] = field(default_factory=list)
    dom_matched: list[ImpactCitation] = field(default_factory=list)
    role_matched: list[ImpactCitation] = field(default_factory=list)

    # Full card prose — same shape as FSDContext: {id: {prose, text_en, source_locus, kind, label}}.
    # Empty dict in fixture/dev mode; builders fall back to label gracefully.
    card_bodies: dict[str, dict[str, Any]] = field(default_factory=dict)

    @property
    def allowed_ids(self) -> set[str]:
        """Whitelist for the BRD grounding gate — only IDs grounded by the FSD stage."""
        return {c.id for c in self.matched}

    def format_for_llm(self) -> str:
        """Structured context string for the BRD ReAct agent.

        Format: REQUIREMENT → CHANGE CLASSIFICATION → STAKEHOLDER INPUT →
                BUSINESS RULES → FUNCTIONAL REQUIREMENTS → DOMAIN TERMS →
                ANALYSIS GAPS.

        Each card shows source_locus + prose snippet so the agent reasons from content
        not just labels. Overflow note tells agent to call kb_query for more.
        """
        sections: list[str] = [
            f"## REQUIREMENT\n{self.requirement}",
            f"## CHANGE CLASSIFICATION\n{self.change_class}",
            f"## STAKEHOLDER INPUT (BA-provided)\n{self.stakeholder_input or '(none provided)'}",
        ]

        def _card_block(title: str, cards: list[ImpactCitation]) -> str:
            lines: list[str] = []
            shown = cards[:_MAX_PER_KIND]
            overflow = len(cards) - len(shown)
            for c in shown:
                body = self.card_bodies.get(c.id, {})
                prose = str(body.get("prose") or body.get("text_en") or "")
                locus = body.get("source_locus") or c.source_locus or ""
                line = f"[{c.id}] {c.kind} — {c.label}"
                if locus:
                    line += f"\n  Source: {locus}"
                if prose:
                    snippet = prose[:_PROSE_SNIPPET_LEN]
                    line += f"\n  Content: {snippet}{'...' if len(prose) > _PROSE_SNIPPET_LEN else ''}"
                lines.append(line)
            if overflow:
                lines.append(f"  ↳ {overflow} more card(s) omitted — call kb_query to retrieve")
            return f"## {title} — {len(shown)} of {len(cards)} shown\n" + (
                "\n".join(lines) if lines else "(none)"
            )

        sections.append(_card_block("BUSINESS RULES (BR-* KB cards)", self.br_matched))
        sections.append(_card_block("FUNCTIONAL REQUIREMENTS (FR-* KB cards)", self.fr_matched))
        sections.append(_card_block("DOMAIN TERMS & ROLES", self.dom_matched + self.role_matched))
        sections.append(
            "## ANALYSIS GAPS (FSD open items → key decisions needed)\n"
            + ("\n".join(f"- {g}" for g in self.gaps) or "(none)")
        )

        return "\n\n".join(sections)

    @classmethod
    def build(
        cls,
        fsd: FSDDocument,
        stakeholder_input: str | None = None,
        card_bodies: dict[str, dict[str, Any]] | None = None,
        artifact_id: str | None = None,
    ) -> "BRDContext":
        """Construct BRDContext from a completed, accepted FSDDocument.

        ``artifact_id`` is the artifact storage ID for the FSD (used as fsd_ref for
        the DERIVES_FROM edge BRD→FSD in the traceability chain).
        """
        matched: list[ImpactCitation] = list(fsd.references)
        gaps: list[str] = [stub.description for stub in fsd.open_items]

        return cls(
            requirement=fsd.requirement,
            kb_version=fsd.kb_version,
            change_class=fsd.change_class,
            fsd_ref=artifact_id,
            analysis_ref=fsd.analysis_ref,
            scope=fsd.scope,
            matched=matched,
            gaps=gaps,
            stakeholder_input=stakeholder_input,
            br_matched=_filter(matched, _BR_KINDS),
            fr_matched=_filter(matched, _FR_KINDS),
            dom_matched=_filter(matched, _DOM_KINDS),
            role_matched=_filter(matched, _ROLE_KINDS),
            card_bodies=card_bodies or {},
        )
