"""ArchitectureContext — multi-layer context built from all 4 prior artifacts.

Loads Stories (required), BRD, FSD, and ImpactAnalysis best-effort.
allowed_ids = union of KB card IDs from all 4 artifacts (artifact UUIDs filtered out).
Filters matched cards by kind for the Architect persona (SYS/INT/CMP/API/WF/FR).

Cross-repo: XPF domain-profile injection pattern (sys_matched + int_matched exposed
for {systems_list}/{interfaces_list} injection into the ReAct prompt).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.lifecycle.stages.analysis.schema import ImpactAnalysis, ImpactCitation
from app.lifecycle.stages.brd.schema import BRDDocument
from app.lifecycle.stages.fsd.schema import FSDDocument
from app.lifecycle.stages.stories.schema import StoriesDocument

# Uppercase-prefix KB ID guard (same pattern as stories/deterministic.py).
_KB_ID_RE = re.compile(r"^[A-Z][A-Z0-9]*-")


def _is_kb_id(value: str) -> bool:
    """True only for valid KB card IDs (uppercase prefix + hyphen)."""
    return bool(value and _KB_ID_RE.match(value))


# Architect persona kind filters (CLAUDE.md §6 includeKinds).
_SYS_KINDS = frozenset({"System"})
_INT_KINDS = frozenset({"Integration"})
_API_KINDS = frozenset({"ApiOp"})
_CMP_KINDS = frozenset({"Component"})
_WF_KINDS = frozenset({"Workflow", "Sequence"})
_FR_KINDS = frozenset({"FunctionalReq"})
_ENT_KINDS = frozenset({"Entity"})


def _citations_from(doc: object | None, attr: str) -> list[ImpactCitation]:
    """Extract ImpactCitation list from a document attribute, filtering artifact IDs."""
    if doc is None:
        return []
    items = getattr(doc, attr, []) or []
    return [c for c in items if isinstance(c, ImpactCitation) and _is_kb_id(c.id)]


def _collect_allowed_ids(
    stories: StoriesDocument | None,
    brd: BRDDocument | None,
    fsd: FSDDocument | None,
    impact: ImpactAnalysis | None,
) -> set[str]:
    """Union of all KB card IDs across all 4 prior artifacts."""
    ids: set[str] = set()

    if stories:
        for row in stories.traceability or []:
            ids.update(kid for kid in (row.kb_ids or []) if _is_kb_id(kid))

    for doc in (brd, fsd):
        if doc:
            for c in (getattr(doc, "references", None) or []):
                if isinstance(c, ImpactCitation) and _is_kb_id(c.id):
                    ids.add(c.id)

    if impact:
        ids.update(c.id for c in (impact.matched or []) if _is_kb_id(c.id))
        ids.update(n.id for n in (impact.affected or []) if _is_kb_id(n.id))
        ids.update(n.id for n in (impact.downstream or []) if _is_kb_id(n.id))

    return ids


def _build_matched(
    stories: StoriesDocument | None,
    brd: BRDDocument | None,
    fsd: FSDDocument | None,
    impact: ImpactAnalysis | None,
    extra_citations: list[ImpactCitation] | None,
) -> tuple[list[ImpactCitation], set[str]]:
    """Unified deduped matched-citation list (priority: impact→fsd→brd→stories→C1 graph retrieval).

    Returns (matched, extra_allowed) where extra_allowed = the C1 graph-retrieved IDs to fold into
    the grounding whitelist (prior-artifact IDs are already in allowed_ids via _collect_allowed_ids).
    """
    seen: set[str] = set()
    matched: list[ImpactCitation] = []

    for source in (
        _citations_from(impact, "matched"),
        _citations_from(fsd, "references"),
        _citations_from(brd, "references"),
    ):
        for c in source:
            if c.id not in seen:
                seen.add(c.id)
                matched.append(c)

    # Fallback: include affected CMP/API/INT nodes from impact analysis
    # (P1/P2 semantic search results may be in affected list for display purposes)
    if impact and (impact.affected or []):
        architect_kinds = {"Component", "CMP", "ApiOp", "API", "Integration", "INT"}
        for n in impact.affected:
            if n.kind in architect_kinds and n.id not in seen and _is_kb_id(n.id):
                seen.add(n.id)
                matched.append(ImpactCitation(id=n.id, kind=n.kind, label=n.label, source_locus=n.source_locus, summary=n.summary))

    if stories:
        for row in stories.traceability or []:
            for kid in row.kb_ids or []:
                if _is_kb_id(kid) and kid not in seen:
                    seen.add(kid)
                    matched.append(ImpactCitation(id=kid, kind="", label=kid))

    # C1 — union independently-retrieved architecture nodes (kind-typed from the graph)
    extra_allowed: set[str] = set()
    for c in (extra_citations or []):
        if _is_kb_id(c.id) and c.id not in seen:
            seen.add(c.id)
            matched.append(c)
            extra_allowed.add(c.id)

    return matched, extra_allowed


def _collect_gaps(fsd: FSDDocument | None, brd: BRDDocument | None) -> list[str]:
    """Seed ARCH-TODO open items from FSD open items + BRD OPEN key-decisions."""
    gaps: list[str] = []
    if fsd:
        gaps.extend(s.description for s in (fsd.open_items or []) if s.description)
    if brd:
        gaps.extend(
            kd.description for kd in (brd.key_decisions or [])
            if kd.tag == "OPEN" and kd.description
        )
    return gaps


@dataclass
class ArchitectureContext:
    """Multi-layer context for the Architect ReAct agent and SRD section builders."""

    workspace_id: str
    requirement: str
    change_class: str
    kb_version: str

    # All unique matched KB citations (deduped, priority: impact → fsd → brd → stories)
    matched: list[ImpactCitation] = field(default_factory=list)

    # Kind-filtered subsets for section builders (XPF domain-profile injection)
    sys_matched: list[ImpactCitation] = field(default_factory=list)   # SYS-*
    int_matched: list[ImpactCitation] = field(default_factory=list)   # INT-*
    api_matched: list[ImpactCitation] = field(default_factory=list)   # API-*
    cmp_matched: list[ImpactCitation] = field(default_factory=list)   # CMP-*
    wf_matched: list[ImpactCitation] = field(default_factory=list)    # WF-*/SEQ-*
    fr_matched: list[ImpactCitation] = field(default_factory=list)    # FR-*
    ent_matched: list[ImpactCitation] = field(default_factory=list)   # ENT-*

    # Grounding whitelist — all valid KB IDs from all 4 artifacts
    allowed_ids: set[str] = field(default_factory=set)

    # Prior artifact documents (best-effort — may be None)
    stories: StoriesDocument | None = None
    fsd_doc: FSDDocument | None = None
    brd_doc: BRDDocument | None = None
    impact: ImpactAnalysis | None = None

    # Pre-built async graph-walk results (passed in from handler before assemble)
    asIs_diagram: str = ""
    seq_diagrams: list[dict] = field(default_factory=list)

    # KB card bodies for LLM grounding (kb_id → {"prose": ..., "text_en": ...})
    card_bodies: dict[str, Any] = field(default_factory=dict)

    # Gaps from prior stages (used to seed ARCH-TODO open items)
    gaps: list[str] = field(default_factory=list)

    # Artifact IDs for DERIVES_FROM traceability chain
    stories_ref: str | None = None
    brd_ref: str | None = None
    fsd_ref: str | None = None
    analysis_ref: str | None = None

    @classmethod
    def build(
        cls,
        *,
        stories: StoriesDocument | None,
        brd: BRDDocument | None = None,
        fsd: FSDDocument | None = None,
        impact: ImpactAnalysis | None = None,
        card_bodies: dict[str, Any] | None = None,
        stories_ref: str | None = None,
        brd_ref: str | None = None,
        fsd_ref: str | None = None,
        analysis_ref: str | None = None,
        asIs_diagram: str = "",
        seq_diagrams: list[dict] | None = None,
        extra_citations: list[ImpactCitation] | None = None,
    ) -> ArchitectureContext:
        """Build from all 4 prior artifacts (stories required; others best-effort).

        ``extra_citations`` (C1) are architecture nodes retrieved independently from the graph when
        the upstream set is thin; they are unioned into ``matched`` and ``allowed_ids`` (grounding
        whitelist) so the SRD can cite them, exactly like prior-artifact IDs.
        """

        # Workspace identity and requirement — fall through from stories → brd → fsd
        workspace_id = (
            (stories.workspace_id if stories else None)
            or (brd.workspace_id if brd else None)
            or (fsd.workspace_id if fsd else None)
            or "unknown"
        )
        requirement = next(
            (doc.requirement for doc in (stories, brd, fsd) if doc and getattr(doc, "requirement", "")),
            "",
        )
        change_class = next(
            (doc.change_class for doc in (stories, brd, fsd) if doc and getattr(doc, "change_class", "")),
            "New",
        )
        kb_version = next(
            (doc.kb_version for doc in (stories, brd, fsd) if doc and getattr(doc, "kb_version", "")),
            "v1",
        )

        allowed_ids = _collect_allowed_ids(stories, brd, fsd, impact)

        # Build unified matched list (priority order; dedup by ID) + fold C1 retrieval into whitelist
        matched, extra_allowed = _build_matched(stories, brd, fsd, impact, extra_citations)
        allowed_ids |= extra_allowed

        # Kind-filtered subsets
        sys_matched = [c for c in matched if c.kind in _SYS_KINDS]
        int_matched = [c for c in matched if c.kind in _INT_KINDS]
        api_matched = [c for c in matched if c.kind in _API_KINDS]
        cmp_matched = [c for c in matched if c.kind in _CMP_KINDS]
        wf_matched = [c for c in matched if c.kind in _WF_KINDS]
        fr_matched = [c for c in matched if c.kind in _FR_KINDS]
        ent_matched = [c for c in matched if c.kind in _ENT_KINDS]

        gaps = _collect_gaps(fsd, brd)

        return cls(
            workspace_id=workspace_id,
            requirement=requirement,
            change_class=change_class,
            kb_version=kb_version,
            matched=matched,
            sys_matched=sys_matched,
            int_matched=int_matched,
            api_matched=api_matched,
            cmp_matched=cmp_matched,
            wf_matched=wf_matched,
            fr_matched=fr_matched,
            ent_matched=ent_matched,
            allowed_ids=allowed_ids,
            stories=stories,
            fsd_doc=fsd,
            brd_doc=brd,
            impact=impact,
            asIs_diagram=asIs_diagram,
            seq_diagrams=seq_diagrams or [],
            card_bodies=card_bodies or {},
            gaps=gaps,
            stories_ref=stories_ref,
            brd_ref=brd_ref,
            fsd_ref=fsd_ref,
            analysis_ref=analysis_ref,
        )

    def format_for_llm(self) -> str:
        """Format context for the Architect ReAct agent (XPF domain-profile injection)."""
        lines = [
            f"## REQUIREMENT\n{self.requirement}",
            f"\n## CHANGE CLASSIFICATION\n{self.change_class}",
        ]

        # XPF: inject known domain vocab ({systems_list} / {interfaces_list})
        if self.sys_matched:
            systems_list = "\n".join(
                f"  [{c.id}] {c.label}" for c in self.sys_matched
            )
            lines.append(f"\n## KNOWN SYSTEMS (SYS-* KB cards)\n{systems_list}")

        if self.int_matched or self.api_matched:
            ifaces = self.int_matched + self.api_matched
            ifaces_list = "\n".join(
                f"  [{c.id}] {c.label} ({c.kind})" for c in ifaces
            )
            lines.append(f"\n## KNOWN INTEGRATIONS & APIs (INT-*/API-*)\n{ifaces_list}")

        if self.cmp_matched:
            cmp_list = "\n".join(f"  [{c.id}] {c.label}" for c in self.cmp_matched)
            lines.append(f"\n## KNOWN COMPONENTS (CMP-*)\n{cmp_list}")

        if self.fr_matched:
            fr_list = "\n".join(f"  [{c.id}] {c.label}" for c in self.fr_matched[:10])
            lines.append(f"\n## FUNCTIONAL REQUIREMENTS (FR-*)\n{fr_list}")

        if self.asIs_diagram:
            lines.append(
                f"\n## AS-IS ARCHITECTURE (Mermaid — cite as reference, do not copy verbatim)\n"
                f"```mermaid\n{self.asIs_diagram}\n```"
            )

        if self.gaps:
            gap_list = "\n".join(f"  - {g}" for g in self.gaps[:8])
            lines.append(f"\n## OPEN GAPS (from prior stages — for ARCH-TODO stubs)\n{gap_list}")

        return "\n".join(lines)
