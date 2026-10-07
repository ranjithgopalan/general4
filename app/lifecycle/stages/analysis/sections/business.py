"""Business view of the ImpactAnalysis — SAME sections as the technical doc, plain-language content.

The business document mirrors the technical section structure 1:1 (docs/22); only the content under
each section is rewritten for business readers: KB/workspace ids, source loci, edge labels, code, and
IT jargon are stripped/relabelled, insurance/domain terms kept. ``build_business_view`` turns the
technical ``ImpactAnalysis`` into a ``BusinessImpactView`` — the ONE structure feeding both the UI
(JSON) and the business ``.docx`` — so the two never drift.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from app.lifecycle.stages.analysis.schema import ImpactAnalysis

_GLOSSARY_PATH = Path(__file__).resolve().parents[2].parent / "templates" / "business_glossary.json"
_EFFORT_WORDS = {"XS": "extra-small", "S": "small", "M": "medium", "L": "large", "XL": "extra-large"}
_MAX_EXCERPT = 240

# KB ids / workspace ids must never appear in a business view (SCR-JAUTO-AU-EN-001, BR-JAUTO-002, ws-abc123).
_KB_ID_RE = re.compile(r"\b[A-Z]{2,4}-JAUTO-[A-Z0-9-]+|\b[A-Z]{2,4}-\d{3,}\b|\bws-[0-9a-f-]{6,}\b")


@lru_cache(maxsize=1)
def _glossary() -> dict[str, Any]:
    return json.loads(_GLOSSARY_PATH.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _jargon_sorted() -> list[tuple[str, str]]:
    """Jargon pairs sorted by phrase length desc so multi-word terms replace before their sub-tokens."""
    return sorted(_glossary().get("jargon", {}).items(), key=lambda kv: -len(kv[0]))


def business_kinds() -> set[str]:
    return set(_glossary().get("business_kinds", []))


def is_business_kind(kind: str | None) -> bool:
    return (kind or "") in business_kinds()


def kind_term(kind: str | None) -> str:
    """Plain-language name for a node kind (e.g. PROC -> 'business process', SYS -> 'system')."""
    return _glossary().get("kind_terms", {}).get(kind or "", "item")


def business_edge_phrase(via: list[str]) -> str:
    """Plain phrase for how a node is affected, from its edge labels (no FEEDS_INTO/CALLS jargon)."""
    phrases = _glossary().get("edge_phrases", {})
    out: list[str] = []
    for e in via or []:
        p = phrases.get((e or "").strip())
        if p and p not in out:
            out.append(p)
    return "; ".join(out) if out else "directly involved in this change"


def effort_word(effort: str | None) -> str:
    return _EFFORT_WORDS.get((effort or "M").upper(), "medium")


def _strip_kb_ids(text: str | None) -> str:
    return _KB_ID_RE.sub("", text or "")


def _friendly_label(label: str | None) -> str | None:
    """A label with KB ids removed — ``None`` if nothing readable is left (the label WAS an id)."""
    return _strip_kb_ids(label).strip(" -:·") or None


# Internal delivery/PMO mechanics that must NOT surface in the business stakeholder view
# (parallel-workspace churn, node-modification internals). These are dropped from risks/gaps.
_MECHANICS_RX = re.compile(
    r"parallel workspace|in-flight|simultaneously|being modified in|open in \w+ (parallel|other)|"
    r"workspace owner|sibling-overlap|open change on|another workspace",
    re.IGNORECASE,
)


def is_delivery_mechanics(text: str | None) -> bool:
    """True for internal delivery/PMO mechanics (not stakeholder-facing) — filtered from the business view."""
    return bool(text and _MECHANICS_RX.search(text))


def business_scrub(text: str | None, label: str | None = None) -> str | None:
    """Strip ids + IT flow-detail + relabel jargon → a plain business clause. Insurance terms kept.

    1. Remove KB/ws ids.  2. For structured screen cards (``label: X - kind: …``) pull the name.
    3. Drop flow detail after the first arrow, then take the first list item / sentence.
    4. If the body is code, fall back to the label.  5. Relabel IT jargon; drop paths; collapse dup words.
    """
    if not text:
        return _friendly_label(label)
    s = _strip_kb_ids(" ".join(text.split()))
    screen = re.search(r"label:\s*(.+?)(?:\s+-\s+\w+:|$)", s)  # "label: Change Menu Screen - kind: Screen …"
    if screen:
        s = screen.group(1)
    for arrow in ("→", "->"):
        if arrow in s:
            s = s.split(arrow)[0]
            break
    for sep in (" - ", ". "):
        if sep in s:
            s = s.split(sep)[0]
            break
    if re.search(r"[{}]|&&|=>|\);|\|\|", s):  # card body is code, not prose → show the readable label only
        return _friendly_label(label)
    for term, repl in _jargon_sorted():
        s = re.sub(re.escape(term), repl, s, flags=re.IGNORECASE)
    s = re.sub(r"\S*/\S+", "", s)  # drop path-like tokens (/checkItemApplication, /v1/print)
    s = re.sub(r"\[\s*\]", "", s)  # drop id-brackets emptied by _strip_kb_ids
    s = re.sub(r"\(\s*[,\s]*\)", "", s)  # drop emptied "( , , )" id lists
    s = re.sub(r"\b(\w+)(\s+\1\b)+", r"\1", s, flags=re.IGNORECASE)  # collapse "core core" → "core"
    s = " ".join(s.split()).strip(" :;-,·")
    return s[:_MAX_EXCERPT] or _friendly_label(label)


def business_summary_of(node: Any) -> str | None:
    """Plain summary for a node — the scrubbed technical card excerpt (``summary``)."""
    return business_scrub(getattr(node, "summary", None), getattr(node, "label", None))


_TECH_KINDS = {"SYS", "INT", "CMP", "API", "SEQ"}


def _relabel(text: str) -> str:
    """Relabel IT jargon in a short label + collapse duplicates (no sentence-trimming)."""
    text = re.sub(r"\s*(?:→|->)\s*", " to ", text)  # "A → B" → "A to B"
    for term, repl in _jargon_sorted():
        text = re.sub(re.escape(term), repl, text, flags=re.IGNORECASE)
    parts, keys = [], []  # dedup "/"-separated repeats: "the integration layer / integration layer"
    for p in (x.strip() for x in text.split("/")):
        k = re.sub(r"^the\s+", "", p.lower())
        if p and k not in keys:
            keys.append(k)
            parts.append(p)
    text = " / ".join(parts) if parts else text
    text = re.sub(r"\b(\w+)(\s+\1\b)+", r"\1", text, flags=re.IGNORECASE)  # "core core" → "core"
    return " ".join(text.split()).strip(" :;-,·/")


def clean_name(label: str | None, kind: str = "") -> str:
    """Business-safe display name from a raw label string: KB ids stripped, IT jargon relabelled.

    For technical kinds (SYS/INT/CMP/API/SEQ) the technical annotation (``(PAS …)`` / ``/ IIB``) is
    dropped. Used by callers (e.g. the FSD business view) whose items don't fit the node shape.
    """
    friendly = _friendly_label(label)
    if not friendly:
        return kind_term(kind).title()
    if (kind or "") in _TECH_KINDS:
        # Same as business_label: strip annotation, keep canonical name, no jargon relabelling.
        friendly = friendly.split(" (")[0].split(" / ")[0]
        return " ".join(friendly.split()).strip(" :;-,·/") or friendly
    return _relabel(friendly) or friendly


def business_label(node: Any) -> str:
    """Business-safe display name: KB ids stripped, IT jargon relabelled. For technical kinds
    (SYS/INT/CMP/API/SEQ) the technical annotation (``(PAS …)``) is dropped before relabelling."""
    friendly = _friendly_label(getattr(node, "label", None))
    if not friendly:
        summ = business_summary_of(node)  # screen cards: the name lives in the summary
        return summ.split(" — ")[0][:60] if summ else kind_term(getattr(node, "kind", None)).title()
    if (getattr(node, "kind", "") or "") in _TECH_KINDS:
        # Strip the KB technical annotation "(PAS — Policy Admin System)" before jargon relabelling
        # so system names like "PEGA" and "Fujitsu Mainframe" appear in plain language.
        friendly = friendly.split(" (")[0]
    return _relabel(friendly) or friendly


# ── Business view models — mirror the technical sections 1:1, content in plain language ─────────


class BizRow(BaseModel):
    """A labelled item under a section (no KB id) — ``name (kind)`` with an optional plain ``detail``."""

    name: str
    kind: str = ""  # plain-language kind term
    detail: str | None = None


class BizScope(BaseModel):
    new: list[BizRow] = Field(default_factory=list)
    enhancement: list[BizRow] = Field(default_factory=list)
    existing: list[BizRow] = Field(default_factory=list)


class BizLayerGroup(BaseModel):
    """Three-layer architectural view of the impact — UI / Backend Service / Data."""
    ui: list[BizRow] = Field(default_factory=list)
    service: list[BizRow] = Field(default_factory=list)
    data: list[BizRow] = Field(default_factory=list)


class BizBlindspot(BaseModel):
    """A matched card that has no downstream dependencies."""

    name: str
    kind: str = ""
    reason: str = ""


class BizCoverage(BaseModel):
    coverage_pct: float = 0.0
    note: str = ""
    blindspots: list[BizBlindspot] = Field(default_factory=list)


class BusinessImpactView(BaseModel):
    """Plain-language mirror of ImpactAnalysis — same 12 sections, no ids / loci / edges / IT jargon."""

    requirement: str | None = None
    # Three-layer architectural view (populated by build_business_view)
    layer_groups: BizLayerGroup = Field(default_factory=BizLayerGroup)
    # 1. Change Classification
    change_class: str = "Enhancement"
    confidence: float = 0.0
    change_statement: str = ""
    # 2. Summary
    summary: str = ""
    # 3. Scope
    scope: BizScope = Field(default_factory=BizScope)
    # 4. How It Touches the Existing System
    touch_points: list[BizRow] = Field(default_factory=list)
    # 5. What Is Being Modified
    modifications: list[BizRow] = Field(default_factory=list)
    # 6. Downstream Effect
    downstream: list[BizRow] = Field(default_factory=list)
    # 6b. TECHNICAL DETAILS — Full P1/P2 Impact (Not Filtered)
    technical_components: list[BizRow] = Field(default_factory=list)  # P1 semantic search results
    technical_screens: list[BizRow] = Field(default_factory=list)     # P2 semantic search results
    technical_systems: list[BizRow] = Field(default_factory=list)      # Systems always shown
    # 7. Where Changes Are Needed
    change_locations: list[BizRow] = Field(default_factory=list)
    # 8. Conflicts
    conflicts: list[str] = Field(default_factory=list)
    # 9. Coverage & Blind Spots
    coverage: BizCoverage = Field(default_factory=BizCoverage)
    # 10. Risks & Effort
    effort: str = "medium"
    risks: list[str] = Field(default_factory=list)
    # 11. Open Questions & Gaps
    questions: list[str] = Field(default_factory=list)
    # 12. References
    references: list[str] = Field(default_factory=list)
    generated_at: str | None = None


_CHANGE_VERB = {
    "New": "introduces a new",
    "Enhancement": "changes an existing",
    "Existing": "is already covered by an existing",
    "Derived": "implies changes to an existing",
}


def _biz_row(name: str, kind: str, detail: str | None) -> BizRow:
    """A row that drops the detail when it just repeats the name (e.g. screen cards)."""
    if detail and detail.strip().lower() == (name or "").strip().lower():
        detail = None
    return BizRow(name=name, kind=kind, detail=detail)


def build_business_view(a: ImpactAnalysis) -> BusinessImpactView:  # noqa: C901 — 12 parallel section builds, flat
    """Derive the plain-language, id-free business view (same 12 sections) from the ImpactAnalysis."""
    by_id: dict[str, Any] = {}
    for n in (*a.matched, *a.affected, *a.downstream):
        by_id.setdefault(n.id, n)

    def _summary_by_id(node_id: str | None) -> str | None:
        node = by_id.get(node_id) if node_id else None
        return business_summary_of(node) if node is not None else None

    # 2. Summary — clean generated line (never the id-laden narrative fallback).
    names = ", ".join(business_label(c) for c in a.matched[:10])
    summary = (
        f"This change affects {len(a.matched)} area(s): {names}."
        if a.matched
        else "This change does not directly affect any recorded business area."
    )

    # 3. Scope — 3 columns, business labels + card summary (no ids/edge notes).
    def _scope_rows(items: list[Any]) -> list[BizRow]:
        rows: list[BizRow] = []
        for it in items:
            node = by_id.get(it.id) if it.id else None
            name = business_label(node) if node is not None else (_friendly_label(it.label) or kind_term(it.kind).title())
            detail = _summary_by_id(it.id) if is_business_kind(it.kind) else None
            rows.append(_biz_row(name, kind_term(it.kind), detail))
        return rows

    scope = BizScope(
        new=_scope_rows(a.scope.new),
        enhancement=_scope_rows(a.scope.enhancement),
        existing=_scope_rows(a.scope.existing),
    )

    # 4. How it touches — boundary systems/roles in plain terms + plain "how".
    def _touch_detail(tp: Any) -> str | None:
        summ = _summary_by_id(tp.id) if is_business_kind(tp.kind) else None
        rel = (tp.relation or "").strip()
        if rel and rel.lower() != "directly matched":
            phrase = business_edge_phrase([e.strip() for e in rel.split(",")])
            return f"{summ} · {phrase}" if summ else phrase
        return summ or "directly involved in this change"

    touch_points = [_biz_row(business_label(t), kind_term(t.kind), _touch_detail(t)) for t in a.touch_points]
    if not touch_points and a.matched:  # additive/contained change — it touches the target screen itself
        touch_points = [_biz_row(business_label(m), kind_term(m.kind), "the change is applied here") for m in a.matched]

    # 5. What is modified — as-is → to-be, scrubbed.
    def _mod_detail(m: Any) -> str | None:
        if not is_business_kind(m.kind):
            return "Under review — to-be defined during design."
        before = business_scrub(m.before)
        after = business_scrub(m.after or m.note)
        parts = []
        if before:
            parts.append(f"As-is: {before}")
        if after:
            parts.append(f"To-be: {after}")
        return "  ".join(parts) or None

    modifications = [_biz_row(business_label(m), kind_term(m.kind), _mod_detail(m)) for m in a.modifications]

    # 6. Downstream — dependents + plain "why".
    def _down_detail(n: Any) -> str | None:
        summ = business_summary_of(n) if is_business_kind(n.kind) else None
        phrase = business_edge_phrase(n.via)
        return f"{summ} · {phrase}" if summ else phrase

    downstream = [_biz_row(business_label(n), kind_term(n.kind), _down_detail(n)) for n in a.downstream]

    # Layer groups — three-layer architectural view (UI / Service / Data)
    # Short kind codes (SCR, SYS, CMP…) are not in the glossary kind_terms dict, which maps
    # full Neo4j labels (Screen, System, Component…). Normalise before calling kind_term so
    # badges show "screen" / "system" etc. instead of the default "item".
    _SHORT_TO_FULL: dict[str, str] = {
        "SCR": "Screen", "SYS": "System", "CMP": "Component",
        "INT": "Integration", "API": "ApiOp", "WF": "Workflow",
        "PROC": "Process", "BR": "BusinessRule", "FR": "FunctionalReq",
        "ENT": "Entity", "ROLE": "Role",
    }

    def _layer_biz_rows(nodes: list) -> list[BizRow]:
        rows = []
        for n in nodes:
            name = business_label(n) if hasattr(n, "label") else (_friendly_label(getattr(n, "label", None)) or "")
            if not name:
                continue
            raw_kind = getattr(n, "kind", "") or ""
            full_kind = _SHORT_TO_FULL.get(raw_kind.upper(), raw_kind)
            detail = business_summary_of(n) if is_business_kind(full_kind) else None
            rows.append(_biz_row(name, kind_term(full_kind), detail))
        return rows

    layer_groups = BizLayerGroup(
        ui=_layer_biz_rows(getattr(a.layer_groups, "ui", [])),
        service=_layer_biz_rows(getattr(a.layer_groups, "service", [])),
        data=_layer_biz_rows(getattr(a.layer_groups, "data", []) + a.data_entities),
    )

    # 6b. TECHNICAL DETAILS SECTION — Full impact from P1/P2 semantic search ✨
    # Show ALL components, screens, systems found by semantic search (not just Enhancement class).
    # This is separate from the business scope to avoid hiding 87% of technical impact.
    technical_components: list[BizRow] = []
    technical_screens: list[BizRow] = []
    technical_systems: list[BizRow] = []

    # P1: Components found by semantic search (use new technical_components field)
    if hasattr(a, "technical_components") and a.technical_components:
        for c in a.technical_components:
            name = business_label(c)
            detail = business_summary_of(c) if is_business_kind(c.kind) else None
            technical_components.append(_biz_row(name, kind_term(c.kind), detail))

    # P2: Screens found by semantic search (use new technical_screens field)
    if hasattr(a, "technical_screens") and a.technical_screens:
        for s in a.technical_screens:
            name = business_label(s)
            detail = business_summary_of(s) if is_business_kind(s.kind) else None
            technical_screens.append(_biz_row(name, kind_term(s.kind), detail))

    # Systems affected — ALWAYS show (not filtered!) (use new technical_systems field)
    if hasattr(a, "technical_systems") and a.technical_systems:
        for sys in a.technical_systems:
            name = business_label(sys)
            detail = business_summary_of(sys) if is_business_kind(sys.kind) else None
            technical_systems.append(_biz_row(name, kind_term(sys.kind), detail))

    # 7. Where changes are needed — implementable areas (NO source loci / file paths).
    change_locations = [
        _biz_row(business_label(c), kind_term(c.kind), _summary_by_id(c.id) if is_business_kind(c.kind) else None)
        for c in a.change_locations
    ]
    if not change_locations and a.matched:  # contained change — the work happens on the target screen(s)
        change_locations = [_biz_row(business_label(m), kind_term(m.kind), _summary_by_id(m.id)) for m in a.matched]

    # 8. Conflicts — generic, id-free.
    conflicts = (
        [
            f"This change overlaps with {len(a.conflicts)} other in-flight change(s) touching the same "
            "business area(s). Coordinate with the other teams before proceeding."
        ]
        if a.conflicts
        else []
    )

    # 9. Coverage — percentage + blind spots + plain note. A contained change (nothing else affected) has no blind
    # spots → 100% (not a misleading 0%).
    # P1 Fix: Convert enriched Blindspot objects to business-friendly BizBlindspot rows
    blindspots: list[BizBlindspot] = []
    if a.coverage.blindspots:
        for bs in a.coverage.blindspots:
            # Handle both new Blindspot objects and legacy string IDs
            if isinstance(bs, str):
                # Legacy: just ID (shouldn't happen with P1 fix, but be safe)
                blindspots.append(BizBlindspot(name=bs, kind="unknown", reason="Not linked to other areas"))
            else:
                # New: Blindspot object with label, kind, reason
                bs_name = business_label(bs) if hasattr(bs, "label") else (bs.label or bs.id)
                blindspots.append(
                    BizBlindspot(
                        name=bs_name,
                        kind=kind_term(bs.kind),
                        reason=bs.reason or "No dependencies found in the system graph",
                    )
                )

    if a.affected:
        coverage = BizCoverage(
            coverage_pct=a.coverage.coverage_pct,
            note=f"{a.coverage.linked} of {a.coverage.total} impacted areas have a downstream link.",
            blindspots=blindspots,
        )
    else:
        coverage = BizCoverage(
            coverage_pct=100.0,
            note="Contained to the target screen — no other areas are affected.",
            blindspots=blindspots,
        )

    # 12. References — business names of the grounded items (no ids / loci).
    references: list[str] = []
    seen_ref: set[str] = set()
    for r in (a.references or a.matched):
        nm = business_label(r)
        if nm and nm.lower() not in seen_ref:
            seen_ref.add(nm.lower())
            references.append(nm)

    return BusinessImpactView(
        layer_groups=layer_groups,
        requirement=business_scrub(a.requirement),
        change_class=a.classification.change_class,
        confidence=a.classification.confidence,
        change_statement=f"This request {_CHANGE_VERB.get(a.classification.change_class, 'changes an')} business capability.",
        summary=summary,
        scope=scope,
        touch_points=touch_points,
        modifications=modifications,
        downstream=downstream,
        # TECHNICAL DETAILS: Full P1/P2 impact (not filtered by scope class)
        technical_components=technical_components,
        technical_screens=technical_screens,
        technical_systems=technical_systems,
        change_locations=change_locations,
        conflicts=conflicts,
        coverage=coverage,
        effort=effort_word(a.effort),
        # Drop internal delivery mechanics (parallel-workspace churn, node internals) — stakeholder view only.
        risks=[s for s in (business_scrub(x.description) for x in a.risks if not is_delivery_mechanics(x.description)) if s],
        questions=[s for s in (business_scrub(g) for g in a.gaps if not is_delivery_mechanics(g)) if s],
        references=references,
        generated_at=a.generated_at,
    )
