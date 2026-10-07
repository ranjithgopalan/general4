"""Official AIG .docx renderer for ImpactAnalysis, FSDDocument, and BRDDocument (one source → Word deliverable).

Deterministic (no LLM): maps the structured artifact + its template section spec to a Word document via
python-docx (xpf `document_export_service` pattern). python-docx is imported lazily.
``_bullets`` and ``_table`` are shared helpers used by all renderers.

BRD additions: AIG logo (docs/samples/aig-logo2.png), version table (v / date / author / reason),
sign-off strip (BA / PO / Date), [DECIDED]/[OPEN] decision log, KPI-numbered success criteria.

Stories: exported as CSV (Rally format, HTML descriptions) via ``render_stories_csv()``.
Not docx — stories live in Rally, not Word.
"""

from __future__ import annotations

import csv
import html as html_lib
import io
from pathlib import Path
from typing import Any

from app.lifecycle.stages.analysis.schema import ImpactAnalysis
from app.lifecycle.stages.analysis.sections.business import BusinessImpactView
from app.lifecycle.stages.analysis.sections.deterministic import impact_note
from app.lifecycle.stages.architecture.schema import SRDDocument
from app.lifecycle.stages.brd.schema import BRDDocument
from app.lifecycle.stages.developer.schema import DevDocument
from app.lifecycle.stages.fsd.schema import FSDDocument
from app.lifecycle.stages.fsd.sections.business import BusinessFsdView
from app.lifecycle.stages.qa.schema import TestPlanDocument
from app.lifecycle.stages.stories.schema import StoriesDocument, StoryRow
from app.lifecycle.templates.registry import Template

# AIG logo extracted from docs/samples/Japan AIG Connect - Phase 1.docx.
# Path relative to the repo root (resolved at call-time so tests can override).
_AIG_LOGO_PATH = Path(__file__).resolve().parents[4] / "docs" / "samples" / "aig-logo2.png"


def _bullets(doc: Any, lines: list[str]) -> None:
    if not lines:
        doc.add_paragraph("—")
    for line in lines:
        doc.add_paragraph(line, style="List Bullet")


def _table(doc: Any, headers: list[str], rows: list[list[str]]) -> None:
    if not rows:
        doc.add_paragraph("—")
        return
    table = doc.add_table(rows=1, cols=len(headers))
    table.style = "Light Grid Accent 1"
    for cell, head in zip(table.rows[0].cells, headers, strict=False):
        cell.text = head
    for row in rows:
        cells = table.add_row().cells
        for cell, value in zip(cells, row, strict=False):
            cell.text = value


# ── Business view renderer — SAME 12 sections as the technical doc, plain-language content ──────


def _biz_rows(doc: Any, rows: list[Any]) -> None:
    if not rows:
        doc.add_paragraph("—")
        return
    for r in rows:
        line = f"{r.name} ({r.kind})" if r.kind else r.name
        if r.detail:
            line += f" — {r.detail}"
        doc.add_paragraph(line, style="List Bullet")


def _bz_classification(doc: Any, v: BusinessImpactView) -> None:
    doc.add_paragraph(f"Change class: {v.change_class}  ·  confidence {v.confidence:.2f}")
    if v.change_statement:
        doc.add_paragraph(v.change_statement)


def _bz_summary(doc: Any, v: BusinessImpactView) -> None:
    doc.add_paragraph(v.summary or "—")


def _bz_scope(doc: Any, v: BusinessImpactView) -> None:
    for label, rows in (("New", v.scope.new), ("Enhancement", v.scope.enhancement), ("Existing", v.scope.existing)):
        doc.add_paragraph(f"{label} ({len(rows)})", style="Heading 3")
        _biz_rows(doc, rows)


def _bz_touch(doc: Any, v: BusinessImpactView) -> None:
    _biz_rows(doc, v.touch_points)


def _bz_modifications(doc: Any, v: BusinessImpactView) -> None:
    _biz_rows(doc, v.modifications)


def _bz_downstream(doc: Any, v: BusinessImpactView) -> None:
    _biz_rows(doc, v.downstream)


def _bz_changes(doc: Any, v: BusinessImpactView) -> None:
    _biz_rows(doc, v.change_locations)


def _bz_conflicts(doc: Any, v: BusinessImpactView) -> None:
    if not v.conflicts:
        doc.add_paragraph("No conflicts detected.")
        return
    for c in v.conflicts:
        doc.add_paragraph(c, style="List Bullet")


def _bz_coverage(doc: Any, v: BusinessImpactView) -> None:
    doc.add_paragraph(f"Coverage {v.coverage.coverage_pct:.0f}% — {v.coverage.note}")


def _bz_risks(doc: Any, v: BusinessImpactView) -> None:
    doc.add_paragraph(f"Estimated effort: {v.effort}.")
    if not v.risks:
        doc.add_paragraph("No specific delivery risks were identified at this stage.")
        return
    for r in v.risks:
        doc.add_paragraph(r, style="List Bullet")


def _bz_gaps(doc: Any, v: BusinessImpactView) -> None:
    if not v.questions:
        doc.add_paragraph("None at this stage.")
        return
    for q in v.questions:
        doc.add_paragraph(q, style="List Bullet")


def _bz_references(doc: Any, v: BusinessImpactView) -> None:
    _bullets(doc, v.references)


_BUSINESS_SECTION_RENDERERS = {
    "classification": _bz_classification,
    "narrative": _bz_summary,
    "scope": _bz_scope,
    "touch_points": _bz_touch,
    "modifications": _bz_modifications,
    "downstream": _bz_downstream,
    "change_locations": _bz_changes,
    "conflicts": _bz_conflicts,
    "coverage": _bz_coverage,
    "risks": _bz_risks,
    "gaps": _bz_gaps,
    "references": _bz_references,
}


def render_impact_business_docx(view: BusinessImpactView, template: Template) -> bytes:
    """Render the BusinessImpactView to a plain-language BUSINESS .docx — SAME sections as the technical doc."""
    try:
        from docx import Document
    except ImportError as exc:  # pragma: no cover — dep guaranteed by requirements at runtime
        raise RuntimeError("python-docx is required for .docx export") from exc

    doc = Document()
    doc.add_heading(template.title, level=0)  # no workspace id in the business title (it's in the filename)
    if view.requirement:
        doc.add_paragraph(f"Change requested: {view.requirement}")
    doc.add_paragraph("A plain-language summary of what this change affects, for business stakeholders.")
    for section in template.sections:
        doc.add_heading(section.title, level=1)
        renderer = _BUSINESS_SECTION_RENDERERS.get(section.key)
        if renderer:
            renderer(doc, view)
    buffer = io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()

