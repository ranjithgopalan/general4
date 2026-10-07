"""StoriesContext — deterministic context assembled from the completed BRD artifact.

The STORIES stage does NOT re-retrieve from the KB from scratch. It reads the already-grounded
BRDDocument (references, scope, key_decisions, change_class) and reorganises it by KB kind so
the section builders and the ReAct agent have a structured, ready-to-use context.

Key design points:
  • Input is BRDDocument (not FSDDocument) — Stories is the fourth stage; BRD is the prerequisite.
  • Filtered kinds: BR / FR / PROC / ROLE — narrower than BRD (which also reads DOM/Term).
  • ``gaps`` = OPEN key_decisions from BRD — these become story open items.
  • ``allowed_ids`` is derived from BRD references — the grounding gate enforces this.
  • card_bodies: {id: {prose, text_en, source_locus, kind, label}} — same shape as BRDContext.
  • BRD workaround W1: Stories reads brd.references (ImpactCitation with kind intact),
    NOT brd.acceptance_criteria (ACRows strip kind).
  • BRD workaround W2: BRDContext has no proc_matched; we filter brd.references ourselves.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.lifecycle.stages.analysis.schema import ImpactCitation, ScopeDiff
from app.lifecycle.stages.brd.schema import BRDDocument

# Per-kind cap for LLM context — keeps the stories agent prompt focused.
# For Enhancement changes: increased range for comprehensive story generation
_MAX_PER_KIND: int = 8
_MAX_PER_KIND_ENHANCEMENT: int = 4  # Enhancement: 4 cards per kind (from 1) → 15-20 stories possible
_PROSE_SNIPPET_LEN: int = 300
_PROSE_SNIPPET_LEN_ENHANCEMENT: int = 400  # Enhancement: 400 chars (from 200) → fuller context

# KB kind filter sets (match Neo4j labels from CLAUDE.md §3).
# Accept both the full Neo4j node labels (CLAUDE.md §3) AND the short ID-family codes the
# analysis actually emits (FR/BR/SCR/…). The analysis normalizes kind to short codes via
# family_of(), so filtering on full labels alone matches nothing. Same convention as FSDContext.
_BR_KINDS = frozenset({"BusinessRule", "BR"})
_FR_KINDS = frozenset({"FunctionalReq", "FR"})
_PROC_KINDS = frozenset({"Process", "Workflow", "PROC", "WF"})
_ROLE_KINDS = frozenset({"Role", "ROLE"})
_SCR_KINDS = frozenset({"Screen", "SCR"})

# change_class values that use CONTEXTUAL (structural) grounding, not CONTENT (word-overlap).
# Enhancement added (2026-08-11): Enhancement stories describe future-state delta (what's being added
# to existing systems), so word-overlap with as-is KB prose is meaningless. Structural check
# (matched system exists?) is the correct gate. Derived stays as CONTENT grounding since it must
# echo existing BR/FR rules (constrained behavior).
# Exported so deterministic.py and reasoned.py can import without duplication.
CONTEXTUAL_CLASSES = frozenset({"New", "Migration", "Enhancement"})


def _filter(nodes: list[ImpactCitation], kinds: frozenset[str]) -> list[ImpactCitation]:
    return [n for n in nodes if n.kind in kinds]


@dataclass
class StoriesContext:
    """Grounded context for the Stories stage — derived from the completed BRD artifact."""

    requirement: str
    kb_version: str
    change_class: str
    brd_ref: str | None          # artifact_id of the accepted BRD
    analysis_ref: str | None     # transitively from BRD→FSD (for full GROUNDS chain)
    scope: ScopeDiff

    # Source of truth for grounding gate — all KB card ids the BRD grounded to.
    # BRD workaround W1: use brd.references (ImpactCitation with kind intact),
    # NOT brd.acceptance_criteria (ACRows strip the kind field).
    matched: list[ImpactCitation]

    # OPEN key_decisions from BRD — become story open items and feed Derived refinement.
    gaps: list[str]

    # Kind-filtered subsets for deterministic builders.
    # BRD workaround W2: BRDContext has no proc_matched; we filter brd.references ourselves.
    br_matched: list[ImpactCitation] = field(default_factory=list)
    fr_matched: list[ImpactCitation] = field(default_factory=list)
    proc_matched: list[ImpactCitation] = field(default_factory=list)
    role_matched: list[ImpactCitation] = field(default_factory=list)
    scr_matched: list[ImpactCitation] = field(default_factory=list)

    # Full card prose — {id: {prose, text_en, source_locus, kind, label}}.
    # Empty dict in fixture/dev mode; builders fall back to label gracefully.
    card_bodies: dict[str, dict[str, Any]] = field(default_factory=dict)

    @property
    def allowed_ids(self) -> set[str]:
        """Whitelist for the grounding gate — only IDs grounded by the BRD stage."""
        return {c.id for c in self.matched}

    @property
    def is_contextual(self) -> bool:
        """True for New/Migration/Enhancement workspaces, or when only Screen cards are matched.

        SCR-only contexts use structural grounding: the stories describe future implementation
        of a screen, not modifications to existing prose — word-overlap is meaningless.
        """
        if self.change_class in CONTEXTUAL_CLASSES:
            return True
        if self.scr_matched and not self.fr_matched and not self.br_matched:
            return True
        return False

    def format_for_llm(self) -> str:
        """Structured context string for the Stories ReAct agent.

        Format: REQUIREMENT → CHANGE CLASSIFICATION → SCOPE →
                FR CARDS → BR CARDS → PROCESS CARDS → ROLES → GAPS.

        Each card shows source_locus + prose snippet so the agent reasons from content
        not just labels. Overflow note tells agent to call kb_query for more.

        For New/Migration workspaces, adds a note that CONTEXTUAL grounding applies
        so the agent understands it should cite system/role cards even though word-
        overlap with those cards is not expected in the story prose.
        """
        sections: list[str] = [
            f"## REQUIREMENT\n{self.requirement}",
            f"## CHANGE CLASSIFICATION\n{self.change_class}",
        ]

        if self.is_contextual:
            sections.append(
                "## GROUNDING MODE: CONTEXTUAL\n"
                "This is a New/Migration workspace. The KB holds as-is system context; "
                "stories describe future state. Cite system/role/architecture KB cards "
                "in source_refs to establish context — word-overlap is NOT required. "
                "Every story MUST cite at least one KB card (STUB = no citation at all)."
            )

        # ScopeDiff items are ScopeItem objects — extract .label for display.
        # ScopeDiff fields: new, enhancement, existing (no deferred field).
        def _labels(items: list) -> str:
            return " · ".join(getattr(item, "label", str(item)) for item in items)

        scope_parts: list[str] = []
        if self.scope.new:
            scope_parts.append("IN SCOPE: " + _labels(self.scope.new))
        if self.scope.existing:
            scope_parts.append("OUT OF SCOPE: " + _labels(self.scope.existing))
        if self.scope.enhancement:
            scope_parts.append("ENHANCEMENT: " + _labels(self.scope.enhancement))
        sections.append("## SCOPE\n" + ("\n".join(scope_parts) or "(not specified)"))

        # Reduce context for Enhancement changes to speed up LLM synthesis
        max_per_kind = _MAX_PER_KIND_ENHANCEMENT if self.change_class == "Enhancement" else _MAX_PER_KIND
        prose_len = _PROSE_SNIPPET_LEN_ENHANCEMENT if self.change_class == "Enhancement" else _PROSE_SNIPPET_LEN

        def _card_block(title: str, cards: list[ImpactCitation]) -> str:
            lines: list[str] = []
            shown = cards[:max_per_kind]
            overflow = len(cards) - len(shown)
            for c in shown:
                body = self.card_bodies.get(c.id, {})
                prose = str(body.get("prose") or body.get("text_en") or "")
                locus = body.get("source_locus") or c.source_locus or ""
                line = f"[{c.id}] {c.kind} — {c.label}"
                if locus:
                    line += f"\n  Source: {locus}"
                if prose:
                    snippet = prose[:prose_len]
                    line += f"\n  Content: {snippet}{'...' if len(prose) > prose_len else ''}"
                lines.append(line)
            if overflow:
                lines.append(f"  ↳ {overflow} more card(s) omitted — call kb_query to retrieve")
            return f"## {title} — {len(shown)} of {len(cards)} shown\n" + (
                "\n".join(lines) if lines else "(none)"
            )

        sections.append(_card_block("FUNCTIONAL REQUIREMENTS (FR-* KB cards — one story per FR)", self.fr_matched))
        sections.append(_card_block("BUSINESS RULES (BR-* KB cards — drive Derived detection)", self.br_matched))
        sections.append(_card_block("PROCESS & WORKFLOW CARDS", self.proc_matched))
        sections.append(_card_block("ROLES (for as_a field)", self.role_matched))

        if self.scr_matched:
            # Show ALL SCR cards — no per-kind cap. All required context is here; the LLM
            # must NOT call kb_query. Prose capped at 300 chars per card to stay concise.
            _SCR_PROSE_LEN = 300
            scr_lines: list[str] = []
            for c in self.scr_matched:
                body = self.card_bodies.get(c.id, {})
                prose = str(body.get("prose") or body.get("text_en") or "")
                locus = body.get("source_locus") or c.source_locus or ""
                line = f"[{c.id}] {c.kind} — {c.label}"
                if locus:
                    line += f"\n  Source: {locus}"
                if prose:
                    snippet = prose[:_SCR_PROSE_LEN]
                    line += f"\n  Content: {snippet}{'...' if len(prose) > _SCR_PROSE_LEN else ''}"
                scr_lines.append(line)
            sections.append(
                "## SCREEN SPECIFICATIONS (SCR-* KB cards)\n"
                "ALL screens are listed below. No tool calls are available — write stories\n"
                "directly from this context. Use the Content: field names and buttons verbatim\n"
                "in ac_given / ac_when / ac_then.\n"
                "ID suffixes: -RN- = Renewal | -EN- = Endorsement | -NB- = New Business\n\n"
                + "\n".join(scr_lines)
            )
        sections.append(
            "## OPEN ITEMS (BRD gaps → story open items)\n"
            + ("\n".join(f"- {g}" for g in self.gaps) or "(none)")
        )

        return "\n\n".join(sections)

    @classmethod
    def build(
        cls,
        source: Any,
        artifact_id: str | None = None,
        card_bodies: dict[str, dict[str, Any]] | None = None,
    ) -> "StoriesContext":
        """Construct StoriesContext from the accepted prior spec.

        Since the BRD stage was MERGED into the FSD, ``source`` is now the FSDDocument (which carries the
        same handles Stories needs: ``references``, ``change_class``, ``requirement``). A BRDDocument is
        still accepted for backward-compatibility. Read duck-typed so either works.

        Workaround W1: reads ``references`` (ImpactCitation with kind intact), NOT acceptance_criteria.
        """
        matched: list[ImpactCitation] = list(getattr(source, "references", []) or [])
        # Story gaps / open items: BRD's OPEN key_decisions, else the FSD's open_items.
        gaps: list[str] = [
            kd.description for kd in getattr(source, "key_decisions", []) or [] if getattr(kd, "tag", "") == "OPEN"
        ]
        if not gaps:
            gaps = [
                (getattr(oi, "description", "") or getattr(oi, "text", "") or str(oi))
                for oi in getattr(source, "open_items", []) or []
            ]

        return cls(
            requirement=getattr(source, "requirement", "") or "",
            kb_version=getattr(source, "kb_version", "") or "",
            change_class=getattr(source, "change_class", "") or "",
            brd_ref=artifact_id,  # now the FSD artifact id (DERIVES_FROM: Stories → FSD)
            analysis_ref=getattr(source, "analysis_ref", None),
            # BRD uses ``scope_definition``; the merged FSD uses ``scope`` — accept either, never None.
            scope=(getattr(source, "scope_definition", None) or getattr(source, "scope", None) or ScopeDiff()),
            matched=matched,
            gaps=gaps,
            br_matched=_filter(matched, _BR_KINDS),
            fr_matched=_filter(matched, _FR_KINDS),
            proc_matched=_filter(matched, _PROC_KINDS),
            role_matched=_filter(matched, _ROLE_KINDS),
            scr_matched=_filter(matched, _SCR_KINDS),
            card_bodies=card_bodies or {},
        )
