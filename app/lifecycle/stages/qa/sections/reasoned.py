"""Reasoned (LLM-enriched) QA section builders.

Only two sections use LLM reasoning:
  test_scope    — "what is under test, environment, exclusions" (≤300w; soft-cap; never truncates data)
  regression_enrichment — agent may suggest additional regression areas beyond deterministic list

Fallbacks (used when model=None or agent returns no usable output):
  test_scope fallback  → summary built deterministically from ctx source counts
  regression fallback  → keep the deterministic regression_scope as-is

All LLM output passes through the same whitelist gate as other stages: any KB ID in the
prose that is NOT in ctx.allowed_ids → flagged as BA-TODO stub (Import 3 pattern).
"""

from __future__ import annotations

from typing import Any

from app.lifecycle.stages.qa.match.context import QAContext
from app.lifecycle.stages.qa.schema import QAGap

# Soft word cap for test_scope — mirrors BRD Import 1 pattern.
_TEST_SCOPE_CAP = 300


def _word_count(text: str) -> int:
    return len(text.split())


def _trim_to_cap(text: str, cap: int) -> str:
    """Trim to last complete sentence within word budget — never mid-sentence cut."""
    if _word_count(text) <= cap:
        return text
    words = text.split()
    trimmed = " ".join(words[:cap])
    # Walk back to last sentence end
    for sep in (".", "!", "?"):
        idx = trimmed.rfind(sep)
        if idx > 0:
            return trimmed[: idx + 1].strip()
    return trimmed.strip()


def build_test_scope(enriched: dict[str, Any] | None, ctx: QAContext) -> str:
    """Build the test_scope narrative (LLM-driven; deterministic fallback).

    LLM target: 3-5 sentences covering:
      - What is being tested (the change / requirement)
      - Which test sections are populated vs gapped
      - What is explicitly excluded from this test plan
      - Test environment assumptions (UAT / staging)

    Soft cap: 300w.  Deterministic fallback fires when enriched is None or empty.
    """
    if enriched:
        scope_text = (enriched.get("test_scope") or "").strip()
        if scope_text and _word_count(scope_text) >= 10:
            return _trim_to_cap(scope_text, _TEST_SCOPE_CAP)

    # Deterministic fallback
    parts = [f"This test plan covers the change: '{ctx.requirement[:150]}'."]

    sections_populated: list[str] = []
    sections_gapped: list[str] = []

    if ctx.stories_source == "prior_artifact":
        sections_populated.append(f"{len(ctx.story_rows)} user story test case(s)")
    else:
        sections_gapped.append("story test cases (StoriesDocument absent)")

    if ctx.fsd_source == "prior_artifact":
        sections_populated.append(f"{len(ctx.fsd_screens)} screen validation(s)")
    else:
        sections_gapped.append("screen validations (FSD absent)")

    if ctx.brd_rules:
        sections_populated.append(f"{len(ctx.brd_rules)} business rule test(s)")
    elif ctx.br_ref_ids:
        sections_populated.append(f"{len(ctx.br_ref_ids)} business rule test(s) via KB fallback")
    else:
        sections_gapped.append("business rule tests (BRD absent)")

    if ctx.srd_seq_diagrams:
        sections_populated.append(f"{len(ctx.srd_seq_diagrams)} workflow scenario(s)")
    else:
        sections_gapped.append("workflow scenarios (SRD sequence_diagrams absent)")

    if ctx.srd_int_design:
        sections_populated.append(f"{len(ctx.srd_int_design)} integration test(s)")
    elif ctx.int_ref_ids:
        sections_populated.append(f"{len(ctx.int_ref_ids)} integration test(s) via KB fallback")
    else:
        sections_gapped.append("integration tests (SRD integration_design absent)")

    if sections_populated:
        parts.append("In scope: " + "; ".join(sections_populated) + ".")
    if sections_gapped:
        parts.append(
            "Out of scope / gapped: " + "; ".join(sections_gapped) + ". "
            "See gap_log for required upstream fixes."
        )

    parts.append(
        "Environment assumption: test execution targets the UAT/staging environment. "
        "Production deployment is out of scope for this test plan."
    )
    return " ".join(parts)


def build_gap_log_enrichment(
    enriched: dict[str, Any] | None,
    existing_gaps: list[QAGap],
) -> list[QAGap]:
    """Optionally append LLM-identified additional gaps to the deterministic gap_log.

    LLM may surface gaps not detectable deterministically (e.g., cross-cutting concerns,
    missing negative test coverage for specific rules). Validated against existing gap_ids
    to prevent duplicates. Never replaces deterministic gaps.
    """
    if not enriched:
        return []

    extra_items = enriched.get("additional_gaps") or []
    if not extra_items:
        return []

    existing_ids = {g.gap_id for g in existing_gaps}
    added: list[QAGap] = []

    for item in extra_items[:5]:  # cap at 5 LLM-sourced gaps
        if not isinstance(item, dict):
            continue
        description = (item.get("description") or "").strip()
        if not description:
            continue
        gap_type = (item.get("gap_type") or "kb_gap").strip()
        priority = (item.get("priority") or "Medium").strip()
        action = (item.get("action") or "Review and address before QA sign-off.").strip()
        gap_id = f"gap-llm-{len(existing_ids) + len(added)}"
        if gap_id not in existing_ids:
            added.append(QAGap(
                gap_id=gap_id,
                description=description,
                gap_type=gap_type,
                source="llm_identified",
                priority=priority,
                action=action,
            ))

    return added


def apply_regression_enrichment(
    existing_scope: list[str],
    enriched: dict[str, Any] | None,
) -> list[str]:
    """Append LLM-suggested regression items to the deterministic regression_scope.

    LLM may suggest cross-cutting regression areas (e.g., "re-test all screens after
    ESB configuration change"). Deduplicated and capped — never replaces deterministic items.
    """
    if not enriched:
        return existing_scope

    extra = enriched.get("regression_items") or []
    if not extra or not isinstance(extra, list):
        return existing_scope

    existing_set = set(existing_scope)
    appended = list(existing_scope)
    for item in extra[:5]:
        text = (str(item) or "").strip()
        if text and text not in existing_set:
            appended.append(text)
            existing_set.add(text)

    return appended[:20]  # total cap
