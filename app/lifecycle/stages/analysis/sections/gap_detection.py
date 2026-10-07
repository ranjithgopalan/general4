"""Deterministic gap detection + clarification merging.

Identifies gaps in impact analysis that users can clarify.
Merges clarifications back into requirement for rerun.
"""

from __future__ import annotations

import uuid
from typing import Any

from app.lifecycle.stages.analysis.schema import ImpactAnalysis
from app.lifecycle.stages.analysis.schema_gaps import Gap, MergePreview
from app.lifecycle.stages.analysis.match.context import ContextPackage


def detect_gaps(
    analysis: ImpactAnalysis,
    context: ContextPackage | None = None,
) -> list[Gap]:
    """Deterministically detect gaps in impact analysis.

    Five gap types:
    1. ambiguous-requirement — requirement too short/unclear
    2. missing-kb-coverage — feature area not in KB
    3. conflict-unresolved — DISPUTED KB rules
    4. coverage-gap — zero matched cards
    5. scope-unclear — new/enhancement/existing ambiguous

    Returns: sorted by severity (high → low)
    """
    gaps: list[Gap] = []

    # Gap 1: Ambiguous requirement (too short)
    if analysis.requirement and len(analysis.requirement.strip()) < 20:
        gaps.append(
            Gap(
                gap_id=uuid.uuid4().hex,
                gap_type="ambiguous-requirement",
                description=f"Requirement too brief ({len(analysis.requirement)} chars). "
                "Unclear scope or intent.",
                severity="high",
                can_clarify=True,
                clarification_type="text",
                resolution_hint="Provide more context: WHO is affected? WHAT exactly changes? WHY?",
            )
        )

    # Gap 2: Zero matched cards
    if not analysis.matched:
        gaps.append(
            Gap(
                gap_id=uuid.uuid4().hex,
                gap_type="coverage-gap",
                description="No KB cards matched the requirement. "
                "The change may be outside the scope of the KB or use different terminology.",
                severity="high",
                can_clarify=True,
                clarification_type="text",
                resolution_hint="Rephrase requirement using KB terminology (e.g., 'AU policy', 'underwriting', 'screen')",
            )
        )

    # Gap 3: KB coverage blindspots
    if analysis.coverage:
        for blindspot in analysis.coverage.blindspots:
            gaps.append(
                Gap(
                    gap_id=uuid.uuid4().hex,
                    gap_type="missing-kb-coverage",
                    description=f"Feature area [{blindspot}] not covered in KB. "
                    "It may be affected but not explicitly documented.",
                    affected_ids=[blindspot],
                    severity="medium",
                    can_clarify=False,
                    clarification_type=None,
                    resolution_hint="This is a KB gap. Feature area needs to be added to KB.",
                )
            )

    # Gap 4: Unresolved conflicts
    for conflict in analysis.conflicts:
        if conflict.status == "DISPUTED":
            gaps.append(
                Gap(
                    gap_id=uuid.uuid4().hex,
                    gap_type="conflict-unresolved",
                    description=f"Conflict: {conflict.detail}. "
                    f"IDs involved: {', '.join(conflict.subject_ids)}",
                    affected_ids=conflict.subject_ids,
                    severity=conflict.severity or "medium",
                    can_clarify=True,
                    clarification_type="choice",
                    resolution_hint=f"Choose which applies to your change: {' or '.join(conflict.subject_ids)}",
                )
            )

    # Gap 5: Scope ambiguity
    if (
        not analysis.scope.new
        and not analysis.scope.enhancement
        and analysis.classification.change_class not in ["Existing", "Derived"]
    ):
        gaps.append(
            Gap(
                gap_id=uuid.uuid4().hex,
                gap_type="scope-unclear",
                description="Requirement classification is uncertain. "
                "Not clear if this is a new feature, enhancement, or existing.",
                severity="medium",
                can_clarify=True,
                clarification_type="choice",
                resolution_hint="Clarify: Is this entirely NEW, an ENHANCEMENT of existing feature, or EXISTING feature behavior?",
            )
        )

    # Sort by severity: high → medium → low
    severity_order = {"high": 0, "medium": 1, "low": 2}
    gaps.sort(key=lambda g: severity_order.get(g.severity, 3))

    return gaps


def merge_clarifications_into_requirement(
    original_requirement: str,
    clarifications: list[dict[str, Any]] | list[tuple[str, str, str]],
) -> str:
    """Merge clarifications into a single, cohesive requirement.

    Strategy:
    - Text clarifications expand scope/context
    - Choice clarifications resolve ambiguities
    - Result: ONE coherent requirement (not tacked-on list)

    Args:
        original_requirement: Original requirement text
        clarifications: List of clarifications (dict or tuple)
                       Dict keys: gap_type, clarification_type, user_input
                       Tuple: (gap_type, clarification_type, user_input)

    Returns:
        Merged requirement text
    """
    sections = [original_requirement.strip()]

    # Group by gap type
    by_type: dict[str, list[Any]] = {}
    for clarif in clarifications:
        if isinstance(clarif, dict):
            gap_type = clarif.get("gap_type", "")
            user_input = clarif.get("user_input", "")
        else:
            gap_type, _, user_input = clarif

        if gap_type not in by_type:
            by_type[gap_type] = []
        by_type[gap_type].append(user_input)

    # 1. Ambiguous requirement clarifications → expand scope/context
    if "ambiguous-requirement" in by_type:
        details = by_type["ambiguous-requirement"]
        if details:
            context_lines = [f"• {d}" for d in details if d]
            if context_lines:
                sections.append("Additional context:\n" + "\n".join(context_lines))

    # 2. Conflict resolution → specify which applies
    if "conflict-unresolved" in by_type:
        for choice in by_type["conflict-unresolved"]:
            if choice:
                sections.append(f"Applies to: {choice}")

    # 3. Scope clarification → specify type
    if "scope-unclear" in by_type:
        for scope in by_type["scope-unclear"]:
            if scope:
                sections.append(f"Change type: {scope}")

    # 4. Coverage gap clarifications → note what's missing
    if "coverage-gap" in by_type:
        for note in by_type["coverage-gap"]:
            if note:
                sections.append(f"Note: {note}")

    return "\n\n".join(sections).strip()


def build_merge_preview(
    original_requirement: str,
    merged_requirement: str,
    iteration: int,
    clarifications_count: int,
) -> MergePreview:
    """Build a preview of the merged requirement for user confirmation."""
    return MergePreview(
        iteration=iteration,
        original_requirement=original_requirement,
        merged_requirement=merged_requirement,
        clarifications_count=clarifications_count,
        message=f"Merging {clarifications_count} clarifications into requirement for iteration {iteration}...",
    )
