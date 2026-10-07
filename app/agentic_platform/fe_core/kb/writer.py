"""KB tree writer for the WP2 kb_extract builtin.

Writes the canonical on-disk KB tree from a list of card dicts (as returned by
cards_schema.iter_all_cards()) plus the screens bindings.

Output layout (relative to kb_dir):
  knowledge/<KIND>/<ID>.md         one card per file, YAML front-matter
  knowledge/ontology/graph.json    {nodes, edges}
  evidence/evidence-map.jsonl      one line per evidence entry
  cards.jsonl                      flat export, one card per line
  pages.json                       page → [card_ids]
  _build-report.md                 human-readable intake report
  _provenance.json                 input sha256s, catalogue version
"""

from __future__ import annotations

import json
import re
import textwrap
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

KIND_DIR = {
    "BR": "BR", "FR": "FR", "SCR": "SCR", "CMP": "CMP",
    "API": "API", "ENT": "ENT", "WF": "WF",
}

KIND_LABEL = {
    "BR": "BusinessRule", "FR": "FunctionalReq", "SCR": "Screen",
    "CMP": "Component", "API": "ApiOp", "ENT": "Entity", "WF": "Workflow",
}

# Typed attribute keys to include in the markdown body table per kind
KIND_TYPED_KEYS: dict[str, list[str]] = {
    "BR": ["rule_type", "logic", "business_impact", "acceptance_criteria", "source_file",
           "source_lines", "confidence", "origin"],
    "FR": ["priority", "source_br", "implemented_by", "verification"],
    "SCR": ["route", "roles_visible", "key_fields", "buttons"],
    "CMP": ["component", "business_function", "callers", "db_objects", "db_objects_used",
            "migration_target", "error_handling"],
    "API": ["integration_type", "name", "function", "callers", "target", "protocol", "schema",
            "migration_target", "error_handling"],
    "ENT": ["maps_to_table", "key_columns", "stored_procedures", "sql_query_patterns",
            "jpa_lookup", "pages", "conflicts"],
    "WF": ["entity", "states", "transitions", "events_fired", "steps", "triggers", "actors"],
}


def _cell(v: Any) -> str:
    """One table cell: scalars as text, lists joined, structures as compact JSON."""
    if isinstance(v, dict):
        return json.dumps(v, ensure_ascii=False)
    if isinstance(v, list):
        return ", ".join(_cell(x) for x in v)
    return str(v)


_YAML_PLAIN_RE = re.compile(r"^[A-Za-z0-9_(][A-Za-z0-9 _./()+,;=&%-]*$")
_YAML_WORDS = {"true", "false", "null", "yes", "no", "on", "off"}


def _yaml_str(v: Any) -> str:
    """A front-matter value. Anything YAML could misread (a colon, a '#', quotes,
    a line break, a bare true/null) is written as a double-quoted scalar."""
    if isinstance(v, list):
        if not v:
            return "[]"
        return "[" + ", ".join(str(x) for x in v) + "]"
    if isinstance(v, str):
        if _YAML_PLAIN_RE.match(v) and not v.endswith(" ") and v.lower() not in _YAML_WORDS:
            return v
        return json.dumps(v, ensure_ascii=False)
    return str(v) if v is not None else ""


def _md_table(rows: list[tuple[str, Any]]) -> str:
    if not rows:
        return ""
    lines = ["| Attribute | Value |", "|---|---|"]
    for k, v in rows:
        val = _cell(v).replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {k} | {val} |")
    return "\n".join(lines)