def _add_classification(doc: Any, a: ImpactAnalysis) -> None:
    c = a.classification
    doc.add_paragraph(f"Change class: {c.change_class}  ·  confidence {c.confidence:.2f}")
    if c.rationale:
        doc.add_paragraph(c.rationale)


def _add_narrative(doc: Any, a: ImpactAnalysis) -> None:
    doc.add_paragraph(a.narrative or "—")


def _add_scope(doc: Any, a: ImpactAnalysis) -> None:
    for label, items in (("New", a.scope.new), ("Enhancement", a.scope.enhancement), ("Existing", a.scope.existing)):
        doc.add_paragraph(f"{label} ({len(items)})", style="Heading 3")
        _bullets(
            doc, [f"{it.label}{f' [{it.id}]' if it.id else ''}{f' — {it.note}' if it.note else ''}" for it in items]
        )


def _summary_map(a: ImpactAnalysis) -> dict[str, str]:
    """id → grounded card summary, collected from the source nodes (matched + affected + downstream)."""
    out: dict[str, str] = {}
    for node in (*a.matched, *a.affected, *a.downstream):
        if getattr(node, "summary", None):
            out.setdefault(node.id, node.summary)
    return out


def _node_bullet(doc: Any, nid: str, label: str, kind: str, summary: str | None, tail: str | None) -> None:
    """One grounded per-node prose bullet: ``[id] label (kind) — summary  ▸ impact``."""
    line = f"[{nid}] {label} ({kind})"
    if summary:
        line += f" — {summary}"
    if tail:
        line += f"  ▸ {tail}"
    doc.add_paragraph(line, style="List Bullet")


def _add_touch(doc: Any, a: ImpactAnalysis) -> None:
    """Q1 — how the change touches the existing system: grounded prose per boundary node + index table."""
    if not a.touch_points:
        doc.add_paragraph("—")
        return
    summ = _summary_map(a)
    for t in a.touch_points:
        _node_bullet(doc, t.id, t.label, t.kind, summ.get(t.id) or t.note, f"touched: {t.relation}")
    _table(doc, ["KB id", "Kind", "How"], [[t.id, t.kind, t.relation] for t in a.touch_points])


def _add_modifications(doc: Any, a: ImpactAnalysis) -> None:
    """Q2 — what is modified (as-is → to-be), per node, grounded in the card body."""
    if not a.modifications:
        doc.add_paragraph("—")
        return
    summ = _summary_map(a)
    for m in a.modifications:
        as_is = m.before or summ.get(m.id) or "—"
        to_be = m.after or m.note or "—"
        doc.add_paragraph(f"[{m.id}] {m.label} ({m.kind})", style="List Bullet")
        doc.add_paragraph(f"as-is: {as_is}")
        doc.add_paragraph(f"to-be: {to_be}")


def _add_downstream(doc: Any, a: ImpactAnalysis) -> None:
    """Q3 — downstream 'what breaks': grounded prose per dependent node + why (edge-aware) + index table."""
    if not a.downstream:
        doc.add_paragraph("—")
        return
    summ = _summary_map(a)
    for n in a.downstream:
        _node_bullet(doc, n.id, n.label, n.kind, summ.get(n.id), impact_note(n.via, n.kind))
    _table(doc, ["KB id", "Kind", "Via"], [[n.id, n.kind, ", ".join(n.via)] for n in a.downstream])


def _add_changes(doc: Any, a: ImpactAnalysis) -> None:
    """Q4 — where changes are needed: grounded prose per implementable node + locus + index table."""
    if not a.change_locations:
        doc.add_paragraph("—")
        return
    summ = _summary_map(a)
    for c in a.change_locations:
        _node_bullet(doc, c.id, c.label, c.kind, summ.get(c.id), c.note or None)
        if c.locus:
            doc.add_paragraph(f"    locus: {c.locus}")
    _table(doc, ["Kind", "KB id", "Where (locus)"], [[c.kind, c.id, c.locus or c.label] for c in a.change_locations])


def _add_conflicts(doc: Any, a: ImpactAnalysis) -> None:
    if not a.conflicts:
        doc.add_paragraph("No conflicts detected.")
        return
    _table(
        doc,
        ["Type", "Severity", "Detail", "Status"],
        [[c.kind, c.severity, c.detail, c.status] for c in a.conflicts],
    )


def _add_coverage(doc: Any, a: ImpactAnalysis) -> None:
    cov = a.coverage
    doc.add_paragraph(
        f"Coverage {cov.coverage_pct:.0f}% — {cov.linked}/{max(1, len(a.matched))} matched cards have an "
        f"impact neighbour ({cov.total} nodes in the impact set)."
    )
    if cov.blindspots:
        # Handle both Blindspot objects (new) and legacy string IDs
        blindspot_strs = []
        for bs in cov.blindspots:
            if isinstance(bs, str):
                blindspot_strs.append(bs)
            elif hasattr(bs, 'label'):
                # Blindspot object with label, kind, reason
                blindspot_strs.append(f"{bs.label} ({bs.kind})")
            else:
                blindspot_strs.append(str(bs))
        doc.add_paragraph("Blind spots (no impact neighbour): " + ", ".join(blindspot_strs))


def _add_risks(doc: Any, a: ImpactAnalysis) -> None:
    doc.add_paragraph(f"Estimated effort: {a.effort}{f' — {a.effort_rationale}' if a.effort_rationale else ''}")
    _table(
        doc,
        ["Severity", "Risk", "Mitigation"],
        [[r.severity, r.description, r.mitigation or "—"] for r in a.risks],
    )


def _add_gaps(doc: Any, a: ImpactAnalysis) -> None:
    _bullets(doc, a.gaps)


def _add_references(doc: Any, a: ImpactAnalysis) -> None:
    refs = a.references or a.matched
    _bullets(doc, [f"[{r.id}] {r.label}{f' — {r.source_locus}' if r.source_locus else ''}" for r in refs])


_SECTION_RENDERERS = {
    "classification": _add_classification,
    "narrative": _add_narrative,
    "scope": _add_scope,
    "touch_points": _add_touch,
    "modifications": _add_modifications,
    "downstream": _add_downstream,
    "change_locations": _add_changes,
    "conflicts": _add_conflicts,
    "coverage": _add_coverage,
    "risks": _add_risks,
    "gaps": _add_gaps,
    "references": _add_references,
}


