"""Deterministic Stories section builders — no LLM required.

All builders are pure functions. No function raises. Cyclomatic complexity ≤ 21 per function.

Grounding modes (class-aware — fixed 2026-08-08):
  CONTENT     (Enhancement / Derived)  — 2+ significant words from card prose in field text
  CONTEXTUAL  (New / Migration)        — structural check: at least one cited card exists

WRONG_CONTEXT is NEVER emitted for New/Migration stories — future-state prose cannot be
expected to word-overlap with as-is KB content. Only STUB (no citation at all) is actionable
for New/Migration.

"""

from __future__ import annotations

import re
from typing import Any

from app.config.settings import settings
from app.lifecycle.stages.analysis.schema import ImpactCitation
from app.lifecycle.stages.fsd.schema import Stub
from app.lifecycle.stages.stories.match.context import CONTEXTUAL_CLASSES, StoriesContext
from app.lifecycle.stages.stories.schema import (
    POINTS_BY_CLASS,
    SPLIT_THRESHOLD,
    StoryACRow,
    StoryRow,
)

# The 7 prose fields grounded per story.
_PROSE_FIELDS = ("title", "as_a", "i_want", "so_that", "ac_given", "ac_when", "ac_then")

# Role-only values that don't need word-overlap even in CONTENT mode — per-LOB, from config
# (empty when unset → the bypass simply never triggers; grounding still works via content check).
_ROLE_VALUES = frozenset(r.lower() for r in settings.LOB_KNOWN_ROLES)

# Link type by change class — xpf DERIVES_FROM_RULE pattern.
_LINK_FOR_CLASS: dict[str, str] = {
    "New": "IMPLEMENTS",
    "Enhancement": "IMPLEMENTS",
    "Derived": "DERIVES_FROM_RULE",
}


# ---------------------------------------------------------------------------
# Per-field grounding (Q3 — class-aware)
# ---------------------------------------------------------------------------

def ground_story_field(
    field_text: str,
    kb_ids: list[str],
    card_bodies: dict[str, dict[str, Any]],
    change_class: str = "New",
) -> str:
    """Class-aware per-field grounding check.

    CONTEXTUAL mode (New / Migration):
      The KB holds as-is state; story describes future state. Word-overlap is meaningless.
      Checks only: does at least one cited card exist in card_bodies?
      Returns "OK_CONTEXTUAL" if yes, "STUB" if no.

    CONTENT mode (Enhancement / Derived):
      Story text should echo KB content the requirement modifies.
      Checks 2+ significant content words (len ≥ 4) overlap between card prose and field text.
      Returns "OK", "WRONG_CONTEXT".

    Both modes return "STUB" when kb_ids is empty or field_text is too short (< 5 chars).
    WRONG_CONTEXT is NEVER returned for New/Migration.
    """
    if not kb_ids:
        return "STUB"
    if not field_text or len(field_text.strip()) < 5:
        return "STUB"

    if change_class in CONTEXTUAL_CLASSES:
        # CONTEXTUAL: structural check — at least one cited card resolves in card_bodies.
        for kid in kb_ids:
            if card_bodies.get(kid):
                return "OK_CONTEXTUAL"
        # Citations present but none resolved → no KB context at all.
        return "STUB"

    # CONTENT mode (Enhancement / Derived).
    # Role-only values are always OK — no content overlap needed for actor fields.
    if field_text.strip().lower() in _ROLE_VALUES:
        return "OK"

    for kid in kb_ids:
        card = card_bodies.get(kid, {})
        prose = (card.get("prose") or card.get("text_en") or "").lower()
        if not prose:
            continue
        card_words = {w for w in prose.split() if len(w) >= 4}
        field_words = set(field_text.lower().split())
        if len(card_words & field_words) >= 2:
            return "OK"

    return "WRONG_CONTEXT"


def ground_story_fields(
    stories: list[StoryRow],
    card_bodies: dict[str, dict[str, Any]],
) -> list[StoryRow]:
    """Ground all 7 prose fields for every story. Passes change_class to the selector.

    Mutates ``story.field_ground`` and sets ``story.stub_marker`` on FABRICATED verdict.
    Never raises.
    """
    for story in stories:
        kb_ids = story.source_refs
        for f in _PROSE_FIELDS:
            val = getattr(story, f, "")
            verdict = ground_story_field(val, kb_ids, card_bodies, story.change_class)
            story.field_ground[f] = verdict
            if verdict == "FABRICATED" and story.stub_marker is None:
                story.stub_marker = f"FABRICATED:{f}"
    return stories


