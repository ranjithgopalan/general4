"""assemble_stories() — 15-step deterministic + LLM pipeline for the Stories stage.

Pipeline (mirrors assemble_fsd / assemble_brd pattern):
  1.  build_stories_skeleton       one stub StoryRow per FR card
  2.  run_react                    LLM enrichment + retry-with-merge
  3.  apply_story_enrichment       merge LLM onto skeleton (whitelist-filtered)
  4.  refine_derived_class         Enhancement + all BR-* sources → Derived (Q4)
  5.  assign_story_ids             STR-JAUTO-NNN format
  6.  assign_points                New=8 / Enhancement=5 / Derived=3 (Q1)
  7.  flag_oversized_stories       points > threshold → SPLIT-REQUIRED (genlite)
  8.  ground_story_fields          7 fields × N stories → field_ground dict (Q3)
  9.  _dedup_stories               same source_refs → DUPLICATE (IMAD)
  10. check_story_coverage         every FR must have ≥1 story (genlite)
  11. build_open_items             blind-spot + coverage + SPLIT stubs
  12. build_traceability           AC-3 rows
  13. build_references             pass-through from BRD
  14. compute_grounding_score      grounded / total fields (both OK modes count)
  15. return StoriesDocument
"""

from __future__ import annotations

from app.lifecycle.common.handler_base import _now_iso
from app.lifecycle.stages.stories.match.context import StoriesContext
from app.lifecycle.stages.stories.schema import StoriesDocument
from app.lifecycle.stages.stories.sections.deterministic import (
    _dedup_stories,
    assign_points,
    assign_story_ids,
    build_references,
    build_stories_skeleton,
    build_traceability,
    check_story_coverage,
    compute_grounding_score,
    detect_gherkin_gaps,
    flag_oversized_stories,
    ground_story_fields,
)
from app.lifecycle.stages.stories.sections.reasoned import (
    apply_story_enrichment,
    build_open_items,
    refine_derived_class,
)


async def assemble_stories(
    ctx: StoriesContext,
    *,
    workspace_id: str,
    persona: str,
    enriched: dict | None = None,
    kb_version: str | None = None,
) -> StoriesDocument:
    """Run the full 15-step Stories assembly pipeline.

    ``enriched`` is the parsed LLM output dict from run_react(). When None (model=None
    in tests or LLM failure), all prose fields stay as stubs — grounding will return STUB
    for all fields, and open_items will have NO-KB-CITATION entries for BA to fill.

    Returns a StoriesDocument. Never raises — any internal error leaves fields at defaults.
    """
    if enriched is None:
        enriched = {}

    resolved_kb_version = kb_version or ctx.kb_version

    # Early-exit: no KB cards at all → abstained (cite-or-abstain).
    if not ctx.matched:
        return StoriesDocument(
            workspace_id=workspace_id,
            brd_ref=ctx.brd_ref,
            analysis_ref=ctx.analysis_ref,
            kb_version=resolved_kb_version,
            persona=persona,
            requirement=ctx.requirement,
            change_class=ctx.change_class,
            grounding_score=0.0,
            stub_count=0,
            abstained=True,
            generated_at=_now_iso(),
        )

    # Step 1 — skeleton.
    stories = build_stories_skeleton(ctx)

    # Step 3 — apply LLM enrichment (step 2 = run_react, called by handler before assemble).
    stories = apply_story_enrichment(stories, enriched, ctx.allowed_ids)

    # Step 2b — cite-or-abstain: detect Gherkin AC fields still empty after enrichment.
    # Surfaces which KB cards need prose enrichment (/kb-build); never fabricates content.
    gherkin_gap_stubs = detect_gherkin_gaps(stories, ctx.card_bodies)

    # Step 4 — Derived class refinement (Q4).
    stories = refine_derived_class(stories, ctx)

    # Step 5 — assign stable IDs.
    stories = assign_story_ids(stories, workspace_id)

    # Step 6 — assign points by class (Q1).
    stories = assign_points(stories)

    # Step 7 — flag oversized (genlite).
    stories = flag_oversized_stories(stories)

    # Step 8 — per-field grounding (Q3, class-aware).
    stories = ground_story_fields(stories, ctx.card_bodies)

    # Step 9 — dedup (IMAD).
    stories = _dedup_stories(stories)

    # Step 10 — FR coverage check (genlite).
    coverage_stubs = check_story_coverage(stories, ctx)

    # Step 11 — open items (Gherkin KB gaps first, then blind-spot + coverage + SPLIT).
    open_items = build_open_items(enriched, ctx, stories)
    open_items = open_items + [s for s in coverage_stubs if s not in open_items]
    # Gherkin AC gaps prepended (High priority — BA must enrich KB before re-running).
    open_items = gherkin_gap_stubs + open_items

    # Step 12 — AC-3 traceability.
    traceability = build_traceability(stories)

    # Step 13 — references.
    references = build_references(ctx)

    # Step 14 — grounding score.
    grounding_score = compute_grounding_score(stories)

    # Step 15 — stub count.
    stub_count = sum(1 for s in stories if s.stub_marker is not None)

    # Section basis (provenance summary — genlite Import 3 pattern).
    section_basis = _compute_section_basis(stories, ctx)

    return StoriesDocument(
        workspace_id=workspace_id,
        brd_ref=ctx.brd_ref,
        analysis_ref=ctx.analysis_ref,
        kb_version=resolved_kb_version,
        persona=persona,
        requirement=ctx.requirement,
        change_class=ctx.change_class,
        grounding_score=grounding_score,
        stub_count=stub_count,
        stories=stories,
        traceability=traceability,
        open_items=open_items,
        references=references,
        section_basis=section_basis,
        abstained=False,
        generated_at=_now_iso(),
    )


def _compute_section_basis(stories, ctx: StoriesContext) -> dict[str, str]:
    """Per-section provenance summary (genlite Import 3 pattern)."""
    kb_explicit = sum(1 for s in stories if s.source_type == "kb_explicit")
    kb_inferred = sum(1 for s in stories if s.source_type == "kb_inferred")
    stub_count = sum(1 for s in stories if s.source_type == "stub")
    new_count = sum(1 for s in stories if s.change_class == "New")
    enh_count = sum(1 for s in stories if s.change_class == "Enhancement")
    der_count = sum(1 for s in stories if s.change_class == "Derived")
    return {
        "stories": (
            f"KB-explicit: {kb_explicit} | KB-inferred: {kb_inferred} | stub: {stub_count}"
        ),
        "by_class": f"New: {new_count} | Enhancement: {enh_count} | Derived: {der_count}",
        "grounding_mode": (
            "CONTEXTUAL (structural)" if ctx.change_class in ("New", "Migration")
            else "CONTENT (word-overlap)"
        ),
    }
