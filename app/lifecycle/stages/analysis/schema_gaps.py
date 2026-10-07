"""Gap detection models and schema (docs/IMPACT_ANALYSIS_CLARIFICATION_MERGED.md)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class Gap(BaseModel):
    """An open question or gap detected in impact analysis."""

    gap_id: str
    gap_type: str = Field(
        description="Type of gap",
        examples=["ambiguous-requirement", "missing-kb-coverage", "conflict-unresolved", "coverage-gap", "scope-unclear"]
    )
    description: str
    affected_ids: list[str] = Field(default_factory=list, description="KB IDs involved (if any)")
    severity: str = Field(
        description="low | medium | high",
        examples=["low", "medium", "high"]
    )
    resolution_hint: str | None = None
    can_clarify: bool = True
    clarification_type: str | None = Field(
        description="text | choice | reference",
        examples=["text", "choice", "reference"],
        default=None
    )


class Clarification(BaseModel):
    """User clarification for a gap."""

    clarification_id: str
    workspace_id: str
    analysis_iteration: int
    gap_id: str
    gap_type: str
    user_input: str | dict  # Text or choice value
    clarification_type: str  # "text" | "choice" | "reference"
    created_at: str
    created_by: str | None = None


class MergePreview(BaseModel):
    """Preview of merged requirement before rerun."""

    iteration: int
    original_requirement: str
    merged_requirement: str
    clarifications_count: int
    message: str | None = None


class GapsIterationResult(BaseModel):
    """Result of gap detection after rerun iteration."""

    iteration: int
    previous_gap_count: int
    current_gap_count: int
    resolved_count: int
    gaps: list[Gap]