def write_card(
    kb_dir: Path,
    card: dict[str, Any],
    screenshot_path: str = "",
    screenshot_source: dict | None = None,
    screenshots: list[str] | None = None,
) -> Path:
    """Write a single card to kb_dir/knowledge/<KIND>/<ID>.md. Returns the path."""
    cid = card.get("id", "UNKNOWN")
    kind = card.get("kind", "BR")
    kind_dir = KIND_DIR.get(kind, kind)

    out_dir = kb_dir / "knowledge" / kind_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{cid}.md"

    # Front-matter
    fm_lines = [
        "---",
        f"id: {cid}",
        f"kind: {kind}",
        f"label: {_yaml_str(card.get('label', ''))}",
        f"summary: {_yaml_str(card.get('summary', ''))}",
        f"page: {_yaml_str(card.get('page', ''))}",
        f"section: {card.get('section', 0)}",
    ]
    if screenshot_path:
        fm_lines.append(f"screenshot: {screenshot_path}")
    if screenshots and len(screenshots) > 1:
        fm_lines.append(f"screenshots: {json.dumps(screenshots)}")
    if screenshot_source:
        fm_lines.append(f"screenshot_source: {json.dumps(screenshot_source)}")
    owner_epic = card.get("owner_epic", "")
    if owner_epic:
        fm_lines.append(f"owner_epic: {owner_epic}")

    tags = card.get("tags", [])
    if tags:
        fm_lines.append(f"tags: {json.dumps(tags)}")
    origin = card.get("origin", "specify")
    fm_lines.append(f"origin: {origin}")
    fm_lines.append("---")

    body_parts: list[str] = []

    # Statement section
    desc = card.get("description", "") or card.get("label", "")
    if desc:
        body_parts.append(f"## Statement\n\n{desc}")

    # Typed attributes table
    typed_keys = KIND_TYPED_KEYS.get(kind, [])
    typed_rows = []
    for key in typed_keys:
        val = card.get(key)
        if val is not None and val != "" and val != []:
            typed_rows.append((key, val))
    if typed_rows:
        body_parts.append(f"## Typed Attributes\n\n{_md_table(typed_rows)}")

    # Evidence section
    evidence = card.get("evidence", [])
    if evidence:
        ev_lines = ["## Evidence", ""]
        for ev in evidence:
            source = ev.get("source", "")
            locus = ev.get("locus", {})
            snippet = ev.get("snippet", "")
            locus_str = json.dumps(locus) if locus else ""
            ev_lines.append(f"- **source:** {source}")
            if locus_str:
                ev_lines.append(f"  **locus:** `{locus_str}`")
            if snippet:
                ev_lines.append(f"  **snippet:** {snippet[:200]}")
        body_parts.append("\n".join(ev_lines))

    # Related section (cross-references)
    related: list[str] = []
    for ref_field in ("source_br", "implemented_by", "db_objects_used"):
        refs = card.get(ref_field, [])
        if refs:
            related.extend(f"[{r}]" for r in refs)
    if related:
        body_parts.append(f"## Related\n\n{', '.join(related)}")

    content = "\n".join(fm_lines) + "\n\n" + "\n\n".join(body_parts) + "\n"
    out_path.write_text(content, encoding="utf-8")
    return out_path


@dataclass
class KbWriteResult:
    cards_written: int = 0
    edges_written: int = 0
    evidence_lines: int = 0
    scr_with_screenshot: int = 0
    pages: dict[str, list[str]] = field(default_factory=dict)
    issues: list[str] = field(default_factory=list)
    # SCR id → [{"file": "screens/<SCR-ID>.png", "sha256": "..."}], relative to kb_dir
    screenshots: dict[str, list[dict]] = field(default_factory=dict)
    scr_without_screenshot: list[str] = field(default_factory=list)