def _fsd_section_fr(doc: Any, f: FSDDocument) -> None:
    _table(
        doc,
        ["KB ID", "Requirement", "As-Is", "To-Be", "Priority", "Source"],
        [
            [
                fr.id,
                fr.title,
                fr.as_is or "—",
                fr.to_be or (fr.stub_marker or "—"),
                fr.priority,
                fr.source_type,
            ]
            for fr in f.functional_requirements
        ],
    )


def _fsd_section_proc(doc: Any, f: FSDDocument) -> None:
    for p in f.process_flows:
        doc.add_paragraph(f"[{p.id}] {p.title} ({p.kind})", style="Heading 3")
        if p.actors:
            doc.add_paragraph("Actors: " + ", ".join(p.actors))
        _bullets(doc, p.steps)


def _fsd_section_screens(doc: Any, f: FSDDocument) -> None:
    for s in f.screen_specs:
        doc.add_paragraph(f"[{s.id}] {s.title}", style="Heading 3")
        if s.purpose:
            doc.add_paragraph(s.purpose)
        if s.fields:
            doc.add_paragraph("Fields: " + ", ".join(s.fields))
        if s.business_rules:
            doc.add_paragraph("Rules: " + ", ".join(s.business_rules))


def _fsd_section_rules(doc: Any, f: FSDDocument) -> None:
    _table(
        doc,
        ["KB ID", "Rule", "Applies To"],
        [[r.id, r.rule_text or r.title, ", ".join(r.applies_to) or "—"] for r in f.business_rules],
    )


def _fsd_section_data(doc: Any, f: FSDDocument) -> None:
    _table(
        doc,
        ["KB ID", "Entity", "Impact", "Note"],
        [[d.id, d.label, d.impact, d.note or "—"] for d in f.data_model],
    )


def _fsd_section_integrations(doc: Any, f: FSDDocument) -> None:
    _table(
        doc,
        ["KB ID", "Integration", "Kind", "Direction"],
        [[i.id, i.label, i.kind, i.direction or "—"] for i in f.integration_points],
    )


def _fsd_section_nfrs(doc: Any, f: FSDDocument) -> None:
    _table(
        doc,
        ["ID", "Category", "Description", "Stub"],
        [[n.id, n.category, n.description, n.stub_marker or "—"] for n in f.non_functional_reqs],
    )


def _fsd_section_ac3(doc: Any, f: FSDDocument) -> None:
    _table(
        doc,
        ["FSD Section", "KB ID", "Label", "Link Type", "Locus"],
        [[a.fsd_section, a.kb_id, a.kb_label, a.link_type, a.source_locus or "—"] for a in f.acceptance_criteria],
    )


def _fsd_section_open(doc: Any, f: FSDDocument) -> None:
    _table(
        doc,
        ["ID", "Section", "Description", "Marker", "Priority"],
        [[s.id, s.section, s.description, s.marker, s.priority] for s in f.open_items],
    )


def _fsd_section_refs(doc: Any, f: FSDDocument) -> None:
    _bullets(doc, [f"[{r.id}] {r.label}{f' — {r.source_locus}' if r.source_locus else ''}" for r in f.references])


_FSD_SECTION_RENDERERS: dict[str, Any] = {
    "context_summary":        lambda doc, f: doc.add_paragraph(f.context_summary or "—"),
    "scope":                  lambda doc, f: _add_scope(doc, f),  # type: ignore[arg-type]
    "functional_requirements": _fsd_section_fr,
    "process_flows":          _fsd_section_proc,
    "screen_specs":           _fsd_section_screens,
    "business_rules":         _fsd_section_rules,
    "data_model":             _fsd_section_data,
    "integration_points":     _fsd_section_integrations,
    "non_functional_reqs":    _fsd_section_nfrs,
    "acceptance_criteria":    _fsd_section_ac3,
    "open_items":             _fsd_section_open,
    "references":             _fsd_section_refs,
}


# ── Business FSD renderer — SAME 12 sections as the technical FSD, plain-language content ──────


def _fbz_context(doc: Any, v: BusinessFsdView) -> None:
    doc.add_paragraph(f"Change class: {v.change_class or '—'}")
    doc.add_paragraph(v.context_summary or "—")


def _fbz_scope(doc: Any, v: BusinessFsdView) -> None:
    for label, rows in (("New", v.scope.new), ("Enhancement", v.scope.enhancement), ("Existing", v.scope.existing)):
        doc.add_paragraph(f"{label} ({len(rows)})", style="Heading 3")
        _biz_rows(doc, rows)


def _fbz_fr(doc: Any, v: BusinessFsdView) -> None:
    _biz_rows(doc, v.functional_requirements)


def _fbz_proc(doc: Any, v: BusinessFsdView) -> None:
    _biz_rows(doc, v.process_flows)


def _fbz_screens(doc: Any, v: BusinessFsdView) -> None:
    _biz_rows(doc, v.screen_specs)


def _fbz_rules(doc: Any, v: BusinessFsdView) -> None:
    _biz_rows(doc, v.business_rules)


def _fbz_data(doc: Any, v: BusinessFsdView) -> None:
    _biz_rows(doc, v.data_model)


def _fbz_integ(doc: Any, v: BusinessFsdView) -> None:
    _biz_rows(doc, v.integration_points)


def _fbz_nfr(doc: Any, v: BusinessFsdView) -> None:
    _biz_rows(doc, v.non_functional_reqs)


def _fbz_ac(doc: Any, v: BusinessFsdView) -> None:
    _biz_rows(doc, v.acceptance_criteria)


def _fbz_open(doc: Any, v: BusinessFsdView) -> None:
    _bullets(doc, v.open_items)


def _fbz_refs(doc: Any, v: BusinessFsdView) -> None:
    _bullets(doc, v.references)


_FSD_BUSINESS_SECTION_RENDERERS = {
    "context_summary": _fbz_context,
    "scope": _fbz_scope,
    "functional_requirements": _fbz_fr,
    "process_flows": _fbz_proc,
    "screen_specs": _fbz_screens,
    "business_rules": _fbz_rules,
    "data_model": _fbz_data,
    "integration_points": _fbz_integ,
    "non_functional_reqs": _fbz_nfr,
    "acceptance_criteria": _fbz_ac,
    "open_items": _fbz_open,
    "references": _fbz_refs,
}


