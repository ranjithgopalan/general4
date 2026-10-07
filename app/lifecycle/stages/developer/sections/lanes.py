"""docs/24 §D — first-class developer change specs on the four lanes.

The developer stage used to express "lane" only as a presentational ``stack`` label on code stubs, and
``build_code_stubs`` iterated only CMP-*/API-* cards — so Screens (frontend) and DB entities (db) never
became change specs. This module makes the lanes first-class: one ``ChangeSpec`` per KB item, tying the
IDs to the concrete file(s) to modify (from ``source_locus``) and the as-is→to-be behaviour, grounded in
the SRD carried into ``DevContext``.

Deterministic (no LLM). Each lane builder stays well under the cyclomatic bar; ``build_change_specs``
just concatenates them.
"""

from __future__ import annotations

import re

from app.lifecycle.stages.developer.match.context import DevContext
from app.lifecycle.stages.developer.schema import ChangeSpec, GapStatus, Ownership

# A source_locus is "path §section char:start-end" — grab the leading file path.
_LOCUS_FILE_RE = re.compile(r"^\s*([^\s§]+)")


def _target_files(source_locus: str | None) -> list[str]:
    """Real file to modify from a source_locus (path prefix); [] when no locus (new-file DEV-TODO)."""
    if not source_locus:
        return []
    m = _LOCUS_FILE_RE.match(source_locus)
    return [m.group(1)] if m and "/" in m.group(1) else []


def _grounding(source_locus: str | None) -> tuple[GapStatus, Ownership]:
    """Grounded (has a real file) → direct/HYBRID; otherwise a gap the developer must fill."""
    if source_locus and "/" in source_locus:
        return "direct", "HYBRID"
    return "gap", "DEV-TODO"


def _spec(lane: str, prefix: str, kb_id: str, title: str, locus: str | None,
          extra_ids: list[str] | None = None, to_be: str = "") -> ChangeSpec:
    gap, owner = _grounding(locus)
    return ChangeSpec(
        lane=lane,  # type: ignore[arg-type]
        id=f"{prefix}-{kb_id}",
        title=title,
        kb_ids=[kb_id, *(extra_ids or [])],
        target_files=_target_files(locus),
        as_is=(f"Existing implementation at {locus}" if locus else ""),
        to_be=to_be or "DEV-TODO: implement the change for this item",
        ownership=owner,
        gap_status=gap,
        source_locus=locus,
    )


def _frontend(ctx: DevContext) -> list[ChangeSpec]:
    """One spec per Screen (SCR-*), linking related Angular components."""
    cmp_ids = [c.id for c in ctx.cmp_cards]
    return [
        _spec("frontend", "fe", s.id, f"Screen: {s.label or s.id}", s.source_locus, extra_ids=cmp_ids[:3])
        for s in ctx.scr_cards
    ]


def _backend(ctx: DevContext) -> list[ChangeSpec]:
    """One spec per API operation (from the SRD's C4 api_specs), method/path in the title."""
    specs: list[ChangeSpec] = []
    for a in ctx.api_specs:
        verb = f"{a.http_method} {a.path}".strip()
        specs.append(_spec("backend", "be", a.id, f"API: {verb or a.label or a.id}", a.source_locus))
    return specs


def _integration(ctx: DevContext) -> list[ChangeSpec]:
    """One spec per INT-* integration (protocol + endpoints in the title)."""
    specs: list[ChangeSpec] = []
    for i in ctx.int_cards:
        proto = f" [{i.protocol}]" if i.protocol else ""
        specs.append(_spec("integration", "int", i.id, f"Integration: {i.label or i.id}{proto}", i.source_locus))
    return specs


def _db(ctx: DevContext) -> list[ChangeSpec]:
    """One spec per DB-table entity (ENT-*); to-be carries any schema change targeting that table."""
    change_by_table = {(sc.table or "").lower(): sc for sc in ctx.schema_changes}
    specs: list[ChangeSpec] = []
    for e in ctx.data_entities:
        sc = change_by_table.get((e.label or "").lower())
        to_be = (f"{sc.op} on {sc.table}: {sc.rationale}" if sc else
                 "DEV-TODO: no schema change proposed — confirm read-only usage")
        specs.append(_spec("db", "db", e.id, f"Table: {e.label or e.id}", e.source_locus, to_be=to_be))
    return specs


def build_change_specs(ctx: DevContext) -> list[ChangeSpec]:
    """docs/24 §D — the four lanes concatenated (frontend · backend · integration · db)."""
    return _frontend(ctx) + _backend(ctx) + _integration(ctx) + _db(ctx)
