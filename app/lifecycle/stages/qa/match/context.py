"""QAContext — multi-layer context built from all accepted prior SDLC artifacts.

Source hierarchy (confirmed design):
  story_test_cases    → StoriesDocument Gherkin (no fallback)
  screen_validations  → FSDDocument.screen_specs (no fallback)
  business_rule_tests → BRDDocument.business_rules → card_bodies if empty
  workflow_scenarios  → SRDDocument.sequence_diagrams (no fallback)
  role_access_tests   → DevDocument.implementation_plan × StoriesDocument.as_a
  integration_tests   → SRDDocument.integration_design → card_bodies if empty

Two-pass pattern (mirrors DevContext):
  Pass 1: QAContext.build() with no card_bodies → discovers needed IDs (allowed_ids + fallback)
  Pass 2: handler fetches card_bodies; QAContext.build() again with card_bodies filled

needs_br_kb_fallback  → True when BRD.business_rules is empty; handler fetches BR-* from brd.references
needs_int_kb_fallback → True when SRD.integration_design is empty; handler fetches INT-* from srd.references
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.lifecycle.stages.brd.schema import BRDDocument, BRDRule
    from app.lifecycle.stages.developer.schema import DevDocument, ImplTask
    from app.lifecycle.stages.fsd.schema import FSDDocument, ScreenSpec
    from app.lifecycle.stages.architecture.schema import IntegrationPoint, SequenceDiagram, SRDDocument
    from app.lifecycle.stages.stories.schema import StoriesDocument, StoryRow

_KB_ID_RE = re.compile(r"^[A-Z][A-Z0-9]*-")


def _is_kb_id(value: str) -> bool:
    return bool(value and _KB_ID_RE.match(value))


@dataclass
class QAContext:
    """Context object passed to all QA section builders.

    Required: dev_doc (DevDocument — accepted Developer artifact; the immediate prerequisite).
    Optional: stories_doc, srd_doc, fsd_doc, brd_doc — best-effort, never raise on absence.
    card_bodies: dict — fetched by handler in two-pass pattern.
    """

    workspace_id: str
    requirement: str
    change_class: str
    kb_version: str

    # ── Stories data (for story_test_cases) ───────────────────────────────────
    story_rows: list[Any] = field(default_factory=list)   # list[StoryRow]
    stories_source: str = "absent"                         # "prior_artifact" | "absent"

    # ── FSD screen specs (for screen_validations) ─────────────────────────────
    fsd_screens: list[Any] = field(default_factory=list)  # list[ScreenSpec]
    fsd_source: str = "absent"                             # "prior_artifact" | "absent"

    # ── BRD business rules (for business_rule_tests) ──────────────────────────
    brd_rules: list[Any] = field(default_factory=list)    # list[BRDRule]
    brd_source: str = "prior_artifact"                     # "prior_artifact" | "kb_direct"
    br_ref_ids: list[str] = field(default_factory=list)   # BR-* IDs from brd.references

    # ── SRD data (for workflow_scenarios + integration_tests) ─────────────────
    srd_seq_diagrams: list[Any] = field(default_factory=list)  # list[SequenceDiagram]
    srd_int_design: list[Any] = field(default_factory=list)    # list[IntegrationPoint]
    srd_source: str = "absent"                                  # "prior_artifact" | "absent"
    int_ref_ids: list[str] = field(default_factory=list)       # INT-* IDs from srd.references

    # ── Dev impl_tasks (for role_access_tests) ────────────────────────────────
    impl_tasks: list[Any] = field(default_factory=list)   # list[ImplTask]
    dev_source: str = "absent"                             # "prior_artifact" | "absent"

    # ── KB card bodies (fetched by handler) ───────────────────────────────────
    card_bodies: dict[str, Any] = field(default_factory=dict)

    # ── Allowed KB IDs for QA persona ─────────────────────────────────────────
    allowed_ids: set[str] = field(default_factory=set)

    # ── Fallback flags (set by build(), read by handler) ─────────────────────
    needs_br_kb_fallback: bool = False
    needs_int_kb_fallback: bool = False

    # ── Artifact refs for DERIVES_FROM ────────────────────────────────────────
    dev_ref: str | None = None
    stories_ref: str | None = None
    srd_ref: str | None = None
    fsd_ref: str | None = None
    brd_ref: str | None = None

    @classmethod
    def build(
        cls,
        dev_doc: "DevDocument | None",
        stories_doc: "StoriesDocument | None" = None,
        srd_doc: "SRDDocument | None" = None,
        fsd_doc: "FSDDocument | None" = None,
        brd_doc: "BRDDocument | None" = None,
        card_bodies: dict[str, Any] | None = None,
        dev_ref: str | None = None,
        stories_ref: str | None = None,
        srd_ref: str | None = None,
        fsd_ref: str | None = None,
        brd_ref: str | None = None,
    ) -> "QAContext":
        """Build QAContext from accepted prior SDLC artifacts.

        Each layer is best-effort — absence of any prior artifact is tolerated and
        surfaces as a gap in gap_log (not a build failure).

        Fallback detection:
          needs_br_kb_fallback=True  when BRD.business_rules is empty (handler fetches card_bodies)
          needs_int_kb_fallback=True when SRD.integration_design is empty
        """
        allowed: set[str] = set()

        # ── Workspace ID, requirement, change_class, kb_version from first available ──
        workspace_id = ""
        requirement = ""
        change_class = ""
        kb_version = ""
        for doc in (dev_doc, stories_doc, srd_doc, fsd_doc, brd_doc):
            if doc is not None:
                workspace_id = getattr(doc, "workspace_id", "") or workspace_id
                requirement = getattr(doc, "requirement", "") or requirement
                change_class = getattr(doc, "change_class", "") or change_class
                kb_version = getattr(doc, "kb_version", "") or kb_version
                if workspace_id and requirement and kb_version:
                    break

        # ── Stories data ───────────────────────────────────────────────────────
        story_rows = []
        stories_source = "absent"
        if stories_doc is not None:
            story_rows = list(stories_doc.stories or [])
            stories_source = "prior_artifact"
            for row in story_rows:
                for ref in getattr(row, "source_refs", []):
                    if _is_kb_id(ref):
                        allowed.add(ref)

        # ── FSD screen specs ───────────────────────────────────────────────────
        fsd_screens = []
        fsd_source = "absent"
        if fsd_doc is not None:
            fsd_screens = list(fsd_doc.screen_specs or [])
            fsd_source = "prior_artifact"
            for scr in fsd_screens:
                if _is_kb_id(scr.id):
                    allowed.add(scr.id)

        # ── BRD business rules ─────────────────────────────────────────────────
        brd_rules = []
        brd_source = "prior_artifact"
        br_ref_ids: list[str] = []
        needs_br_kb_fallback = False
        if brd_doc is not None:
            brd_rules = list(brd_doc.business_rules or [])
            for rule in brd_rules:
                if _is_kb_id(rule.id):
                    allowed.add(rule.id)
            # Collect BR-* IDs from brd.references for KB fallback if needed
            for ref in (brd_doc.references or []):
                rid = getattr(ref, "id", "") or ""
                if rid.startswith("BR-") and rid not in br_ref_ids:
                    br_ref_ids.append(rid)
                    allowed.add(rid)
            if not brd_rules:
                # BRD has no business_rules → flag for KB card_bodies fallback
                needs_br_kb_fallback = True
                brd_source = "kb_direct"

        # ── SRD data ───────────────────────────────────────────────────────────
        srd_seq_diagrams = []
        srd_int_design = []
        srd_source = "absent"
        int_ref_ids: list[str] = []
        needs_int_kb_fallback = False
        if srd_doc is not None:
            srd_seq_diagrams = list(srd_doc.sequence_diagrams or [])
            srd_int_design = list(srd_doc.integration_design or [])
            srd_source = "prior_artifact"
            for intg in srd_int_design:
                if _is_kb_id(intg.id):
                    allowed.add(intg.id)
            # Collect INT-* IDs from srd.references for KB fallback if needed
            for ref in (srd_doc.references or []):
                rid = getattr(ref, "id", "") or ""
                if rid.startswith(("INT-", "API-")) and rid not in int_ref_ids:
                    int_ref_ids.append(rid)
                    allowed.add(rid)
            if not srd_int_design:
                needs_int_kb_fallback = True

        # ── Dev impl_tasks ─────────────────────────────────────────────────────
        impl_tasks = []
        dev_source = "absent"
        if dev_doc is not None:
            impl_tasks = list(dev_doc.implementation_plan or [])
            dev_source = "prior_artifact"
            for task in impl_tasks:
                for kid in getattr(task, "kb_ids", []):
                    if _is_kb_id(kid):
                        allowed.add(kid)
                for scr in getattr(task, "screens_to_update", []):
                    if _is_kb_id(scr):
                        allowed.add(scr)

        return cls(
            workspace_id=workspace_id,
            requirement=requirement,
            change_class=change_class,
            kb_version=kb_version,
            story_rows=story_rows,
            stories_source=stories_source,
            fsd_screens=fsd_screens,
            fsd_source=fsd_source,
            brd_rules=brd_rules,
            brd_source=brd_source,
            br_ref_ids=br_ref_ids,
            srd_seq_diagrams=srd_seq_diagrams,
            srd_int_design=srd_int_design,
            srd_source=srd_source,
            int_ref_ids=int_ref_ids,
            impl_tasks=impl_tasks,
            dev_source=dev_source,
            card_bodies=card_bodies or {},
            allowed_ids=allowed,
            needs_br_kb_fallback=needs_br_kb_fallback,
            needs_int_kb_fallback=needs_int_kb_fallback,
            dev_ref=dev_ref,
            stories_ref=stories_ref,
            srd_ref=srd_ref,
            fsd_ref=fsd_ref,
            brd_ref=brd_ref,
        )

    def format_for_llm(self) -> str:
        """Human-readable context block for the ReAct QA agent prompt."""
        lines = [
            f"## REQUIREMENT\n{self.requirement}",
            f"\n## CHANGE CLASSIFICATION\n{self.change_class}",
            f"\n## DATA SOURCES",
            f"  Stories:  {self.stories_source} ({len(self.story_rows)} stories)",
            f"  FSD:      {self.fsd_source} ({len(self.fsd_screens)} screen specs)",
            f"  BRD:      {self.brd_source} ({len(self.brd_rules)} rules)",
            f"  SRD:      {self.srd_source} ({len(self.srd_seq_diagrams)} sequences, "
            f"{len(self.srd_int_design)} integrations)",
            f"  Dev:      {self.dev_source} ({len(self.impl_tasks)} impl tasks)",
        ]

        # List QA-BLOCKED stories (for test_scope LLM context)
        blocked = [
            row for row in self.story_rows
            if not (getattr(row, "ac_given", "") or "").strip()
            or not (getattr(row, "ac_when", "") or "").strip()
            or not (getattr(row, "ac_then", "") or "").strip()
        ]
        if blocked:
            block_list = "\n".join(
                f"  {getattr(r, 'story_id', '?')} — {getattr(r, 'title', '')[:60]}"
                for r in blocked[:5]
            )
            lines.append(f"\n## QA-BLOCKED STORIES (Gherkin missing — {len(blocked)} total)\n{block_list}")

        if self.story_rows:
            sample = self.story_rows[:3]
            stories_list = "\n".join(
                f"  [{getattr(r, 'story_id', '?')}] {getattr(r, 'title', '')[:60]}"
                for r in sample
            )
            lines.append(f"\n## USER STORIES (sample — {len(self.story_rows)} total)\n{stories_list}")

        return "\n".join(lines)