def compute_grounding_score(stories: list[StoryRow]) -> float:
    """grounding_score = grounded_fields / total_prose_fields.

    Both "OK" (content) and "OK_CONTEXTUAL" (structural) count as grounded.
    Honest across mixed-class backlogs (New + Enhancement in same workspace).
    """
    if not stories:
        return 1.0
    total = 0
    grounded = 0
    for s in stories:
        for v in s.field_ground.values():
            total += 1
            if v in ("OK", "OK_CONTEXTUAL"):
                grounded += 1
    return round(grounded / total, 4) if total else 1.0


# ---------------------------------------------------------------------------
# Skeleton builder — one stub StoryRow per FR card
# ---------------------------------------------------------------------------

_KB_ID_RE = re.compile(r"^[A-Z][A-Z0-9]*-")
"""KB card IDs start with an uppercase prefix followed by a hyphen (e.g. BR-, FR-, SYS-).
Artifact IDs (e.g. art-ec09026b12d3 or bare hex strings) never start with uppercase."""

# Workflow hint map: SCR card ID segment → human-readable process name for stub text.
_SCR_WORKFLOW_HINTS: dict[str, str] = {
    "-AU-RN-": "Auto Renewal",
    "-AU-EN-": "Auto Endorsement",
    "-AU-NB-": "Auto New Business",
    "-AUW-RN-": "Corporate Auto Renewal",
    "-AUW-EN-": "Corporate Auto Endorsement",
    "-AUW-NB-": "Corporate Auto New Business",
}


def _is_kb_id(value: str) -> bool:
    """Return True only for valid KB card IDs (uppercase prefix + hyphen).

    Filters out DB artifact IDs that may appear in brd.references due to how
    fsd_ref/analysis_ref are surfaced in the BRD reference list.
    """
    return bool(value and _KB_ID_RE.match(value))


def _scr_workflow_hint(card_id: str) -> str:
    """Return a workflow label for a SCR-* card based on its ID segment, or empty string."""
    upper = card_id.upper()
    for segment, label in _SCR_WORKFLOW_HINTS.items():
        if segment in upper:
            return label
    return ""


def build_stories_skeleton(ctx: StoriesContext) -> list[StoryRow]:
    """One stub StoryRow per FR card in the context (genlite: FR-first story seeding).

    For SCR-only workspaces (no FR cards), generates a small number of stubs — one per
    DISTINCT SCR workflow type (Renewal, Endorsement, New Business) rather than one per
    screen. The LLM determines the actual story count from the REQUIREMENT; excess skeleton
    rows are replaced by LLM output matched by source_ref or index.

    For other non-FR workspaces (New/Migration), falls back to one story per matched card.
    When matched is also empty, returns an empty list (cite-or-abstain).

    Defensive: skips any citation whose id is not a valid KB card ID.
    """
    if ctx.fr_matched:
        seeds = ctx.fr_matched
    elif ctx.scr_matched:
        # SCR-only: pick one representative card per workflow type as the skeleton seed.
        # The LLM writes stories for CHANGES (from the REQUIREMENT), not one per screen.
        # Use deduplication by workflow hint so we get at most one stub per process group.
        seen_workflows: set[str] = set()
        scr_seeds = []
        for c in ctx.scr_matched:
            hint = _scr_workflow_hint(c.id) or "Other"
            if hint not in seen_workflows:
                seen_workflows.add(hint)
                scr_seeds.append(c)
        # Always include at least one seed so the assembler has a row to enrich.
        seeds = scr_seeds if scr_seeds else ctx.scr_matched[:1]
    else:
        seeds = ctx.matched
    # Default actor: first matched role label, or the per-LOB fallback from config (not hardcoded).
    default_actor = ctx.role_matched[0].label if ctx.role_matched else settings.LOB_DEFAULT_ACTOR
    change_class = ctx.change_class if ctx.change_class in ("New", "Enhancement", "Derived") else "New"

    rows: list[StoryRow] = []
    for c in seeds:
        if not _is_kb_id(c.id):
            # Skip artifact IDs / non-KB references that leaked into brd.references.
            continue
        card_label = (c.label or c.id).strip()

        # For SCR-* cards, derive workflow context from the ID suffix so the stub is
        # meaningful and gives the LLM better seed text to enrich.
        is_scr = c.id.upper().startswith("SCR-")
        workflow = _scr_workflow_hint(c.id) if is_scr else ""

        if is_scr and workflow:
            i_want = (
                f"to implement the {card_label} as part of the {workflow} workflow in AIG Connect, "
                f"including all required fields, validations, and navigation flows for this screen"
            )
            so_that = (
                f"agents can complete the {workflow.lower()} process through the AIG Connect portal "
                f"without errors, with all screen fields correctly validated and saved"
            )
        else:
            i_want = f"implement {card_label.lower()}"
            so_that = "[business value — to be defined by BA]"

        rows.append(StoryRow(
            # Stub prose derived from KB card so the CSV is readable without LLM enrichment.
            # The LLM (run_react) overwrites these when a model is available.
            title=f"{card_label}",
            as_a=default_actor,
            i_want=i_want,
            so_that=so_that,
            ac_given="",   # empty → _detect_gherkin_gaps flags missing KB prose
            ac_when="",
            ac_then="",
            source_refs=[c.id],
            source_locus=c.source_locus,
            source_type="kb_explicit",
            change_class=change_class,
        ))
    return rows


