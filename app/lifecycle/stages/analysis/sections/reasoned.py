"""LLM-derived (reasoned) sections — each whitelist-filtered to real ids; deterministic fallbacks.

The ReAct agent proposes; these builders keep only ids in the retrieved whitelist (04 §11b) and fall
back to a deterministic form when the agent is absent or a field is missing (never fabricate).
"""

from __future__ import annotations

import re
from typing import Any

from app.lifecycle.stages.analysis.schema import (
    AffectedNode,
    Classification,
    Conflict,
    ImpactCitation,
    Modification,
    Risk,
    ScopeDiff,
    ScopeItem,
)
from app.lifecycle.stages.analysis.sections.deterministic import impact_note


def _scope_note(summary: str | None, via: list[str], kind: str) -> str:
    """Edge-aware scope note (why it's in scope), prefixed with the grounded card summary when present."""
    base = impact_note(via, kind)
    return f"{summary} · {base}" if summary else base


def _s(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def build_classification(
    matched: list[ImpactCitation],
    affected: list[AffectedNode],
    intent_confidence: float | None = None,
) -> Classification:
    """The change CLASS stays deterministic (B1 anti-flip-flop: derived from the graph-matched context,
    never the OPEN ReAct agent). The CONFIDENCE, however, reflects real evidence: the structured
    intent-analysis confidence when available (single-turn extraction — stable, not the flip-floppy ReAct
    agent), else a deterministic floor that scales with match evidence instead of a flat placeholder."""
    if not matched:
        conf = intent_confidence if intent_confidence is not None else 0.3
        return Classification(
            change_class="New",
            confidence=round(conf, 2),
            rationale="No matching KB concept found — net-new.",
        )
    if intent_confidence is not None:
        conf = intent_confidence
    else:
        conf = min(0.9, 0.55 + 0.05 * min(len(matched), 5))  # evidence-scaled deterministic fallback
    return Classification(
        change_class="Enhancement",
        confidence=round(conf, 2),
        rationale=f"Matches {len(matched)} existing KB card(s); impact touches {len(affected)}.",
    )


def build_scope(matched: list[ImpactCitation], affected: list[AffectedNode]) -> ScopeDiff:
    """Deterministic (B1): scope is the graph-matched (enhancement) + graph-reached (existing) sets,
    not the LLM agent's re-authored partition."""
    return ScopeDiff(
        enhancement=[
            ScopeItem(id=c.id, label=c.label, kind=c.kind, note=_scope_note(c.summary, [], c.kind)) for c in matched
        ],
        existing=[
            ScopeItem(id=n.id, label=n.label, kind=n.kind, note=_scope_note(n.summary, n.via, n.kind))
            for n in affected
        ],
    )


def build_modifications(matched: list[ImpactCitation]) -> list[Modification]:
    """Deterministic (B1): one modification per directly-matched card (as-is grounded, to-be at design),
    derived from context — not the LLM agent's re-authored list."""
    return [
        Modification(
            id=c.id,
            label=c.label,
            kind=c.kind,
            before=c.summary,  # as-is: what the card describes today (grounded)
            after=None,
            note="directly touched by the requirement — to-be defined during design",
        )
        for c in matched
    ]


def build_risks(enriched: dict[str, Any] | None, allowed_ids: set[str]) -> list[Risk]:
    out: list[Risk] = []
    for entry in ((enriched or {}).get("risks") or [])[:12]:
        if not isinstance(entry, dict):
            continue
        desc = _s(entry.get("description"))
        if not desc:
            continue
        sev = str(entry.get("severity") or "medium").lower()
        sev = sev if sev in ("low", "medium", "high") else "medium"
        aff = [i for i in (entry.get("affected_ids") or []) if i in allowed_ids]
        out.append(Risk(severity=sev, description=desc, affected_ids=aff, mitigation=_s(entry.get("mitigation"))))
    return out


def build_drift_conflicts(enriched: dict[str, Any] | None, allowed_ids: set[str]) -> list[Conflict]:
    out: list[Conflict] = []
    for entry in ((enriched or {}).get("drift_conflicts") or [])[:10]:
        if not isinstance(entry, dict):
            continue
        detail = _s(entry.get("detail"))
        subjects = [i for i in (entry.get("subject_ids") or []) if i in allowed_ids]
        if detail and subjects:
            out.append(Conflict(kind="drift", subject_ids=subjects, detail=detail, severity="medium"))
    return out


def build_narrative(
    enriched: dict[str, Any] | None, matched: list[ImpactCitation], affected: list[AffectedNode]
) -> str:
    if enriched and _s(enriched.get("narrative")):
        return str(enriched["narrative"]).strip()
    return fallback_narrative(matched, affected)


def fallback_narrative(matched: list[ImpactCitation], affected: list[AffectedNode]) -> str:
    ids = ", ".join(f"[{c.id}]" for c in matched) or "no existing cards"
    return (
        f"The requirement touches {len(matched)} existing KB card(s): {ids}. "
        f"The impact set spans {len(affected)} connected node(s) reached via typed edges."
    )


def build_gaps(enriched: dict[str, Any] | None) -> list[str]:
    raw = (enriched or {}).get("gaps")
    if isinstance(raw, list):
        return [str(g).strip() for g in raw if str(g).strip()][:10]
    return []


# UPPER_SNAKE identifiers (data tables / schema objects, e.g. WEB_RECEIPTS) named in the requirement.
_DATA_OBJ_RE = re.compile(r"\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\b")
# External integration touchpoints, e.g. "email provider API", "payment gateway", "X webhook".
_INTEG_RE = re.compile(r"\b([A-Za-z][\w ./-]{2,30}?)\s+(API|SDK|gateway|webhook|endpoint)\b", re.IGNORECASE)


def detect_requirement_gaps(requirement: str, matched: list[Any], affected: list[Any]) -> list[str]:
    """Surface data/integration touchpoints NAMED in the requirement but NOT reflected in the impacted
    business areas (which are UI/business by design — data tables + integrations are filtered out).

    Domain-neutral: pattern-detected from the requirement text, never a hardcoded term. Each becomes an
    SME-confirm gap so the analysis is honest about layers it does not model, instead of silently
    dropping them. Skips anything already covered by a matched/affected label or id.
    """
    if not requirement:
        return []
    covered = " ".join(
        f"{getattr(x, 'label', '') or ''} {getattr(x, 'id', '') or ''}"
        for x in list(matched) + list(affected)
    ).lower()
    gaps: list[str] = []
    seen: set[str] = set()

    for obj in _DATA_OBJ_RE.findall(requirement):
        key = obj.lower()
        if key in seen or key in covered:
            continue
        seen.add(key)
        gaps.append(
            f"The requirement names a data object '{obj}' (likely a table/schema change) that is not "
            f"reflected in the impacted business areas — confirm the data-model change and migration/"
            f"backfill scope with an SME."
        )

    for name, kind in _INTEG_RE.findall(requirement):
        label = f"{name.strip()} {kind.upper()}"
        key = label.lower()
        if key in seen or name.strip().lower() in covered:
            continue
        seen.add(key)
        gaps.append(
            f"The requirement involves an external integration ('{label}') that is not reflected in the "
            f"impacted business areas — confirm the integration contract, ownership and failure/retry "
            f"handling with an SME."
        )

    return gaps[:5]


def build_effort(
    classification: Classification, matched: list[ImpactCitation], affected: list[AffectedNode]
) -> tuple[str, str | None]:
    """Deterministic (B1): effort is a monotonic function of the impact-set size + change class, NOT the
    LLM agent's re-authored T-shirt size (which flip-flopped run-to-run). Bands are stable step functions."""
    # Direct impact (matched) counts full; graph-walk ripple (affected) is a secondary neighbourhood,
    # not direct work → half weight, so a large ripple fan-out doesn't inflate effort on its own.
    # Aug 17, 2026: Filter to exclude SYS (systems are dependencies, not implementable changes)
    # This prevents false positives from P1/P2 semantic search from inflating effort estimates
    direct_change_kinds = {"SCR", "CMP", "API", "INT"}  # Implementable kinds only
    primary_kinds = {getattr(c, "kind", "") for c in matched if getattr(c, "kind", "") in direct_change_kinds}
    affected_direct = [n for n in affected if n.kind in direct_change_kinds]
    n = len(matched) + len(affected_direct) // 2
    if n == 0:
        size = "S"          # net-new with no graph anchors — small, provisional
    elif n <= 2:
        size = "S"
    elif n <= 6:
        size = "M"
    elif n <= 14:
        size = "L"
    else:
        size = "XL"
    if classification.change_class == "New" and size == "S":
        size = "M"          # net-new work skews larger than a tiny impact set alone implies
    # A single-kind Enhancement (e.g. the same field added across several screens) is repetitive
    # breadth, not cross-layer complexity → one band smaller than the raw count implies.
    if classification.change_class == "Enhancement" and len(primary_kinds) <= 1 and size in ("L", "XL"):
        size = {"XL": "L", "L": "M"}[size]
    rationale = (
        f"Derived from impact size: {len(matched)} matched card(s) + {len(affected_direct)} affected implementable node(s) "
        f"(ripple, half-weight); breadth={len(primary_kinds)} kind(s); class={classification.change_class}. "
        f"(Systems excluded from count as they are dependencies, not direct changes)"
    )
    return size, rationale
