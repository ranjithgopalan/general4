"""DevContext — multi-layer context built from the accepted SRDDocument.

Built from the SRD (required) + optionally StoriesDocument (P1) and FSDDocument (P2).
Filters KB cards to Developer persona kinds: CMP, API, SCR, INT, FR, SYS.

P0 fix: also scans srd.references for developer-visible IDs (eliminates false gaps).
P1 addition: accepts stories_doc → story_details lookup (story_id → StoryRow).
P2 addition: accepts fsd_doc → fr_map (fr_id → FunctionalRequirement) + br_map (br_id → BusinessRule).

Cross-repo: ArchitectureContext pattern (context.py in architecture/match/) applied to SRD.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.lifecycle.stages.analysis.schema import ImpactCitation
from app.lifecycle.stages.architecture.schema import (
    ApiSpec,
    DataEntity,
    IntegrationPoint,
    SchemaChange,
    SequenceDiagram,
    SRDDocument,
    SRDStoryRef,
    SystemComponent,
)

if TYPE_CHECKING:
    # Avoid circular import at runtime; used only in type hints.
    from app.lifecycle.stages.fsd.schema import BusinessRule, FunctionalRequirement, FSDDocument
    from app.lifecycle.stages.stories.schema import StoryRow, StoriesDocument

_KB_ID_RE = re.compile(r"^[A-Z][A-Z0-9]*-")

# Developer persona allowed kinds (from personas.json)
_DEV_KINDS = frozenset({"System", "Integration", "Component", "ApiOp", "Screen", "FunctionalReq"})
_DEV_REF_KINDS = frozenset({"FunctionalReq", "Integration", "Component", "ApiOp", "Screen", "System"})
_CMP_KINDS = frozenset({"Component"})
_SYS_KINDS = frozenset({"System"})
_INT_KINDS = frozenset({"Integration"})
_API_KINDS = frozenset({"ApiOp"})
_SCR_KINDS = frozenset({"Screen"})
_FR_KINDS = frozenset({"FunctionalReq"})


def _is_kb_id(value: str) -> bool:
    return bool(value and _KB_ID_RE.match(value))


def _collect_dev_allowed(srd: SRDDocument) -> set[str]:
    """Developer-visible KB IDs from component/integration design, FRs, and (P0) references."""
    allowed: set[str] = set()
    for comp in srd.component_design:
        if _is_kb_id(comp.id) and comp.kind in _DEV_KINDS:
            allowed.add(comp.id)
    for intg in srd.integration_design:
        if _is_kb_id(intg.id) and intg.kind in _DEV_KINDS:
            allowed.add(intg.id)
    for fr in srd.functional_requirements:
        if _is_kb_id(fr.id):
            allowed.add(fr.id)
    # P0 FIX — cards lacking full protocol/component fields land in references, not the design lists.
    for ref in (srd.references or []):
        if _is_kb_id(ref.id) and ref.kind in _DEV_REF_KINDS:
            allowed.add(ref.id)
    return allowed


@dataclass
class DevContext:
    """Context object passed to all deterministic section builders.

    Required: srd_doc (SRDDocument — accepted Architecture artifact).
    Optional: stories_doc (P1), fsd_doc (P2) — best-effort, never raise on absence.
    """

    workspace_id: str
    requirement: str
    change_class: str
    kb_version: str

    # SRD source of truth (required)
    srd_doc: SRDDocument

    # KB cards filtered to developer-visible kinds
    cmp_cards: list[SystemComponent] = field(default_factory=list)   # Component
    sys_cards: list[SystemComponent] = field(default_factory=list)   # System
    int_cards: list[IntegrationPoint] = field(default_factory=list)  # Integration
    api_cards: list[IntegrationPoint] = field(default_factory=list)  # ApiOp
    seq_cards: list[SequenceDiagram] = field(default_factory=list)   # SequenceDiagram
    story_refs: list[SRDStoryRef] = field(default_factory=list)      # story traceability

    # ── D1/D4 — Screen + DB-entity + API-spec + schema-change carry-forward from the SRD ──
    scr_cards: list[ImpactCitation] = field(default_factory=list)    # Screen (from srd.references)
    data_entities: list[DataEntity] = field(default_factory=list)    # ENT-* DB tables (srd.data_model)
    api_specs: list[ApiSpec] = field(default_factory=list)           # srd.api_specs (C4)
    schema_changes: list[SchemaChange] = field(default_factory=list) # srd.schema_changes (C6)

    # Full KB card bodies (id → body dict) fetched by handler
    card_bodies: dict[str, Any] = field(default_factory=dict)

    # Allowed KB IDs for this developer (SRD-derived; excludes BA/Architect-only cards)
    allowed_ids: set[str] = field(default_factory=set)

    # Gaps inherited from SRD open_items (ARCH-TODO → becomes DEV-TODO)
    gaps: list[str] = field(default_factory=list)

    # Artifact ref for DERIVES_FROM traceability
    srd_ref: str | None = None

    # ── P1: Stories data (optional — populated when StoriesDocument is available) ──
    # story_id → StoryRow (has as_a, i_want, so_that, ac_given, ac_when, ac_then, priority, points)
    story_details: dict[str, Any] = field(default_factory=dict)

    # ── P2: FSD data (optional — best-effort; never raises on absence) ───────────
    # fr_id → FunctionalRequirement (has as_is, to_be, source_locus, priority)
    fr_map: dict[str, Any] = field(default_factory=dict)
    # br_id → BusinessRule (has rule_text, applies_to, source_locus)
    br_map: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def build(
        cls,
        srd: SRDDocument,
        card_bodies: dict[str, Any] | None = None,
        srd_ref: str | None = None,
        stories_doc: "StoriesDocument | None" = None,
        fsd_doc: "FSDDocument | None" = None,
    ) -> "DevContext":
        """Build DevContext from an accepted SRDDocument.

        Filters component_design, integration_design, and (P0 fix) references
        to developer-visible kinds only. Sequence diagrams and story_refs are
        passed through as-is.

        P1: stories_doc enriches story_details lookup.
        P2: fsd_doc enriches fr_map + br_map.
        """
        # ── Collect developer-visible allowed IDs (design lists + FRs + P0 references) ──
        allowed = _collect_dev_allowed(srd)

        cmp_cards = [c for c in srd.component_design if c.kind in _CMP_KINDS]
        sys_cards = [c for c in srd.component_design if c.kind in _SYS_KINDS]
        int_cards = [i for i in srd.integration_design if i.kind in _INT_KINDS]
        api_cards = [i for i in srd.integration_design if i.kind in _API_KINDS]

        # D1/D4 — carry Screen refs + the SRD's data model / API specs / schema changes forward
        scr_cards = [r for r in (srd.references or []) if r.kind in _SCR_KINDS and _is_kb_id(r.id)]

        gaps = [s.description for s in (srd.open_items or []) if s.description]

        # ── P1: story_details from StoriesDocument ────────────────────────────
        story_details: dict[str, Any] = {}
        if stories_doc is not None:
            for row in (stories_doc.stories or []):
                story_details[row.story_id] = row

        # ── P2: fr_map + br_map from FSDDocument ─────────────────────────────
        fr_map: dict[str, Any] = {}
        br_map: dict[str, Any] = {}
        if fsd_doc is not None:
            for fr in (fsd_doc.functional_requirements or []):
                fr_map[fr.id] = fr
            for br in (fsd_doc.business_rules or []):
                br_map[br.id] = br

        return cls(
            workspace_id=srd.workspace_id,
            requirement=srd.requirement,
            change_class=srd.change_class,
            kb_version=srd.kb_version,
            srd_doc=srd,
            cmp_cards=cmp_cards,
            sys_cards=sys_cards,
            int_cards=int_cards,
            api_cards=api_cards,
            seq_cards=list(srd.sequence_diagrams or []),
            story_refs=list(srd.story_refs or []),
            scr_cards=scr_cards,
            data_entities=list(srd.data_model or []),
            api_specs=list(srd.api_specs or []),
            schema_changes=list(srd.schema_changes or []),
            card_bodies=card_bodies or {},
            allowed_ids=allowed,
            gaps=gaps,
            srd_ref=srd_ref,
            story_details=story_details,
            fr_map=fr_map,
            br_map=br_map,
        )

    def format_for_llm(self) -> str:
        """Human-readable context block injected into the ReAct agent prompt."""
        lines = [
            f"## REQUIREMENT\n{self.requirement}",
            f"\n## CHANGE CLASSIFICATION\n{self.change_class}",
        ]
        if self.sys_cards:
            sys_list = "\n".join(f"  [{c.id}] {c.label} ({c.kind})" for c in self.sys_cards)
            lines.append(f"\n## SYSTEMS TO MODIFY (SYS-*)\n{sys_list}")
        if self.cmp_cards:
            cmp_list = "\n".join(
                f"  [{c.id}] {c.label} — {c.source_locus or 'no locus'}"
                for c in self.cmp_cards
            )
            lines.append(f"\n## COMPONENTS (CMP-*)\n{cmp_list}")
        if self.api_cards:
            api_list = "\n".join(
                f"  [{i.id}] {i.label} ({i.protocol or 'protocol TBD'})"
                for i in self.api_cards
            )
            lines.append(f"\n## API OPERATIONS (API-*)\n{api_list}")
        if self.int_cards:
            int_list = "\n".join(
                f"  [{i.id}] {i.label} [{i.protocol or 'ARCH-TODO'}] {i.direction}"
                for i in self.int_cards
            )
            lines.append(f"\n## INTEGRATIONS (INT-*)\n{int_list}")
        # ── C4: API Specs (REST operations from Architecture) ──
        if self.api_specs:
            api_spec_list = "\n".join(
                f"  [{api.id}] {api.http_method} {api.path} — {api.label}"
                for api in self.api_specs[:10]
            )
            lines.append(f"\n## API SPECS (C4 Operations)\n{api_spec_list}")

        # ── C6: Schema Changes (Database mutations from Architecture) ──
        if self.schema_changes:
            schema_list = "\n".join(
                f"  {sc.op} {sc.table}.{sc.column} ({sc.column_type}) — {sc.rationale[:60]}"
                for sc in self.schema_changes[:10]
            )
            lines.append(f"\n## SCHEMA CHANGES (C6 Database Mutations)\n{schema_list}")

        if self.story_details:
            story_list = "\n".join(
                f"  [{sid}] {getattr(row, 'title', sid)} — "
                f"Priority: {getattr(row, 'priority', 'Medium')} "
                f"Points: {getattr(row, 'points', 0)}"
                for sid, row in list(self.story_details.items())[:5]
            )
            lines.append(f"\n## USER STORIES (from StoriesDocument)\n{story_list}")
        if self.gaps:
            gap_list = "\n".join(f"  - {g}" for g in self.gaps[:8])
            lines.append(f"\n## OPEN GAPS (ARCH-TODO → DEV-TODO)\n{gap_list}")
        return "\n".join(lines)
