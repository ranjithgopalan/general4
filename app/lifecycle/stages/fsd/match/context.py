"""FSDContext — deterministic context assembled from the completed ANALYSIS artifact.

The FSD stage does NOT re-retrieve from the KB from scratch. It reads the already-grounded
ImpactAnalysis (matched + affected + downstream + scope + gaps) and reorganises it by KB kind
so the section builders and the ReAct agent have a structured, ready-to-use context.
``allowed_ids`` is the whitelist the grounding gate enforces on LLM output.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.lifecycle.stages.analysis.schema import (
    AffectedNode,
    Conflict,
    Coverage,
    ImpactAnalysis,
    ImpactCitation,
    Modification,
    Risk,
    ScopeDiff,
)

# Per-kind cap for LLM context (IMAD pattern: representative sample, not all cards).
# Matched cards are directly retrieved (higher relevance) → allow more per kind.
# Affected nodes come from graph-walking (noisier) → stricter cap.
_MAX_MATCHED_PER_KIND: int = 8
_MAX_AFFECTED_PER_KIND: int = 5
# Prose snippet length sent to LLM — long enough to be useful, short enough to not flood context.
_PROSE_SNIPPET_LEN: int = 300

# KB kind strings — accept BOTH the full Neo4j node labels (CLAUDE.md §3) AND the short ID-family
# codes the analysis actually emits (FR/BR/SCR/…). The analysis normalizes kind to short codes via
# family_of, so filtering on full labels alone matched nothing → every bucket was empty (the reason
# FSD §3–§10 rendered blank). Matching both conventions keeps it robust either way.
_FR_KINDS = {"FunctionalReq", "FR"}
_BR_KINDS = {"BusinessRule", "BR"}
_SCR_KINDS = {"Screen", "SCR"}
_PROC_KINDS = {"Process", "PROC"}
_WF_KINDS = {"Workflow", "WF"}
_INT_KINDS = {"Integration", "ApiOp", "INT", "API"}
_ENT_KINDS = {"Entity", "ENT"}
_SYS_KINDS = {"System", "SYS"}


def _filter_citations(nodes: list[ImpactCitation], kinds: set[str]) -> list[ImpactCitation]:
    return [n for n in nodes if n.kind in kinds]


def _filter_affected(nodes: list[AffectedNode], kinds: set[str]) -> list[AffectedNode]:
    return [n for n in nodes if n.kind in kinds]


@dataclass
class FSDContext:
    """Grounded context for the FSD stage — derived from the completed analysis."""

    requirement: str
    kb_version: str
    change_class: str
    classification_rationale: str
    analysis_ref: str | None          # artifact_id of the source analysis
    scope: ScopeDiff
    conflicts: list[Conflict]
    coverage: Coverage
    gaps: list[str]

    # Full matched + affected sets (for grounding gate and format_for_llm)
    matched: list[ImpactCitation]
    affected: list[AffectedNode]
    downstream: list[AffectedNode]

    # P3 Fix: Risks and downstream from analysis (inform AC scope and testing strategy)
    risks: list[Risk] = field(default_factory=list)

    # As-is → to-be deltas the analysis produced (seeds PROPOSED FRs for net-new/light changes).
    modifications: list[Modification] = field(default_factory=list)

    # Pre-filtered by kind (deterministic section builders consume these)
    fr_matched: list[ImpactCitation] = field(default_factory=list)
    br_matched: list[ImpactCitation] = field(default_factory=list)
    scr_matched: list[ImpactCitation] = field(default_factory=list)
    proc_matched: list[ImpactCitation] = field(default_factory=list)
    wf_matched: list[ImpactCitation] = field(default_factory=list)
    int_matched: list[ImpactCitation] = field(default_factory=list)
    ent_matched: list[ImpactCitation] = field(default_factory=list)
    sys_matched: list[ImpactCitation] = field(default_factory=list)

    fr_affected: list[AffectedNode] = field(default_factory=list)
    br_affected: list[AffectedNode] = field(default_factory=list)
    scr_affected: list[AffectedNode] = field(default_factory=list)
    proc_affected: list[AffectedNode] = field(default_factory=list)
    wf_affected: list[AffectedNode] = field(default_factory=list)
    int_affected: list[AffectedNode] = field(default_factory=list)
    ent_affected: list[AffectedNode] = field(default_factory=list)
    sys_affected: list[AffectedNode] = field(default_factory=list)

    # Full card info for matched + affected IDs — fetched from KB post-analysis (optional;
    # empty dict in fixture/dev mode → builders fall back to label gracefully).
    # Shape: {id: {prose, text_en, source_locus, kind, label}} — same as HybridRetriever.content().
    card_bodies: dict[str, dict[str, Any]] = field(default_factory=dict)

    # [NEW] Technical Analysis Sections (P1/P2 semantic search results, unfiltered)
    # These capture the full scope for FSD consumption in Section 8b
    technical_components: list[AffectedNode] = field(default_factory=list)
    technical_screens: list[AffectedNode] = field(default_factory=list)
    technical_systems: list[AffectedNode] = field(default_factory=list)

    @property
    def allowed_ids(self) -> set[str]:
        return (
            {c.id for c in self.matched}
            | {n.id for n in self.affected}
            | {n.id for n in self.downstream}
        )

    @property
    def matched_ids(self) -> set[str]:
        return {c.id for c in self.matched}

    def format_for_llm(self) -> str:
        """Enriched, grounded context string for the FSD ReAct agent (Import 1 — IMAD pattern).

        Sends card prose + source_locus alongside each card ID so the agent reasons from
        real content instead of bare labels. Grouped by KB kind, capped per kind to prevent
        context overflow — overflow note tells the agent to call kb_query/kb_read for more.
        """
        sections: list[str] = [
            f"## REQUIREMENT\n{self.requirement}",
            f"## CHANGE CLASSIFICATION\n{self.change_class} — {self.classification_rationale}",
        ]

        # ── Matched KB cards (directly retrieved — highest relevance) ──────────
        matched_by_kind: dict[str, list[ImpactCitation]] = {}
        for c in self.matched:
            matched_by_kind.setdefault(c.kind, []).append(c)

        matched_lines: list[str] = []
        total_matched_shown = 0
        for kind, cards in matched_by_kind.items():
            shown = cards[:_MAX_MATCHED_PER_KIND]
            overflow = len(cards) - len(shown)
            for c in shown:
                card = self.card_bodies.get(c.id, {})
                prose = str(card.get("prose") or card.get("text_en") or "")
                locus = card.get("source_locus") or c.source_locus or ""
                line = f"[{c.id}] {c.kind} — {c.label}"
                if locus:
                    line += f"\n  Source: {locus}"
                if prose:
                    snippet = prose[:_PROSE_SNIPPET_LEN]
                    line += f"\n  Content: {snippet}{'...' if len(prose) > _PROSE_SNIPPET_LEN else ''}"
                matched_lines.append(line)
                total_matched_shown += 1
            if overflow:
                matched_lines.append(
                    f"  ↳ {overflow} more {kind} card(s) omitted — call kb_query to retrieve"
                )

        hdr = f"## MATCHED KB CARDS — {total_matched_shown} of {len(self.matched)} shown (capped {_MAX_MATCHED_PER_KIND}/kind)"
        sections.append(hdr + "\n" + ("\n".join(matched_lines) or "(none retrieved)"))

        # ── Impact set (graph-walked neighbours — inferred relevance) ──────────
        affected_by_kind: dict[str, list[AffectedNode]] = {}
        for n in self.affected:
            affected_by_kind.setdefault(n.kind, []).append(n)

        affected_lines: list[str] = []
        total_affected_shown = 0
        for kind, nodes in affected_by_kind.items():
            shown = nodes[:_MAX_AFFECTED_PER_KIND]
            overflow = len(nodes) - len(shown)
            for n in shown:
                card = self.card_bodies.get(n.id, {})
                prose = str(card.get("prose") or card.get("text_en") or "")
                via_str = ", ".join(n.via)
                line = f"[{n.id}] {n.kind} — {n.label}  (via {via_str})"
                if prose:
                    snippet = prose[:200]
                    line += f"\n  Content: {snippet}{'...' if len(prose) > 200 else ''}"
                affected_lines.append(line)
                total_affected_shown += 1
            if overflow:
                affected_lines.append(
                    f"  ↳ {overflow} more {kind} node(s) omitted — call graph_neighbors to explore"
                )

        aff_hdr = f"## IMPACT SET (graph neighbours) — {total_affected_shown} of {len(self.affected)} shown (capped {_MAX_AFFECTED_PER_KIND}/kind)"
        sections.append(aff_hdr + "\n" + ("\n".join(affected_lines) or "(none)"))

        # ── Analysis gaps ──────────────────────────────────────────────────────
        sections.append(
            "## ANALYSIS GAPS (open questions)\n"
            + ("\n".join(f"- {g}" for g in self.gaps) or "(none)")
        )

        # ── P3 Fix: Dependency & Risk Context (inform AC testing strategy) ─────
        # Include downstream systems + risks so FSD AC can scope testing appropriately
        dep_lines: list[str] = []
        if self.downstream:
            dep_lines.append("**Downstream Systems (affected by this change):**")
            by_kind: dict[str, list[AffectedNode]] = {}
            for n in self.downstream:
                by_kind.setdefault(n.kind, []).append(n)
            for kind, nodes in sorted(by_kind.items()):
                for n in nodes[:3]:  # Show top 3 per kind
                    via_str = ", ".join(n.via) if n.via else "connected"
                    dep_lines.append(f"  • [{n.id}] {n.label} (via {via_str})")
                if len(nodes) > 3:
                    dep_lines.append(f"    …and {len(nodes) - 3} more {kind} systems")

        if self.risks:
            if dep_lines:
                dep_lines.append("")
            dep_lines.append("**Delivery Risks (from analysis):**")
            for risk in self.risks[:5]:  # Show top 5 risks
                severity_badge = f"[{risk.severity.upper()}]" if risk.severity else "[MEDIUM]"
                dep_lines.append(f"  • {severity_badge} {risk.description}")
            if len(self.risks) > 5:
                dep_lines.append(f"    …and {len(self.risks) - 5} more risk(s)")

        if dep_lines:
            sections.append("## DEPENDENCY & RISK CONTEXT\n" + "\n".join(dep_lines))

        # ── [NEW] Technical sections (P1/P2 semantic search results) ──────────────────
        # Include full technical scope so LLM sees components, screens, systems
        if self.technical_components:
            tech_comp_lines: list[str] = []
            for c in self.technical_components[:15]:  # Show top 15
                tech_comp_lines.append(f"  • [{c.id}] {c.label} ({c.kind})")
            if len(self.technical_components) > 15:
                tech_comp_lines.append(f"  • …and {len(self.technical_components) - 15} more component(s)")
            sections.append(f"## COMPONENTS AFFECTED (P1 semantic search) — {len(self.technical_components)} total\n" + "\n".join(tech_comp_lines))

        if self.technical_screens:
            tech_scr_lines: list[str] = []
            for s in self.technical_screens[:15]:  # Show top 15
                tech_scr_lines.append(f"  • [{s.id}] {s.label} ({s.kind})")
            if len(self.technical_screens) > 15:
                tech_scr_lines.append(f"  • …and {len(self.technical_screens) - 15} more screen(s)")
            sections.append(f"## SCREENS AFFECTED (P2 semantic search) — {len(self.technical_screens)} total\n" + "\n".join(tech_scr_lines))

        if self.technical_systems:
            tech_sys_lines: list[str] = []
            for sys in self.technical_systems:
                tech_sys_lines.append(f"  • [{sys.id}] {sys.label}")
            sections.append(f"## SYSTEMS AFFECTED — {len(self.technical_systems)} total\n" + "\n".join(tech_sys_lines))

        return "\n\n".join(sections)

    @classmethod
    def build(
        cls,
        analysis: ImpactAnalysis,
        artifact_id: str | None = None,
        card_bodies: dict[str, str] | None = None,
    ) -> FSDContext:
        """Construct FSDContext from a completed ImpactAnalysis."""
        m = analysis.matched
        a = analysis.affected
        return cls(
            requirement=analysis.requirement,
            kb_version=analysis.kb_version,
            change_class=analysis.classification.change_class,
            classification_rationale=analysis.classification.rationale,
            analysis_ref=artifact_id,
            scope=analysis.scope,
            conflicts=analysis.conflicts,
            coverage=analysis.coverage,
            gaps=analysis.gaps,
            matched=m,
            affected=a,
            downstream=analysis.downstream,
            risks=analysis.risks,  # P3 Fix: Include risks from analysis
            modifications=analysis.modifications,
            # By kind — matched
            fr_matched=_filter_citations(m, _FR_KINDS),
            br_matched=_filter_citations(m, _BR_KINDS),
            scr_matched=_filter_citations(m, _SCR_KINDS),
            proc_matched=_filter_citations(m, _PROC_KINDS),
            wf_matched=_filter_citations(m, _WF_KINDS),
            int_matched=_filter_citations(m, _INT_KINDS),
            ent_matched=_filter_citations(m, _ENT_KINDS),
            sys_matched=_filter_citations(m, _SYS_KINDS),
            # By kind — affected (inferred)
            fr_affected=_filter_affected(a, _FR_KINDS),
            br_affected=_filter_affected(a, _BR_KINDS),
            scr_affected=_filter_affected(a, _SCR_KINDS),
            proc_affected=_filter_affected(a, _PROC_KINDS),
            wf_affected=_filter_affected(a, _WF_KINDS),
            int_affected=_filter_affected(a, _INT_KINDS),
            ent_affected=_filter_affected(a, _ENT_KINDS),
            sys_affected=_filter_affected(a, _SYS_KINDS),
            # [NEW] Technical Analysis Sections (P1/P2 semantic search results)
            technical_components=getattr(analysis, 'technical_components', []),
            technical_screens=getattr(analysis, 'technical_screens', []),
            technical_systems=getattr(analysis, 'technical_systems', []),
            # Full card prose for matched + affected — empty dict in fixture/dev mode (fallback to label).
            card_bodies=card_bodies or {},
        )