def render_fsd_business_docx(view: BusinessFsdView, template: Template) -> bytes:
    """Render the BusinessFsdView to a plain-language BUSINESS FSD .docx — SAME sections, no ids / jargon."""
    try:
        from docx import Document
    except ImportError as exc:  # pragma: no cover — dep guaranteed by requirements at runtime
        raise RuntimeError("python-docx is required for .docx export") from exc

    doc = Document()
    doc.add_heading(template.title, level=0)
    if view.requirement:
        doc.add_paragraph(f"Change requested: {view.requirement}")
    doc.add_paragraph("A plain-language functional specification for business stakeholders.")
    # Merged-BRD block (business framing folded into the FSD) — rendered explicitly so it appears
    # regardless of the template's section list.
    if view.business_case or view.persona_needs or view.key_decisions:
        doc.add_heading("Business Case, Personas & Key Decisions", level=1)
        if view.business_case:
            doc.add_paragraph(view.business_case)
        if view.persona_needs:
            doc.add_paragraph("Target personas & needs", style="Heading 3")
            _biz_rows(doc, view.persona_needs)
        if view.key_decisions:
            doc.add_paragraph("Key decisions & open items", style="Heading 3")
            _biz_rows(doc, view.key_decisions)
    for section in template.sections:
        doc.add_heading(section.title, level=1)
        renderer = _FSD_BUSINESS_SECTION_RENDERERS.get(section.key)
        if renderer:
            renderer(doc, view)
    buffer = io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


def render_fsd_docx(fsd: FSDDocument, template: Template) -> bytes:
    """Render the FSDDocument to an official AIG .docx and return the bytes."""
    try:
        from docx import Document
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("python-docx is required for .docx export") from exc

    doc = Document()
    doc.add_heading(f"{template.title} — {fsd.workspace_id}", level=0)
    doc.add_paragraph(
        f"KB version {fsd.kb_version}  ·  template {fsd.template_id} {fsd.template_version}"
        f"  ·  persona {fsd.persona}  ·  grounding {fsd.grounding_score * 100:.0f}%"
        f"  ·  stubs {fsd.stub_count}"
    )
    doc.add_paragraph(f"Change class: {fsd.change_class}  ·  Requirement: {fsd.requirement}")
    for section in template.sections:
        doc.add_heading(section.title, level=1)
        renderer = _FSD_SECTION_RENDERERS.get(section.key)
        if renderer:
            renderer(doc, fsd)
    # PRD (docs/24 §E) — entry/exit criteria rendered when populated (FSD has none unless PRD mode).
    for title, items in (("Entry Criteria", fsd.entry_criteria), ("Exit Criteria", fsd.exit_criteria)):
        if items:
            doc.add_heading(title, level=1)
            _table(
                doc,
                ["Criterion", "Met", "Basis"],
                [[c.text, "✓" if c.met else "—", c.source_type] for c in items],
            )
    buffer = io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


# ── BRD section renderers ──────────────────────────────────────────────────────

def _brd_section_business_case(doc: Any, b: BRDDocument) -> None:
    doc.add_paragraph(b.business_case or "—")
    trunc = b.section_truncated.get("business_case")
    basis = b.section_basis.get("1.Business Case")
    if trunc:
        doc.add_paragraph("[Truncated — see section 9 (Open Items) for full detail]")
    if basis:
        doc.add_paragraph(f"Basis: {basis}")


def _brd_section_scope(doc: Any, b: BRDDocument) -> None:
    s = b.scope_definition
    for label, items in (("IN SCOPE", s.new), ("OUT OF SCOPE", s.existing), ("DEFERRED", s.enhancement)):
        doc.add_paragraph(f"{label} ({len(items)})", style="Heading 3")
        _bullets(
            doc,
            [f"{it.label}{f' [{it.id}]' if it.id else ''}{f' — {it.note}' if it.note else ''}" for it in items] or ["(none)"],
        )


def _brd_section_requirements(doc: Any, b: BRDDocument) -> None:
    _table(
        doc,
        ["KB ID", "Requirement", "Current State", "Proposed Change", "Priority", "Source"],
        [
            [
                r.id,
                r.requirement or "—",
                (r.current_state or "—")[:200],          # full prose; Word wraps within cell
                r.proposed_change or (r.stub_marker or "BA-TODO"),
                r.priority,
                r.source_type,
            ]
            for r in b.business_requirements
        ],
    )


def _brd_section_rules(doc: Any, b: BRDDocument) -> None:
    _table(
        doc,
        ["KB ID", "Rule", "Applies To"],
        [
            [
                r.id,
                r.rule_text or r.title or "—",
                ", ".join(r.applies_to) or "—",
            ]
            for r in b.business_rules
        ],
    )


def _brd_section_personas(doc: Any, b: BRDDocument) -> None:
    _table(
        doc,
        ["Persona", "Use Case", "What They Need", "Source"],
        [
            [p.persona, p.use_case or "—", p.need or "—", p.source]
            for p in b.stakeholder_personas
        ],
    )


def _brd_section_kpis(doc: Any, b: BRDDocument) -> None:
    trunc = b.section_truncated.get("success_criteria")
    for i, kpi in enumerate(b.success_criteria, 1):
        doc.add_paragraph(f"KPI {i} — {kpi}", style="List Number")
    if not b.success_criteria:
        doc.add_paragraph("—")
    if trunc:
        doc.add_paragraph("[Truncated — see section 9 (Open Items) for full detail]")


def _brd_section_risks(doc: Any, b: BRDDocument) -> None:
    _bullets(doc, b.risks_and_compliance or ["—"])


def _brd_section_ac(doc: Any, b: BRDDocument) -> None:
    _table(
        doc,
        ["BRD Section", "KB ID", "Label", "Link Type", "Locus"],
        [[a.fsd_section, a.kb_id, a.kb_label, a.link_type, a.source_locus or "—"] for a in b.acceptance_criteria],
    )


def _brd_section_decisions(doc: Any, b: BRDDocument) -> None:
    _table(
        doc,
        ["Tag", "Description", "Section", "Marker"],
        [[kd.tag, kd.description or "—", kd.section or "—", kd.marker] for kd in b.key_decisions],
    )
    if not b.key_decisions:
        doc.add_paragraph("—")


def _brd_section_refs(doc: Any, b: BRDDocument) -> None:
    _bullets(
        doc,
        [f"[{r.id}] {r.label or r.kind}{f' — {r.source_locus}' if r.source_locus else ''}" for r in b.references] or ["—"],
    )


_BRD_SECTION_RENDERERS: dict[str, Any] = {
    "business_case":         _brd_section_business_case,
    "scope_definition":      _brd_section_scope,
    "business_requirements": _brd_section_requirements,
    "business_rules":        _brd_section_rules,
    "stakeholder_personas":  _brd_section_personas,
    "success_criteria":      _brd_section_kpis,
    "risks_and_compliance":  _brd_section_risks,
    "acceptance_criteria":   _brd_section_ac,
    "key_decisions":         _brd_section_decisions,
    "references":            _brd_section_refs,
}


