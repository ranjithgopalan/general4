"""Reasoned Stories section builders — apply LLM enrichment and post-assembly checks.

All builders are pure functions (no async, no DB). No function raises. Cyclomatic ≤ 21.

The LLM produces a dict via run_react(). Builders here apply it onto the deterministic
skeleton. Nothing here calls the LLM directly.

"""

from __future__ import annotations

from app.lifecycle.stages.analysis.schema import ImpactCitation
from app.lifecycle.stages.fsd.schema import Stub
from app.lifecycle.stages.stories.match.context import CONTEXTUAL_CLASSES, StoriesContext
from app.lifecycle.stages.stories.schema import POINTS_BY_CLASS, StoryRow


# ---------------------------------------------------------------------------
# Apply LLM enrichment onto skeleton rows
# ---------------------------------------------------------------------------

def apply_story_enrichment(
    stories: list[StoryRow],
    enriched: dict,
    allowed_ids: set[str],
) -> list[StoryRow]:
    """Merge LLM-produced story fields onto the deterministic skeleton rows.

    Matching order: source_ref lookup → index fallback.
    Only whitelisted KB IDs are accepted in source_refs updates.

    For SCR-seeded workspaces the LLM generates stories for CHANGES (not one per screen),
    so it may produce more stories than the skeleton has rows. Any LLM story whose
    source_ref does not match a skeleton row AND whose index exceeds the skeleton length
    is appended as a new StoryRow so it is not silently dropped.
    """
    llm_stories: list[dict] = enriched.get("stories", [])

    # Build a lookup from source_ref → enriched dict for fast matching.
    llm_by_ref: dict[str, dict] = {}
    for item in llm_stories:
        ref = item.get("source_ref", "")
        if ref:
            llm_by_ref[ref] = item

    matched_indices: set[int] = set()

    for i, story in enumerate(stories):
        primary_ref = story.source_refs[0] if story.source_refs else ""
        by_ref = llm_by_ref.get(primary_ref)
        by_idx = llm_stories[i] if i < len(llm_stories) else {}
        enrichment = by_ref or by_idx
        if not enrichment:
            continue
        # Track which LLM story was consumed so we can append the rest.
        if by_ref:
            try:
                matched_indices.add(llm_stories.index(by_ref))
            except ValueError:
                pass
        elif by_idx:
            matched_indices.add(i)

        for prose_field in ("title", "as_a", "i_want", "so_that", "ac_given", "ac_when", "ac_then", "points_rationale"):
            val = enrichment.get(prose_field, "")
            if val and isinstance(val, str) and val.strip():
                setattr(story, prose_field, val.strip())

        priority = enrichment.get("priority", "")
        if priority in ("High", "Medium", "Low"):
            story.priority = priority

        cls = enrichment.get("change_class", "")
        if cls in ("New", "Enhancement", "Derived"):
            story.change_class = cls

        extra_refs = [r for r in enrichment.get("extra_source_refs", []) if r in allowed_ids]
        for ref in extra_refs:
            if ref not in story.source_refs:
                story.source_refs.append(ref)

    # Append LLM stories that had no matching skeleton row (SCR change-story overflow).
    for j, llm_item in enumerate(llm_stories):
        if j in matched_indices:
            continue
        ref = llm_item.get("source_ref", "")
        # Only append if the source_ref is a whitelisted KB ID (grounding guard).
        if not ref or ref not in allowed_ids:
            continue
        new_row = StoryRow(
            title=llm_item.get("title", ref),
            as_a=llm_item.get("as_a", ""),
            i_want=llm_item.get("i_want", ""),
            so_that=llm_item.get("so_that", ""),
            ac_given=llm_item.get("ac_given", ""),
            ac_when=llm_item.get("ac_when", ""),
            ac_then=llm_item.get("ac_then", ""),
            points_rationale=llm_item.get("points_rationale", ""),
            priority=llm_item.get("priority", "Medium") if llm_item.get("priority") in ("High", "Medium", "Low") else "Medium",
            change_class=llm_item.get("change_class", "Enhancement") if llm_item.get("change_class") in ("New", "Enhancement", "Derived") else "Enhancement",
            source_refs=[ref],
            source_type="kb_explicit",
        )
        extra_refs = [r for r in llm_item.get("extra_source_refs", []) if r in allowed_ids]
        for er in extra_refs:
            if er not in new_row.source_refs:
                new_row.source_refs.append(er)
        stories.append(new_row)

    return stories


# ---------------------------------------------------------------------------
# Derived class refinement — Q4 (IMAD CAP/DAN analogy)
# ---------------------------------------------------------------------------

def refine_derived_class(stories: list[StoryRow], ctx: StoriesContext) -> list[StoryRow]:
    """Promote Enhancement → Derived where behavior is logically implied from existing rules.

    IMAD analogy: DAN = "differs, apply delta" (Enhancement); CAP = "common, preserve"
    (Derived — behavior already implied by the rule, no new code required).

    Heuristic (deterministic — no LLM). A story becomes Derived when ALL of:
      1. change_class is currently "Enhancement"
      2. All source_refs are BR-* cards (no FR-* cards in the citation list)
      3. The workspace change_class is not itself "New" (New workspaces should stay New)

    Derived stories get:
      - change_class = "Derived"
      - points = POINTS_BY_CLASS["Derived"] (3)
      - points_rationale updated
      - link_type = "DERIVES_FROM_RULE" in AC-3 rows (set in build_traceability)

    For New/Migration workspaces: refinement is skipped entirely — the workspace-level
    change_class overrides; stories stay as-is.
    """
    if ctx.change_class in CONTEXTUAL_CLASSES:
        return stories  # New/Migration: no Derived refinement

    fr_ids: set[str] = {c.id for c in ctx.fr_matched}

    for story in stories:
        if story.change_class != "Enhancement":
            continue
        if not story.source_refs:
            continue
        # If any source_ref is an FR card → Enhancement stands (explicit delta stated).
        has_fr = any(rid in fr_ids for rid in story.source_refs)
        if not has_fr:
            story.change_class = "Derived"
            story.points = POINTS_BY_CLASS["Derived"]
            story.points_rationale = (
                "Derived from business rule (3 pts) — implied behavior, no explicit FR delta"
            )
            story.points_action = "Accept"

    return stories


