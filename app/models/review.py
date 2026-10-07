"""Pre-Activation Review models (docs/25) — the version-scoped gap ledger a reviewer clears before ACTIVE."""

from __future__ import annotations

from pydantic import BaseModel, Field

# gap types (see docs/25 §4)
GAP_TYPES = ("isolated_node", "ungrounded_card", "gate_failure", "review_queue", "contradiction", "completeness")
SEVERITIES = ("blocking", "soft", "info")


DISPOSITIONS = ("OPEN", "FALSE_POSITIVE", "RESOLVED", "SME_CONFIRMED")


class GapItem(BaseModel):
    """One reviewable gap in a STAGING/ACTIVE build (+ its disposition in Phase 2)."""

    gap_id: str = Field(description="stable id, e.g. 'ungrounded_card:BR-JAUTO-005'")
    type: str = Field(description="one of GAP_TYPES")
    severity: str = Field(default="soft", description="blocking | soft | info")
    title: str
    detail: str = ""
    source_ref: str | None = Field(default=None, description="card/node id, file locus, or review-queue anchor")
    # Phase 2 disposition (merged from kb_review_items)
    status: str = Field(default="OPEN", description="OPEN | FALSE_POSITIVE | RESOLVED | SME_CONFIRMED")
    note: str | None = None
    disposed_by: str | None = None
    artifact_ref: str | None = Field(  # Phase 3 — a workspace/FSD/SRD/evidence that resolves the gap
        default=None, description="artifact linked to resolve this gap (docs/25 Phase 3 #11)"
    )


class KbReview(BaseModel):
    """Aggregated pre-activation review for a kb_version, with dispositions + the promote gate."""

    kb_version: str
    status: str = ""
    summary: dict[str, int] = Field(default_factory=dict, description="counts: total/blocking/soft/info + open_*")
    gaps: list[GapItem] = Field(default_factory=list)
    blocking_open: int = Field(default=0, description="OPEN gaps with severity=blocking (gate the promote)")
    promotable: bool = Field(default=True, description="True iff no OPEN blocking gap remains")
