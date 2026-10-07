"""Standalone HTML renderer for an ImpactAnalysis (preview / offline export).

The primary UI renders the structured JSON (the Angular /impact page); this produces a self-contained
HTML string off the SAME structured artifact — the second renderer in the one-source model.
"""

from __future__ import annotations

from html import escape

from app.lifecycle.stages.analysis.schema import ImpactAnalysis
from app.lifecycle.templates.registry import Template


def _li(items: list[str]) -> str:
    return "<ul>" + "".join(f"<li>{escape(x)}</li>" for x in items) + "</ul>" if items else "<p>—</p>"


def _table(headers: list[str], rows: list[list[str]]) -> str:
    if not rows:
        return "<p>—</p>"
    head = "".join(f"<th>{escape(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{escape(c)}</td>" for c in r) + "</tr>" for r in rows)
    return f"<table><tr>{head}</tr>{body}</table>"


def _section_html(key: str, a: ImpactAnalysis) -> str:  # noqa: PLR0911 — a flat dispatch table by section key
    if key == "classification":
        c = a.classification
        return f"<p><b>{escape(c.change_class)}</b> · confidence {c.confidence:.2f}</p><p>{escape(c.rationale)}</p>"
    if key == "narrative":
        return f"<p>{escape(a.narrative) or '—'}</p>"
    if key == "scope":
        parts = []
        for lab, items in (("New", a.scope.new), ("Enhancement", a.scope.enhancement), ("Existing", a.scope.existing)):
            parts.append(
                f"<h4>{lab} ({len(items)})</h4>" + _li([f"{it.label}{f' [{it.id}]' if it.id else ''}" for it in items])
            )
        return "".join(parts)
    if key == "touch_points":
        return _table(
            ["KB id", "System/Boundary", "How"], [[t.id, f"{t.label} ({t.kind})", t.relation] for t in a.touch_points]
        )
    if key == "modifications":
        return _table(
            ["KB id", "Node", "As-is → To-be"],
            [
                [m.id, f"{m.label} ({m.kind})", f"{m.before or '—'} → {m.after or (m.note or '—')}"]
                for m in a.modifications
            ],
        )
    if key == "downstream":
        return _table(
            ["KB id", "Dependent", "Via"], [[n.id, f"{n.label} ({n.kind})", ", ".join(n.via)] for n in a.downstream]
        )
    if key == "change_locations":
        return _table(["Kind", "KB id", "Where"], [[c.kind, c.id, c.locus or c.label] for c in a.change_locations])
    if key == "conflicts":
        return _table(
            ["Type", "Severity", "Detail", "Status"], [[c.kind, c.severity, c.detail, c.status] for c in a.conflicts]
        )
    if key == "coverage":
        cov = a.coverage
        bs = ("<p>Blind spots: " + escape(", ".join(cov.blindspots)) + "</p>") if cov.blindspots else ""
        return f"<p>Coverage {cov.coverage_pct:.0f}% · {cov.linked}/{max(1, len(a.matched))} linked · {cov.total} nodes.</p>{bs}"
    if key == "risks":
        head = f"<p>Effort: <b>{escape(a.effort)}</b>{f' — {escape(a.effort_rationale)}' if a.effort_rationale else ''}</p>"
        return head + _table(
            ["Severity", "Risk", "Mitigation"], [[r.severity, r.description, r.mitigation or "—"] for r in a.risks]
        )
    if key == "gaps":
        return _li(a.gaps)
    if key == "references":
        refs = a.references or a.matched
        return _li([f"[{r.id}] {r.label}" for r in refs])
    return ""


def render_impact_html(analysis: ImpactAnalysis, template: Template) -> str:
    """Render the ImpactAnalysis to a self-contained HTML string."""
    body = [
        f"<h1>{escape(template.title)} — {escape(analysis.workspace_id)}</h1>",
        f"<p class='meta'>KB {escape(analysis.kb_version)} · {escape(analysis.template_id)} "
        f"{escape(analysis.template_version)} · {escape(analysis.persona)}</p>",
    ]
    for section in template.sections:
        body.append(f"<h2>{escape(section.title)}</h2>{_section_html(section.key, analysis)}")
    style = (
        "body{font:14px/1.6 Segoe UI,Arial,sans-serif;max-width:920px;margin:24px auto;color:#1a1f29}"
        "table{border-collapse:collapse;width:100%}td,th{border:1px solid #e4e8f0;padding:6px 10px;text-align:left}"
        "h1{color:#0b2447}h2{color:#19376d;border-bottom:1px solid #e4e8f0;padding-bottom:4px}.meta{color:#5b6472}"
    )
    return f"<!doctype html><html><head><meta charset='utf-8'><style>{style}</style></head><body>{''.join(body)}</body></html>"