# ---------------------------------------------------------------------------
# ID assignment — stable deterministic format
# ---------------------------------------------------------------------------

def assign_story_ids(stories: list[StoryRow], workspace_id: str) -> list[StoryRow]:
    """Assign ``{prefix}-NNN`` story IDs, where the prefix is the per-LOB value from config."""
    prefix = settings.LOB_STORY_ID_PREFIX
    for i, story in enumerate(stories, start=1):
        story.story_id = f"{prefix}-{i:03d}"
    return stories


# ---------------------------------------------------------------------------
# Points assignment — Q1 (fixed by class)
# ---------------------------------------------------------------------------

def assign_points(stories: list[StoryRow]) -> list[StoryRow]:
    """Assign story points by change_class (Q1 decision — fixed for v1).

    New=8 / Enhancement=5 / Derived=3. Sets points_rationale and points_action.
    """
    for story in stories:
        pts = POINTS_BY_CLASS.get(story.change_class, 8)
        story.points = pts
        action = "Decompose" if pts >= SPLIT_THRESHOLD else "Accept"
        story.points_action = action
        story.points_rationale = (
            f"{story.change_class} requirement ({pts} pts) — "
            + ("full AC set needed" if story.change_class == "New" else
               "delta behavior change" if story.change_class == "Enhancement" else
               "derived from existing business rule")
        )
    return stories


# ---------------------------------------------------------------------------
# Oversized guard — genlite split-threshold
# ---------------------------------------------------------------------------

def flag_oversized_stories(stories: list[StoryRow]) -> list[StoryRow]:
    """Stories with points > SPLIT_THRESHOLD get stub_marker='SPLIT-REQUIRED' (genlite).

    With fixed points (New=8 == threshold), this fires only when points are manually
    raised by BA in v2. Kept for forward-compatibility.
    """
    for story in stories:
        if story.points > SPLIT_THRESHOLD and story.stub_marker is None:
            story.stub_marker = "SPLIT-REQUIRED"
            story.points_action = "Decompose"
    return stories


# ---------------------------------------------------------------------------
# Dedup guard — IMAD pattern (same source_refs set → DUPLICATE)
# ---------------------------------------------------------------------------

def _dedup_stories(stories: list[StoryRow]) -> list[StoryRow]:
    """If two stories share the exact same source_refs set, mark the second DUPLICATE.

    Adopted from IMAD spec_agent.py cross-domain dedup pattern.
    The first story is kept as-is; subsequent duplicates get stub_marker='DUPLICATE'.
    """
    seen: set[frozenset[str]] = set()
    for story in stories:
        key = frozenset(story.source_refs)
        if key in seen:
            if story.stub_marker is None:
                story.stub_marker = "DUPLICATE"
        else:
            seen.add(key)
    return stories