def render_brd_docx(brd: BRDDocument, template: Template) -> bytes:
    """Render the BRDDocument to an official AIG .docx and return the bytes.

    Format follows the Japan AIG Connect Phase 1 PRD (docs/samples/Japan AIG Connect - Phase 1.docx):
      - AIG logo top-left (aig-logo2.png, 32px height) if the file exists
      - Version table: Version | Date | Author | Reason
      - Change-class header line
      - Template 10 sections rendered in order
      - Sign-off strip: BA / PO / Date (static — never generated)
    """
    try:
        from docx import Document
        from docx.shared import Inches
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("python-docx is required for .docx export") from exc

    import datetime as _dt

    doc = Document()

    # ── AIG logo (optional — gracefully absent in test/CI environments) ────────
    if _AIG_LOGO_PATH.exists():
        try:
            doc.add_picture(str(_AIG_LOGO_PATH), height=Inches(0.4))
        except Exception:  # noqa: BLE001 — logo failure must never block the export
            pass

    # ── Title ─────────────────────────────────────────────────────────────────
    doc.add_heading(template.title, level=0)

    # ── Version table (PRD DNA: Version / Date / Author / Reason) ─────────────
    vtable = doc.add_table(rows=2, cols=4)
    vtable.style = "Light Grid Accent 1"
    headers = ["Version", "Date", "Author", "Reason"]
    today = _dt.date.today().strftime("%Y-%m-%d")
    values = ["1.0", today, brd.persona, brd.change_class or "Change Request"]
    for cell, head in zip(vtable.rows[0].cells, headers, strict=False):
        cell.text = head
    for cell, val in zip(vtable.rows[1].cells, values, strict=False):
        cell.text = val

    doc.add_paragraph()  # spacer

    # ── Header metadata ───────────────────────────────────────────────────────
    doc.add_paragraph(
        f"KB version {brd.kb_version}  ·  template {brd.template_id} {brd.template_version}"
        f"  ·  grounding {brd.grounding_score * 100:.0f}%"
        f"  ·  open items {brd.stub_count}"
    )
    if brd.requirement:
        doc.add_paragraph(f"Requirement: {brd.requirement}")

    # ── Abstain banner ───────────────────────────────────────────────────────
    if brd.abstained:
        p = doc.add_paragraph("⚠ BRD ABSTAINED — no KB-grounded cards found. All sections are stubs requiring BA input.")
        p.runs[0].bold = True

    # ── 10 BRD sections ───────────────────────────────────────────────────────
    for section in template.sections:
        doc.add_heading(section.title, level=1)
        renderer = _BRD_SECTION_RENDERERS.get(section.key)
        if renderer:
            renderer(doc, brd)

    # ── Sign-off strip (static — never AI-generated) ──────────────────────────
    doc.add_heading("Sign-off", level=1)
    stable = doc.add_table(rows=2, cols=3)
    stable.style = "Light Grid Accent 1"
    for cell, head in zip(stable.rows[0].cells, ["BA", "PO", "Date"], strict=False):
        cell.text = head
    for cell in stable.rows[1].cells:
        cell.text = "_________________________"

    buffer = io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# Stories CSV renderer (Rally format, HTML descriptions — Q2 decision)
# ---------------------------------------------------------------------------

_STORY_HTML_TMPL = (
    "<p><strong>As a</strong> {as_a},<br/>"
    "<strong>I want</strong> {i_want},<br/>"
    "<strong>So that</strong> {so_that}.</p>"
    "<h3>Acceptance Criteria</h3>"
    "<ul>"
    "<li><strong>Given</strong> {ac_given}</li>"
    "<li><strong>When</strong> {ac_when}</li>"
    "<li><strong>Then</strong> {ac_then}</li>"
    "</ul>"
)


def _story_to_html(story: StoryRow) -> str:
    """Genlite pattern: Rally Description column must be HTML, not plain text."""
    def esc(s: str) -> str:
        return html_lib.escape(s or "")
    return _STORY_HTML_TMPL.format(
        as_a=esc(story.as_a),
        i_want=esc(story.i_want),
        so_that=esc(story.so_that),
        ac_given=esc(story.ac_given),
        ac_when=esc(story.ac_when),
        ac_then=esc(story.ac_then),
    )


def render_stories_csv(doc: StoriesDocument) -> bytes:
    """Render StoriesDocument to a Rally-compatible CSV with HTML Description column.

    Columns: ID, Name, Description (HTML), As A, I Want, So That,
             Given, When, Then, Change Class, Plan Estimate, Priority,
             Source Refs, Schedule State, Points Action.

    Encoding: UTF-8 with BOM (utf-8-sig) so Excel opens without garbled characters.
    Rally Schedule State is always 'Defined' (stories are not yet scheduled at S4).
    """
    buf = io.StringIO()
    writer = csv.writer(buf, quoting=csv.QUOTE_ALL)
    writer.writerow([
        "ID", "Name", "Description", "As A", "I Want", "So That",
        "Given", "When", "Then", "Change Class", "Plan Estimate",
        "Priority", "Source Refs", "Schedule State", "Points Action",
    ])
    for story in doc.stories:
        writer.writerow([
            story.story_id,
            story.title,
            _story_to_html(story),
            story.as_a,
            story.i_want,
            story.so_that,
            story.ac_given,
            story.ac_when,
            story.ac_then,
            story.change_class,
            story.points,
            story.priority,
            " ".join(story.source_refs),
            "Defined",
            story.points_action,
        ])
    return buf.getvalue().encode("utf-8-sig")  # BOM for Excel compatibility


