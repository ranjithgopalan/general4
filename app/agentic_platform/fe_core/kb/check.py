"""Semantic gate for the built KB tree.

Called by the WP2 kb_extract LangGraph node 'gate' and by stage_executor._kb_gate()
after a kb-extract run. Mirrors the shape of the old kb_check.py plugin report.

Returns a CheckResult; the caller decides whether to fail the run.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from app.agentic_platform.fe_core.kb.cards_schema import (
    CARD_ID_PATTERN as CARD_ID_RE,
    KIND_PREFIXES,
    kind_of,
)

_FRONT_MATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.S)


@dataclass
class CheckResult:
    passed: bool
    cards: int = 0
    nodes: int = 0
    edges: int = 0
    evidence_lines: int = 0
    findings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "pass": self.passed,
            "cards": self.cards,
            "nodes": self.nodes,
            "edges": self.edges,
            "evidence_lines": self.evidence_lines,
            "findings": self.findings,
        }


def _read_front_matter(md_text: str) -> dict:
    m = _FRONT_MATTER_RE.match(md_text)
    if not m:
        return {}
    try:
        import yaml  # noqa: PLC0415
        return yaml.safe_load(m.group(1)) or {}
    except Exception:  # noqa: BLE001
        return {}


def check(kb_dir: Path) -> CheckResult:
    """Run all semantic gate checks against the KB tree at kb_dir.

    Checks:
    1. Unique ids across all card .md files
    2. Each card id matches pattern and kind/prefix are consistent
    3. Every card has ≥1 evidence entry (non-empty evidence section or evidence-map entry)
    4. cards.jsonl in sync with .md files (same set of ids)
    5. Every edge endpoint in graph.json exists as a card id
    6. No orphan evidence (evidence-map.jsonl card_id references a real card)
    """
    findings: list[str] = []

    knowledge_dir = kb_dir / "knowledge"
    graph_path = kb_dir / "knowledge" / "ontology" / "graph.json"
    evidence_path = kb_dir / "evidence" / "evidence-map.jsonl"
    cards_jsonl = kb_dir / "cards.jsonl"

    # --- Collect card ids from .md files ------------------------------------
    card_ids: dict[str, Path] = {}
    md_files = list(knowledge_dir.rglob("*.md")) if knowledge_dir.exists() else []
    for md_path in md_files:
        if "ontology" in md_path.parts:
            continue
        fm = _read_front_matter(md_path.read_text(encoding="utf-8", errors="replace"))
        cid = fm.get("id", "")
        if not cid:
            findings.append(f"card file {md_path.name} has no 'id' in front-matter")
            continue
        if not CARD_ID_RE.match(cid):
            findings.append(f"card id '{cid}' in {md_path.name} does not match pattern")
        if cid in card_ids:
            findings.append(f"duplicate card id '{cid}' in {md_path.name} (also {card_ids[cid].name})")
        else:
            card_ids[cid] = md_path

        # kind/prefix consistency (an alias prefix such as SRV belongs to its kind)
        kind = fm.get("kind", "")
        expected = kind_of(cid) if cid else ""
        if kind and expected in KIND_PREFIXES and kind != expected:
            findings.append(f"card '{cid}': kind '{kind}' does not match id prefix '{cid.split('-')[0]}'")

        # evidence check
        body = md_path.read_text(encoding="utf-8", errors="replace")
        has_evidence_section = "## Evidence" in body or "evidence:" in body.split("---")[0] if "---" in body else False
        if not has_evidence_section:
            findings.append(f"card '{cid}' has no Evidence section")

    # --- cards.jsonl sync ---------------------------------------------------
    jsonl_ids: set[str] = set()
    if cards_jsonl.exists():
        for line in cards_jsonl.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
                jid = row.get("id", "")
                if jid:
                    jsonl_ids.add(jid)
            except json.JSONDecodeError:
                findings.append(f"cards.jsonl: unparseable line: {line[:60]}")

        in_md_not_jsonl = set(card_ids) - jsonl_ids
        in_jsonl_not_md = jsonl_ids - set(card_ids)
        for cid in sorted(in_md_not_jsonl):
            findings.append(f"card '{cid}' in .md files but missing from cards.jsonl")
        for cid in sorted(in_jsonl_not_md):
            findings.append(f"card '{cid}' in cards.jsonl but has no .md file")

    # --- graph.json edge endpoints ------------------------------------------
    edge_count = 0
    if graph_path.exists():
        try:
            graph = json.loads(graph_path.read_text(encoding="utf-8"))
            for edge in graph.get("edges", []):
                edge_count += 1
                src = edge.get("source") or edge.get("from", "")
                dst = edge.get("target") or edge.get("to", "")
                if src and src not in card_ids:
                    findings.append(f"graph edge dangling source: '{src}'")
                if dst and dst not in card_ids:
                    findings.append(f"graph edge dangling target: '{dst}'")
        except (json.JSONDecodeError, OSError) as exc:
            findings.append(f"could not read graph.json: {exc}")

    # --- evidence-map.jsonl orphan check ------------------------------------
    evidence_count = 0
    if evidence_path.exists():
        for line in evidence_path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            evidence_count += 1
            try:
                row = json.loads(line)
                cid = row.get("card_id", "")
                if cid and cid not in card_ids:
                    findings.append(f"evidence-map: orphan card_id '{cid}' (no card file)")
            except json.JSONDecodeError:
                findings.append(f"evidence-map.jsonl: unparseable line: {line[:60]}")

    passed = len(findings) == 0
    return CheckResult(
        passed=passed,
        cards=len(card_ids),
        nodes=len(card_ids),
        edges=edge_count,
        evidence_lines=evidence_count,
        findings=findings,
    )