# ---------------------------------------------------------------------------
# FR coverage check — genlite validator
# ---------------------------------------------------------------------------

def check_story_coverage(
    stories: list[StoryRow],
    ctx: StoriesContext,
) -> list[Stub]:
    """Every FR card in context must have ≥1 story citing it (genlite coverage validator).

    Returns HIGH-priority Stub items for uncovered FR cards.
    For New/Migration workspaces with zero FR cards, this check is a no-op.
    """
    covered: set[str] = {rid for s in stories for rid in s.source_refs}
    stubs: list[Stub] = []
    for fr in ctx.fr_matched:
        if fr.id not in covered:
            stubs.append(Stub(
                id=f"cov-{fr.id}",
                section="Open Items",
                description=f"FR card {fr.id} ({fr.label[:60]}) has no story — coverage gap",
                marker="BA-TODO",
                priority="High",
            ))
    return stubs


# ---------------------------------------------------------------------------
# Traceability — AC-3 matrix
# ---------------------------------------------------------------------------

def build_traceability(stories: list[StoryRow]) -> list[StoryACRow]:
    """One AC-3 row per story. link_type by change_class (xpf DERIVES_FROM_RULE pattern)."""
    rows: list[StoryACRow] = []
    for story in stories:
        link = _LINK_FOR_CLASS.get(story.change_class, "IMPLEMENTS")
        rows.append(StoryACRow(
            story_id=story.story_id,
            kb_ids=list(story.source_refs),
            source_loci=[story.source_locus] if story.source_locus else [],
            link_type=link,
            diagram_ref="",  # blank at S4; Architecture fills in v2
        ))
    return rows


# ---------------------------------------------------------------------------
# References — pass-through from BRD
# ---------------------------------------------------------------------------

def build_references(ctx: StoriesContext) -> list[ImpactCitation]:
    """Pass BRD citations through as the Stories references section."""
    return list(ctx.matched)


# ---------------------------------------------------------------------------
# Gherkin AC gap detector — cite-or-abstain enforcement
# ---------------------------------------------------------------------------

# Catches stubs the LLM echoed back unchanged (e.g. "[precondition for X]").
_BRACKET_PAT = re.compile(r"^\[.*\]$")


def _is_empty_or_bracket(val: str) -> bool:
    """True when a Gherkin field has no real content."""
    stripped = val.strip()
    return not stripped or bool(_BRACKET_PAT.match(stripped))


def detect_gherkin_gaps(
    stories: list[StoryRow],
    card_bodies: dict[str, Any],
) -> list[Stub]:
    """Step 2b — detect Gherkin AC fields that are empty after LLM enrichment.

    Never fabricates content.  Surfaces two gap kinds as High-priority open_items:
      - KB card has no prose  → "run /kb-build to enrich this card"
      - KB card has prose but LLM left the field empty → "review KB card and re-run stories"

    Side-effect: sets story.stub_marker = "AC-STUB" on any affected story so the UI
    renders a clear warning badge — never silently passes a story with empty Gherkin.
    """
    stubs: list[Stub] = []
    for story in stories:
        missing = [
            f for f in ("ac_given", "ac_when", "ac_then")
            if _is_empty_or_bracket(getattr(story, f, ""))
        ]
        if not missing:
            continue

        story.stub_marker = "AC-STUB"

        kb_id = story.source_refs[0] if story.source_refs else None
        card = card_bodies.get(kb_id, {}) if kb_id else {}
        has_prose = bool(str(card.get("prose") or card.get("text_en") or "").strip())
        id_label = kb_id or "(no source_ref)"

        for fld in missing:
            if not has_prose:
                desc = (
                    f"Story {story.story_id!r} .{fld}: KB card {id_label} has no prose — "
                    "run /kb-build to enrich this card before regenerating stories"
                )
            else:
                desc = (
                    f"Story {story.story_id!r} .{fld}: KB card {id_label} has prose but LLM "
                    "did not ground this AC field — review the KB card content and re-run stories"
                )
            stubs.append(Stub(description=desc, marker="AC-STUB", priority="High"))

    return stubs