def render_srd_docx(srd: SRDDocument, template: Template) -> bytes:
    """Render SRDDocument to an official AIG .docx. Returns bytes."""
    try:
        from docx import Document
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("python-docx is required for .docx export") from exc

    doc = Document()
    # AIG header
    if _AIG_LOGO_PATH.exists():
        try:
            doc.add_picture(str(_AIG_LOGO_PATH), width=None)
        except Exception:
            pass
    doc.add_heading(f"System Requirements Document — {srd.workspace_id}", level=0)
    doc.add_paragraph(
        f"KB version {srd.kb_version}  ·  template {srd.template_id} {srd.template_version}"
        f"  ·  persona {srd.persona}  ·  grounded {srd.grounding_score * 100:.0f}%"
        f"  ·  change: {srd.change_class}"
    )
    if srd.abstained:
        doc.add_paragraph("⚠ ABSTAINED — insufficient KB coverage for this requirement.")

    for section in template.sections:
        doc.add_heading(section.title, level=1)
        renderer = _SRD_SECTION_RENDERERS.get(section.key)
        if renderer:
            renderer(doc, srd)

    # C-section parity — rendered after the template loop so they appear regardless of template
    # (only when populated, to keep the .docx clean on thin requirements).
    if srd.api_specs:
        doc.add_heading("API Specifications", level=1)
        _srd_api_specs(doc, srd)
    if srd.data_model:
        doc.add_heading("Data Model (Entities)", level=1)
        _srd_data_model(doc, srd)
    if srd.schema_changes:
        doc.add_heading("Schema Changes", level=1)
        _srd_schema_changes(doc, srd)

    # AS-IS / TO-BE / END-TO-END diagrams (code blocks — user pastes to mermaid.live)
    doc.add_heading("AS-IS Architecture Diagram (Mermaid DSL)", level=1)
    doc.add_paragraph(srd.asIs_diagram or "— not yet generated")
    doc.add_heading("TO-BE Architecture Diagram (Mermaid DSL)", level=1)
    doc.add_paragraph(srd.toBe_diagram or "— not yet generated")
    if srd.endToEnd_diagram:
        doc.add_heading("End-to-End Architecture Diagram (Mermaid DSL)", level=1)
        doc.add_paragraph(srd.endToEnd_diagram)
    if srd.erd_diagram:
        doc.add_heading("Entity-Relationship Diagram (Mermaid DSL)", level=1)
        doc.add_paragraph(srd.erd_diagram)

    buffer = io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


def _srd_api_specs(doc: Any, srd: SRDDocument) -> None:
    _table(
        doc,
        ["KB ID", "Operation", "Method", "Path", "From", "To"],
        [[a.id, a.label, a.http_method or "—", a.path or "—", a.from_component or "—", a.to_component or "—"]
         for a in srd.api_specs],
    )


def _srd_data_model(doc: Any, srd: SRDDocument) -> None:
    _table(
        doc,
        ["KB ID", "Entity", "Key Columns", "Relationships"],
        [[e.id, e.label, ", ".join(e.key_columns) or "—", ", ".join(e.relationships) or "—"]
         for e in srd.data_model],
    )


def _srd_schema_changes(doc: Any, srd: SRDDocument) -> None:
    _table(
        doc,
        ["Table", "Op", "Column", "Rationale", "KB ID"],
        [[s.table, s.op, s.column or "—", s.rationale or "—", s.kb_id or "—"] for s in srd.schema_changes],
    )


def _srd_system_context(doc: Any, srd: SRDDocument) -> None:
    doc.add_paragraph(srd.system_context or "—")


def _srd_scope(doc: Any, srd: SRDDocument) -> None:
    scope = srd.scope_definition
    for label, items in (("IN SCOPE", scope.new + scope.enhancement), ("OUT OF SCOPE", []), ("DEFERRED/EXISTING", scope.existing)):
        doc.add_paragraph(f"{label} ({len(items)})", style="Heading 3")
        _bullets(doc, [f"{it.label}{f' [{it.id}]' if it.id else ''}" for it in items])


def _srd_functional_reqs(doc: Any, srd: SRDDocument) -> None:
    _table(
        doc,
        ["KB ID", "Title", "As-Is", "To-Be", "Priority"],
        [[r.id, r.title, r.as_is or "—", r.to_be or "BA-TODO", r.priority] for r in srd.functional_requirements],
    )


def _srd_component_design(doc: Any, srd: SRDDocument) -> None:
    _table(
        doc,
        ["KB ID", "Component", "Kind", "Responsibility"],
        [[c.id, c.label, c.kind, c.responsibility or "ARCH-TODO"] for c in srd.component_design],
    )


def _srd_integration_design(doc: Any, srd: SRDDocument) -> None:
    _table(
        doc,
        ["KB ID", "Integration", "Protocol", "Direction", "From", "To"],
        [[i.id, i.label, i.protocol or "ARCH-TODO", i.direction or "—", i.from_component or "—", i.to_component or "—"]
         for i in srd.integration_design],
    )


def _srd_sequence_diagrams(doc: Any, srd: SRDDocument) -> None:
    for d in srd.sequence_diagrams:
        doc.add_paragraph(f"{d.story_id} — {d.title}", style="Heading 3")
        doc.add_paragraph(d.mermaid_dsl or "— stub (paste to mermaid.live)")


def _srd_nfrs(doc: Any, srd: SRDDocument) -> None:
    _table(
        doc,
        ["Category", "Requirement", "Metric"],
        [[n.category, n.requirement, n.metric or "—"] for n in srd.non_functional_reqs],
    )


def _srd_open_items(doc: Any, srd: SRDDocument) -> None:
    _table(
        doc,
        ["ID", "Description", "Priority"],
        [[s.id, s.description, s.priority] for s in srd.open_items],
    )


def _srd_references(doc: Any, srd: SRDDocument) -> None:
    _bullets(doc, [f"[{c.id}] {c.label}  {c.source_locus or ''}" for c in srd.references])


_SRD_SECTION_RENDERERS: dict[str, Any] = {
    "system_context": _srd_system_context,
    "scope_definition": _srd_scope,
    "functional_requirements": _srd_functional_reqs,
    "component_design": _srd_component_design,
    "integration_design": _srd_integration_design,
    "sequence_diagrams": _srd_sequence_diagrams,
    "non_functional_reqs": _srd_nfrs,
    "open_items": _srd_open_items,
    "references": _srd_references,
}


def render_dev_docx(dev: DevDocument, template: Template) -> bytes:
    """Render DevDocument to an official AIG .docx. Returns bytes.

    Sections:
      1. Implementation Plan — one table row per ImplTask (story/ownership/gap_status)
      2. Code Stubs — stack-separated; filename + ownership + stub body as code block
      3. Integration Wiring — one row per WiringTask
      4. Development Notes — bullet list
      5. Open Items & DEV-TODO — stub table
      6. Dev Gaps — gap log (S3 GAP HANDLING rule: never invented content)
    """
    try:
        from docx import Document
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("python-docx is required for .docx export") from exc

    doc = Document()

    # AIG header
    if _AIG_LOGO_PATH.exists():
        try:
            doc.add_picture(str(_AIG_LOGO_PATH), width=None)
        except Exception:
            pass

    doc.add_heading(f"Developer Implementation Document — {dev.workspace_id}", level=0)
    doc.add_paragraph(
        f"KB version {dev.kb_version}  ·  template {dev.template_id} {dev.template_version}"
        f"  ·  persona {dev.persona}  ·  grounding {dev.grounding_score * 100:.0f}%"
        f"  ·  stubs {dev.stub_count}  ·  change: {dev.change_class}"
    )
    if dev.requirement:
        doc.add_paragraph(f"Requirement: {dev.requirement}")
    if dev.abstained:
        doc.add_paragraph(
            "⚠ DEV ABSTAINED — no developer-visible KB cards found. "
            "All sections are stubs requiring Architect review."
        ).runs[0].bold = True

    for section in template.sections:
        doc.add_heading(section.title, level=1)
        renderer = _DEV_SECTION_RENDERERS.get(section.key)
        if renderer:
            renderer(doc, dev)

    # docs/24 §D — change specs (lane-grouped), rendered after the template loop when populated.
    if dev.change_specs:
        doc.add_heading("Change Specs (by lane)", level=1)
        _dev_change_specs(doc, dev)

    buffer = io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