def write_kb(
    kb_dir: Path,
    cards: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    screen_bindings: dict[str, list[dict]],  # scr_id → [{local_path, s3_key, sha256, order}]
    provenance: dict[str, Any] | None = None,
) -> KbWriteResult:
    """Write the full KB tree to kb_dir from pre-validated card list and edges.

    screen_bindings: maps SCR-UWCR-NNN → list of binding dicts from screens.bind()
    """
    result = KbWriteResult()
    kb_dir.mkdir(parents=True, exist_ok=True)
    evidence_lines: list[str] = []
    graph_nodes: list[dict] = []
    cards_jsonl_lines: list[str] = []
    pages_map: dict[str, list[str]] = {}

    for card in cards:
        cid = card.get("id", "")
        kind = card.get("kind", "BR")
        page = card.get("page", "")

        # Screenshot binding for SCR cards: every picture bound to the card is
        # copied, as kb/screens/<SCR-ID>.png, <SCR-ID>-2.png, ...
        screenshot_path = ""
        screenshot_source: dict | None = None
        screenshot_paths: list[str] = []
        if kind == "SCR":
            import shutil  # noqa: PLC0415
            for b in screen_bindings.get(cid, []):
                local_key = b.get("local_path", "")
                src_path = Path(local_key) if local_key else None
                if not (src_path and src_path.is_file()):
                    continue
                scr_screens_dir = kb_dir / "screens"
                scr_screens_dir.mkdir(exist_ok=True)
                ext = src_path.suffix or ".png"
                n = len(screenshot_paths) + 1
                dest = scr_screens_dir / (f"{cid}{ext}" if n == 1 else f"{cid}-{n}{ext}")
                shutil.copy2(src_path, dest)
                screenshot_paths.append(f"kb/screens/{dest.name}")
                result.screenshots.setdefault(cid, []).append(
                    {"file": f"screens/{dest.name}", "sha256": b.get("sha256", "")})
                if n == 1:
                    screenshot_path = screenshot_paths[0]
                    screenshot_source = {
                        "s3_key": b.get("s3_key", ""),
                        "sha256": b.get("sha256", ""),
                        "order": b.get("order", 1),
                    }
            if screenshot_paths:
                result.scr_with_screenshot += 1
            else:
                result.scr_without_screenshot.append(cid)

        write_card(kb_dir, card, screenshot_path=screenshot_path,
                   screenshot_source=screenshot_source, screenshots=screenshot_paths)
        result.cards_written += 1

        # evidence-map entries
        for ev in card.get("evidence", []):
            row = {
                "card_id": cid,
                "source": ev.get("source", ""),
                "locus": ev.get("locus", {}),
                "snippet": ev.get("snippet", ""),
                "anchor_verdict": "anchored" if ev.get("locus") else "red_only",
                "authority_tier": "red",
            }
            evidence_lines.append(json.dumps(row))
            result.evidence_lines += 1

        # graph node
        graph_nodes.append({
            "id": cid, "kind": KIND_LABEL.get(kind, kind),
            "label": card.get("label", ""),
            "category": card.get("business_domain", card.get("page", "")),
            "page": page,
        })

        # cards.jsonl
        jsonl_row = {
            "id": cid, "kind": kind, "label": card.get("label", ""),
            "summary": card.get("summary", ""), "page": page,
            "section": card.get("section", 0),
            "meta": {
                k: card[k] for k in card
                if k not in ("id", "kind", "label", "summary", "page", "section", "evidence")
                and not k.startswith("_")
            },
        }
        if screenshot_paths:
            jsonl_row["meta"]["screenshot"] = screenshot_paths[0]
            jsonl_row["meta"]["screenshots"] = screenshot_paths
        cards_jsonl_lines.append(json.dumps(jsonl_row))

        # pages map
        if page:
            pages_map.setdefault(page, []).append(cid)

    # graph edges
    graph_edges = []
    for edge in edges:
        from_id = edge.get("from", "") or edge.get("source", "")
        to_id = edge.get("to", "") or edge.get("target", "")
        graph_edges.append({
            "source": from_id, "target": to_id,
            "label": edge.get("label", ""),
            "tag": edge.get("tag") or edge.get("basis", ""),
            "basis": edge.get("basis", ""),
        })
        result.edges_written += 1

    # Write graph.json
    graph_dir = kb_dir / "knowledge" / "ontology"
    graph_dir.mkdir(parents=True, exist_ok=True)
    (graph_dir / "graph.json").write_text(
        json.dumps({"nodes": graph_nodes, "edges": graph_edges}, indent=2),
        encoding="utf-8",
    )

    # Write evidence-map.jsonl
    ev_dir = kb_dir / "evidence"
    ev_dir.mkdir(exist_ok=True)
    (ev_dir / "evidence-map.jsonl").write_text(
        "\n".join(evidence_lines) + ("\n" if evidence_lines else ""),
        encoding="utf-8",
    )

    # Write cards.jsonl
    (kb_dir / "cards.jsonl").write_text(
        "\n".join(cards_jsonl_lines) + ("\n" if cards_jsonl_lines else ""),
        encoding="utf-8",
    )

    # Write pages.json
    result.pages = pages_map
    (kb_dir / "pages.json").write_text(json.dumps(pages_map, indent=2), encoding="utf-8")

    # Write _provenance.json
    prov = provenance or {}
    prov["written_at"] = datetime.now(timezone.utc).isoformat()
    (kb_dir / "_provenance.json").write_text(json.dumps(prov, indent=2), encoding="utf-8")

    return result


def write_build_report(
    kb_dir: Path,
    result: KbWriteResult,
    check_result: Any | None = None,
    catalog: dict | None = None,
    cards_data: dict | None = None,
    sections: dict[str, list[str]] | None = None,
) -> None:
    """Write kb/_build-report.md summarising the build.

    `sections` adds one bullet list per title (unmatched ASP files, dropped
    links, input warnings, ...); empty lists are left out.
    """
    lines = [
        "# KB Build Report",
        "",
        f"**Cards written:** {result.cards_written}",
        f"**Edges:** {result.edges_written}",
        f"**Evidence lines:** {result.evidence_lines}",
        f"**SCR cards with screenshot:** {result.scr_with_screenshot}",
        "",
    ]

    if check_result is not None:
        status = "PASS" if check_result.passed else "FAIL"
        lines += [f"## Gate: {status}", ""]
        if check_result.findings:
            for f_ in check_result.findings:
                lines.append(f"- {f_}")
            lines.append("")

    # Unbound screenshots
    unbound = (catalog or {}).get("unbound", [])
    if unbound:
        lines += [f"## Unbound screenshots ({len(unbound)})", ""]
        for u in unbound:
            lines.append(f"- image order {u.get('order','?')}: {u.get('sha256','')[:16]}…")
        lines.append("")

    # Cards without screenshot
    if cards_data:
        scr_no_pic = [
            p.get("page", "?")
            for p in cards_data.get("issues", [])
            if p.get("type") == "missing_screenshot"
        ]
        if scr_no_pic:
            lines += [f"## SCR cards with no screenshot ({len(scr_no_pic)})", ""]
            for page in scr_no_pic:
                lines.append(f"- {page}")
            lines.append("")

    for title, items in (sections or {}).items():
        if not items:
            continue
        lines += [f"## {title} ({len(items)})", ""]
        lines += [f"- {item}" for item in items]
        lines.append("")

    (kb_dir / "_build-report.md").write_text("\n".join(lines), encoding="utf-8")