# ---------------------------------------------------------------------------
# Open items builder — xpf blind-spot + genlite coverage + IMAD ambiguity stubs
# ---------------------------------------------------------------------------

def build_open_items(
    enriched: dict,
    ctx: StoriesContext,
    stories: list[StoryRow],
) -> list[Stub]:
    """Build open items from:
    1. Agent-proposed items (from enriched["open_items"])
    2. xpf blind-spot: STUB per field (always actionable regardless of class)
       WRONG_CONTEXT suppressed for New/Migration (expected — future-state prose)
       FABRICATED always flagged
    3. genlite: out-of-whitelist KB IDs in agent open items → BA-TODO stubs
    4. genlite: SPLIT-REQUIRED story stubs
    5. genlite: FR coverage check stubs (passed in from deterministic.check_story_coverage)
    """
    stubs: list[Stub] = []
    is_contextual = ctx.change_class in CONTEXTUAL_CLASSES

    # 1. Agent-proposed open items.
    for item in enriched.get("open_items", []):
        desc = item.get("description", "")
        if not desc:
            continue
        stubs.append(Stub(
            description=desc,
            marker=item.get("marker", "BA-TODO"),
            priority=item.get("priority", "Medium"),
        ))

    # 2. xpf blind-spot: field-level grounding gaps.
    for story in stories:
        for fld, verdict in story.field_ground.items():
            if verdict == "STUB":
                # Always actionable — no KB citation at all.
                pri = "High" if fld in ("ac_given", "ac_when", "ac_then") else "Medium"
                stubs.append(Stub(
                    description=(
                        f"Story {story.story_id}.{fld} has no KB context card — add source_ref"
                    ),
                    marker="NO-KB-CITATION",
                    priority=pri,
                ))
            elif verdict == "WRONG_CONTEXT" and not is_contextual:
                # Enhancement/Derived only: prose doesn't match cited KB content.
                stubs.append(Stub(
                    description=(
                        f"Story {story.story_id}.{fld} has no KB grounding — BA review required"
                    ),
                    marker="NO-KB-CITATION",
                    priority="Medium",
                ))
            elif verdict == "FABRICATED":
                stubs.append(Stub(
                    description=(
                        f"Story {story.story_id}.{fld} failed re-anchor (FABRICATED) — must be rewritten"
                    ),
                    marker="FABRICATED",
                    priority="High",
                ))

    # 3. genlite: KB IDs referenced in agent open items but not in allowed_ids.
    # ONLY flag tokens that START with KB ID prefixes (FR-, BR-, SYS-, etc.).
    # Prevents false positives from parsing narrative prose (e.g., "free-text", "mainframe-side").
    allowed = ctx.allowed_ids
    kb_id_prefixes = ("FR-", "BR-", "SYS-", "INT-", "ENT-", "PROC-", "WF-", "SCR-", "TERM-", "DOM-", "ROLE-", "CMP-", "API-", "REL-", "STM-", "SEQ-")
    for item in enriched.get("open_items", []):
        desc = item.get("description", "")
        for token in desc.split():
            clean = token.strip("[](),.")
            # Only flag if it starts with a KB ID prefix AND is not in allowed_ids.
            if any(clean.startswith(prefix) for prefix in kb_id_prefixes) and clean not in allowed:
                stubs.append(Stub(
                    description=f"KB ID '{clean}' referenced in open item but not in allowed whitelist",
                    marker="BA-TODO",
                    priority="High",
                ))

    # 4. genlite: SPLIT-REQUIRED story stubs.
    for story in stories:
        if story.stub_marker == "SPLIT-REQUIRED":
            stubs.append(Stub(
                description=(
                    f"Story {story.story_id} has {story.points} pts — "
                    "recommend splitting before sprint"
                ),
                marker="SPLIT-REQUIRED",
                priority="Medium",
            ))

    return _dedup_stubs(stubs)


def _dedup_stubs(stubs: list[Stub]) -> list[Stub]:
    """Remove duplicate stubs (same description prefix — first 60 chars)."""
    seen: set[str] = set()
    out: list[Stub] = []
    for s in stubs:
        key = s.description[:60]
        if key not in seen:
            seen.add(key)
            out.append(s)
    return out


# ---------------------------------------------------------------------------
# Fallback summary — when LLM is unavailable (model=None in tests)
# ---------------------------------------------------------------------------

def fallback_story_summary(ctx: StoriesContext) -> str:
    """Deterministic fallback when ReAct agent is absent or returns empty."""
    fr_labels = ", ".join(c.label for c in ctx.fr_matched[:3])
    br_count = len(ctx.br_matched)
    return (
        f"Stories for {ctx.change_class} requirement: {ctx.requirement[:80]}. "
        f"Grounded in {len(ctx.fr_matched)} FR cards ({fr_labels or 'none'}) "
        f"and {br_count} business rules."
    )