def _dev_change_specs(doc: Any, dev: DevDocument) -> None:
    for lane in ("frontend", "backend", "integration", "db"):
        rows = [c for c in dev.change_specs if c.lane == lane]
        if not rows:
            continue
        doc.add_paragraph(f"{lane.upper()} ({len(rows)})", style="Heading 3")
        _table(
            doc,
            ["Spec", "KB IDs", "Target files", "To-Be", "Gap", "Owner"],
            [[c.title, ", ".join(c.kb_ids), ", ".join(c.target_files) or "— new (DEV-TODO)",
              c.to_be or "—", c.gap_status, c.ownership] for c in rows],
        )


def _dev_impl_plan(doc: Any, dev: DevDocument) -> None:
    _table(
        doc,
        ["Story", "Title", "Components", "Ownership", "Gap Status", "Confidence"],
        [
            [
                t.story_id,
                t.title[:80] or "—",
                ", ".join(t.components_to_modify[:3]) or "—",
                t.ownership,
                t.gap_status,
                f"{t.match_confidence:.2f}",
            ]
            for t in dev.implementation_plan
        ],
    )


def _dev_code_stubs(doc: Any, dev: DevDocument) -> None:
    if not dev.code_stubs:
        doc.add_paragraph("No code stubs generated.")
        return
    # Group by stack
    by_stack: dict[str, list] = {}
    for stub in dev.code_stubs:
        by_stack.setdefault(stub.stack, []).append(stub)
    for stack, stubs in sorted(by_stack.items()):
        doc.add_paragraph(f"{stack.upper()} ({len(stubs)} stubs)", style="Heading 3")
        for stub in stubs:
            doc.add_paragraph(
                f"  {stub.filename}  [{stub.ownership}]  {stub.source_locus or ''}"
            )
            # Code block — truncate for docx readability (full code in S3)
            preview = (stub.code or "")[:600]
            if len(stub.code or "") > 600:
                preview += "\n  … (truncated — full content in S3)"
            doc.add_paragraph(preview)


def _dev_integration_wiring(doc: Any, dev: DevDocument) -> None:
    _table(
        doc,
        ["Integration ID", "Label", "Protocol", "From", "To", "Ownership", "Note"],
        [
            [
                w.integration_id,
                w.label or "—",
                w.protocol or "ARCH-TODO",
                w.from_component or "—",
                w.to_component or "—",
                w.ownership,
                w.wiring_note[:120] or "—",
            ]
            for w in dev.integration_wiring
        ],
    )


def _dev_notes(doc: Any, dev: DevDocument) -> None:
    _bullets(doc, [n.text for n in dev.development_notes] or ["(no development notes)"])


def _dev_open_items(doc: Any, dev: DevDocument) -> None:
    _table(
        doc,
        ["ID", "Section", "Description", "Priority"],
        [[s.id, s.section or "—", s.description[:200], s.priority] for s in dev.open_items],
    )


def _dev_gaps(doc: Any, dev: DevDocument) -> None:
    if not dev.dev_gaps:
        doc.add_paragraph("No dev gaps recorded.")
        return
    _table(
        doc,
        ["Story", "Component", "Gap Status", "SME Required", "Description"],
        [
            [
                g.story_id or "—",
                g.component_id or "—",
                g.gap_status,
                "Yes" if g.sme_required else "No",
                g.description[:200],
            ]
            for g in dev.dev_gaps
        ],
    )


_DEV_SECTION_RENDERERS: dict[str, Any] = {
    "implementation_plan": _dev_impl_plan,
    "code_stubs": _dev_code_stubs,
    "integration_wiring": _dev_integration_wiring,
    "development_notes": _dev_notes,
    "open_items": _dev_open_items,
    "dev_gaps": _dev_gaps,
}


# ── QA Test Plan section renderers ─────────────────────────────────────────────────────────────


def _qa_test_scope(doc: Any, plan: TestPlanDocument) -> None:
    doc.add_paragraph(plan.test_scope or "—")


def _qa_story_cases(doc: Any, plan: TestPlanDocument) -> None:
    _table(
        doc,
        ["TC ID", "Story", "Type", "Preconditions", "Steps", "Expected Result", "Priority", "Stub"],
        [
            [
                tc.id,
                tc.story_id or "—",
                tc.test_type,
                "; ".join(tc.preconditions[:2]) or "—",
                "; ".join(tc.steps[:3]) or "—",
                (tc.expected_result or "—")[:200],
                tc.priority,
                tc.stub_marker or "—",
            ]
            for tc in plan.story_test_cases
        ],
    )


def _qa_screen_validations(doc: Any, plan: TestPlanDocument) -> None:
    for sv in plan.screen_validations:
        doc.add_paragraph(f"[{sv.screen_id}] {sv.label}", style="Heading 3")
        if sv.purpose:
            doc.add_paragraph(sv.purpose)
        if sv.field_validations:
            doc.add_paragraph("Field validations: " + " | ".join(sv.field_validations))
        if sv.rule_validations:
            doc.add_paragraph("Rule validations: " + " | ".join(sv.rule_validations))
        if sv.stub_marker:
            doc.add_paragraph(f"⚠ {sv.stub_marker}")
    if not plan.screen_validations:
        doc.add_paragraph("—")


def _qa_br_tests(doc: Any, plan: TestPlanDocument) -> None:
    _table(
        doc,
        ["Rule ID", "Label", "Rule Text", "Test Assertions", "Source", "Stub"],
        [
            [
                brt.rule_id,
                brt.label or "—",
                (brt.rule_text or "—")[:300],
                "; ".join(brt.test_assertions[:2]) or "—",
                brt.source_type,
                brt.stub_marker or "—",
            ]
            for brt in plan.business_rule_tests
        ],
    )


def _qa_workflow_scenarios(doc: Any, plan: TestPlanDocument) -> None:
    for ws in plan.workflow_scenarios:
        doc.add_paragraph(f"[{ws.workflow_id}] {ws.title}", style="Heading 3")
        if ws.participants:
            doc.add_paragraph("Participants: " + ", ".join(ws.participants))
        _bullets(doc, ws.scenario_steps or ["—"])
        if ws.expected_outcome:
            doc.add_paragraph(f"Expected: {ws.expected_outcome}")
        if ws.stub_marker:
            doc.add_paragraph(f"⚠ {ws.stub_marker}")
    if not plan.workflow_scenarios:
        doc.add_paragraph("—")


def _qa_role_access(doc: Any, plan: TestPlanDocument) -> None:
    _table(
        doc,
        ["Role", "Story", "Screens", "Test Assertions", "Stub"],
        [
            [
                ra.role_label,
                ra.story_id or "—",
                ", ".join(ra.screens[:3]) or "—",
                "; ".join(ra.test_assertions[:2]) or "—",
                ra.stub_marker or "—",
            ]
            for ra in plan.role_access_tests
        ],
    )


def _qa_integration_tests(doc: Any, plan: TestPlanDocument) -> None:
    _table(
        doc,
        ["INT ID", "Label", "Protocol", "From", "To", "Test Assertions", "Stub"],
        [
            [
                it.integration_id,
                it.label or "—",
                it.protocol or "ARCH-TODO",
                it.from_component or "—",
                it.to_component or "—",
                "; ".join(it.test_assertions[:2]) or "—",
                it.stub_marker or "—",
            ]
            for it in plan.integration_tests
        ],
    )


def _qa_regression_scope(doc: Any, plan: TestPlanDocument) -> None:
    _bullets(doc, plan.regression_scope or ["—"])


def _qa_gap_log(doc: Any, plan: TestPlanDocument) -> None:
    _table(
        doc,
        ["Gap ID", "Type", "Source", "Priority", "Action"],
        [
            [
                g.gap_id,
                g.gap_type,
                (g.source or "—")[:120],
                g.priority,
                (g.action or "—")[:200],
            ]
            for g in plan.gap_log
        ],
    )


def _qa_references(doc: Any, plan: TestPlanDocument) -> None:
    refs = []
    for label, ref_id in (
        ("DevDocument", plan.dev_ref),
        ("StoriesDocument", plan.stories_ref),
        ("SRDDocument", plan.srd_ref),
        ("FSDDocument", plan.fsd_ref),
        ("BRDDocument", plan.brd_ref),
    ):
        if ref_id:
            refs.append(f"{label}: {ref_id}")
    for r in plan.references:
        refs.append(f"[{r.id}] {r.label or r.kind}{f' — {r.source_locus}' if r.source_locus else ''}")
    _bullets(doc, refs or ["—"])


def _qa_test_executions(doc: Any, plan: TestPlanDocument) -> None:
    _table(
        doc,
        ["Test ID", "Status", "Notes", "Executed By", "Executed At"],
        [
            [
                ex.test_id,
                ex.status,
                (ex.notes or "—")[:200],
                ex.executed_by or "—",
                ex.executed_at or "—",
            ]
            for ex in plan.test_executions
        ],
    )


_QA_SECTION_RENDERERS: dict[str, Any] = {
    "test_scope":          _qa_test_scope,
    "story_test_cases":    _qa_story_cases,
    "screen_validations":  _qa_screen_validations,
    "business_rule_tests": _qa_br_tests,
    "workflow_scenarios":  _qa_workflow_scenarios,
    "role_access_tests":   _qa_role_access,
    "integration_tests":   _qa_integration_tests,
    "regression_scope":    _qa_regression_scope,
    "gap_log":             _qa_gap_log,
    "references":          _qa_references,
    "test_executions":     _qa_test_executions,
}


def render_qa_docx(plan: TestPlanDocument, template: Template) -> bytes:
    """Render TestPlanDocument to an official AIG .docx. Returns bytes.

    Follows the same AIG header pattern as render_brd_docx:
      - AIG logo (optional)
      - Title + workspace metadata
      - Template sections in order
      - Execution summary appended after the last template section
    """
    try:
        from docx import Document
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("python-docx is required for .docx export") from exc

    doc = Document()

    # ── AIG logo (optional — gracefully absent in test/CI) ─────────────────────
    if _AIG_LOGO_PATH.exists():
        try:
            from docx.shared import Inches
            doc.add_picture(str(_AIG_LOGO_PATH), height=Inches(0.4))
        except Exception:  # noqa: BLE001 — logo failure must never block the export
            pass

    # ── Title ──────────────────────────────────────────────────────────────────
    doc.add_heading(f"{template.title} — {plan.workspace_id}", level=0)

    # ── Metadata ───────────────────────────────────────────────────────────────
    doc.add_paragraph(
        f"KB version {plan.kb_version}  ·  template {plan.template_id} {plan.template_version}"
        f"  ·  persona {plan.persona}  ·  grounding {plan.grounding_score * 100:.0f}%"
        f"  ·  stubs {plan.stub_count}  ·  change: {plan.change_class}"
    )
    if plan.requirement:
        doc.add_paragraph(f"Requirement: {plan.requirement}")
    if plan.abstained:
        p = doc.add_paragraph(
            "⚠ QA ABSTAINED — no KB-grounded content found. All sections are stubs."
            " Ensure Developer stage has been accepted and re-generate."
        )
        p.runs[0].bold = True

    # ── Template sections (10 + execution records) ─────────────────────────────
    for section in template.sections:
        doc.add_heading(section.title, level=1)
        renderer = _QA_SECTION_RENDERERS.get(section.key)
        if renderer:
            renderer(doc, plan)

    buffer = io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


def render_impact_docx(analysis: ImpactAnalysis, template: Template) -> bytes:
    """Render the ImpactAnalysis to an official AIG .docx and return the bytes."""
    try:
        from docx import Document
    except ImportError as exc:  # pragma: no cover — dep guaranteed by requirements at runtime
        raise RuntimeError("python-docx is required for .docx export") from exc

    doc = Document()
    doc.add_heading(f"{template.title} — {analysis.workspace_id}", level=0)
    doc.add_paragraph(
        f"KB version {analysis.kb_version}  ·  template {analysis.template_id} {analysis.template_version}"
        f"  ·  persona {analysis.persona}  ·  grounded {analysis.grounding_score * 100:.0f}%"
    )
    for section in template.sections:
        doc.add_heading(section.title, level=1)
        renderer = _SECTION_RENDERERS.get(section.key)
        if renderer:
            renderer(doc, analysis)
    buffer = io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()
