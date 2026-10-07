"""Derive a file dependency graph from RED per-file analyses. Pure Python, no database.

Input is one record per analysed source file, either a ``red_file_analyses`` row (jsonb
columns already parsed by psycopg) or an entry of ``result.files`` in a RED JSON export.
Both carry the same field names.

Where the edges come from
-------------------------
``internal_dependencies``   strings such as ``"Datastore2.inc (provides Provider, DBPath)"``
                            or ``"BLStatusSelect.asp"``. A file token that names another
                            analysed file becomes a ``USES`` edge from this file to it.
                            Tokens that name no analysed file (shared includes, stylesheets)
                            are kept per node as ``includes``.
``entry_points[].triggers`` strings such as ``"HTTP POST from accGetCreditReq.asp"``. A
                            file token naming another analysed file becomes a ``CALLS``
                            edge from that file to this one (it posts to / navigates here).
``database_tables``         kept per node as ``tables`` (the DAO turns them into
                            ``REFERENCES`` edges to ``Table`` nodes).

Name resolution
---------------
File names repeat across folders (``maintain.asp`` exists five times in the AIG set), so
nodes are keyed by normalised path, and a token is resolved by name with this preference:
exactly one file of that name -> it; several, one in the same module -> that one; several,
none in the same module -> an edge to each, flagged ``ambiguous`` with lower confidence.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

FILE_TOKEN = re.compile(r"([\w\-\.]+\.(?:asp|inc|js|css|htm|html|asa|vbs))", re.I)
PAGE_EXTENSIONS = {"asp", "asa"}
MODULE_ROOT_MARKER = "active_files"

REL_USES = "USES"        # this file includes / redirects to / links to the target
REL_CALLS = "CALLS"      # the source posts to / navigates to this file (from triggers)
REL_REFERENCES = "REFERENCES"  # file -> database table (lmod convention)

LABEL_PAGE = "ASPPage"
LABEL_INCLUDE = "IncludeFile"
LABEL_TABLE = "Table"


@dataclass
class FileRecord:
    """The subset of a RED per-file analysis the graph needs."""

    file_name: str
    file_path: str
    analysis_id: int | None = None
    layer: str | None = None
    risk_level: str | None = None
    loc: int = 0
    complexity_score: int = 0
    cyclomatic_complexity: int = 0
    business_domain: str | None = None
    modernization_approach: str | None = None
    estimated_effort: str | None = None
    purpose: str = ""
    internal_dependencies: list[str] = field(default_factory=list)
    triggers: list[str] = field(default_factory=list)
    database_tables: list[str] = field(default_factory=list)
    business_rule_count: int = 0
    security_counts: dict[str, int] = field(default_factory=dict)
    source_language: str | None = None

    @property
    def key(self) -> str:
        return normalise_path(self.file_path)

    @property
    def module(self) -> str:
        return module_of(self.file_path)

    @property
    def stem(self) -> str:
        base = self.file_name.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
        return base.rsplit(".", 1)[0] if "." in base else base


def normalise_path(path: str) -> str:
    return (path or "").replace("\\", "/").strip()


def module_of(path: str, marker: str = MODULE_ROOT_MARKER) -> str:
    """Folder directly under ``marker`` when present (``03-business-line``), else the file's
    parent folder name, else ``(root)``."""
    parts = [p for p in normalise_path(path).split("/") if p]
    if marker in parts:
        i = parts.index(marker)
        if i + 1 < len(parts) - 1:
            return parts[i + 1]
    return parts[-2] if len(parts) >= 2 else "(root)"


def _as_list(value: Any) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        import json  # noqa: PLC0415

        try:
            parsed = json.loads(value)
        except ValueError:
            return [value]
        return parsed if isinstance(parsed, list) else [parsed]
    return [value]


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def record_from_row(row: Mapping[str, Any]) -> FileRecord:
    """Build a FileRecord from a ``red_file_analyses`` row or a RED JSON file entry."""
    triggers: list[str] = []
    for ep in _as_list(row.get("entry_points")):
        if isinstance(ep, Mapping):
            triggers.extend(str(t) for t in _as_list(ep.get("triggers")) if t)
    security: Counter[str] = Counter()
    for sc in _as_list(row.get("security_concerns")):
        if isinstance(sc, Mapping) and sc.get("severity"):
            security[str(sc["severity"])] += 1
    rules = row.get("business_rule_count")
    if rules is None:
        rules = len(_as_list(row.get("business_rules")))
    return FileRecord(
        file_name=str(row.get("file_name") or ""),
        file_path=str(row.get("file_path") or ""),
        analysis_id=row.get("id") if isinstance(row.get("id"), int) else None,
        layer=row.get("layer"),
        risk_level=row.get("risk_level"),
        loc=_int(row.get("loc")),
        complexity_score=_int(row.get("complexity_score")),
        cyclomatic_complexity=_int(row.get("cyclomatic_complexity")),
        business_domain=row.get("business_domain"),
        modernization_approach=row.get("modernization_approach"),
        estimated_effort=row.get("estimated_effort"),
        purpose=str(row.get("purpose") or "").strip(),
        internal_dependencies=[str(d) for d in _as_list(row.get("internal_dependencies")) if d],
        triggers=triggers,
        database_tables=sorted({str(t) for t in _as_list(row.get("database_tables")) if t}),
        business_rule_count=_int(rules),
        security_counts=dict(security),
        source_language=row.get("source_language"),
    )


@dataclass
class DerivedNode:
    key: str
    name: str
    path: str
    module: str
    record: FileRecord
    includes: list[str] = field(default_factory=list)

    def properties(self) -> dict[str, Any]:
        r = self.record
        props: dict[str, Any] = {
            "language": (r.source_language or "asp").lower(),
            "file_name": r.file_name,
            "module": self.module,
            "loc": r.loc,
            "layer": r.layer,
            "risk_level": r.risk_level,
            "complexity_score": r.complexity_score,
            "cyclomatic_complexity": r.cyclomatic_complexity,
            "business_domain": r.business_domain,
            "modernization_approach": r.modernization_approach,
            "estimated_effort": r.estimated_effort,
            "business_rule_count": r.business_rule_count,
            "security_counts": r.security_counts,
            "purpose": r.purpose,
            "database_tables": r.database_tables,
            "includes": self.includes,
            "red_analysis_id": r.analysis_id,
        }
        return {k: v for k, v in props.items() if v is not None}


@dataclass
class DerivedEdge:
    source_key: str
    target_key: str
    rel_type: str
    evidence: str
    confidence: float = 1.0
    ambiguous: bool = False


@dataclass
class DerivedGraph:
    nodes: dict[str, DerivedNode]
    edges: list[DerivedEdge]
    unresolved_pages: Counter
    includes: Counter

    def summary(self) -> dict[str, Any]:
        rel = Counter(e.rel_type for e in self.edges)
        return {
            "nodes": len(self.nodes),
            "edges": len(self.edges),
            "by_rel_type": dict(rel),
            "ambiguous_edges": sum(1 for e in self.edges if e.ambiguous),
            "modules": len({n.module for n in self.nodes.values()}),
            "shared_includes": len(self.includes),
            "unresolved_page_refs": sum(self.unresolved_pages.values()),
        }


def derive(records: Iterable[FileRecord]) -> DerivedGraph:
    nodes: dict[str, DerivedNode] = {}
    by_name: dict[str, list[DerivedNode]] = defaultdict(list)
    for rec in records:
        if not rec.file_path:
            continue
        node = DerivedNode(key=rec.key, name=rec.stem, path=normalise_path(rec.file_path), module=rec.module, record=rec)
        nodes[node.key] = node
        by_name[rec.file_name.lower()].append(node)

    unresolved: Counter = Counter()
    includes: Counter = Counter()
    include_case: dict[str, str] = {}
    edges: dict[tuple[str, str, str], DerivedEdge] = {}

    def resolve(token: str, from_module: str) -> tuple[list[DerivedNode], float, bool]:
        candidates = by_name.get(token.lower(), [])
        if not candidates:
            return [], 0.0, False
        if len(candidates) == 1:
            return candidates, 1.0, False
        same = [c for c in candidates if c.module == from_module]
        if len(same) == 1:
            return same, 0.9, False
        return candidates, 0.5, True

    def add(src: DerivedNode, tgt: DerivedNode, rel: str, evidence: str, conf: float, amb: bool) -> None:
        if src.key == tgt.key:
            return
        k = (src.key, tgt.key, rel)
        cur = edges.get(k)
        if cur is None or conf > cur.confidence:
            edges[k] = DerivedEdge(src.key, tgt.key, rel, evidence[:500], conf, amb)

    for node in nodes.values():
        seen_inc: set[str] = set()
        for dep in node.record.internal_dependencies:
            for tok in FILE_TOKEN.findall(dep):
                targets, conf, amb = resolve(tok, node.module)
                if targets:
                    for t in targets:
                        add(node, t, REL_USES, dep, conf, amb)
                    continue
                low = tok.lower()
                if low.rsplit(".", 1)[-1] in PAGE_EXTENSIONS:
                    unresolved[tok] += 1
                if low not in seen_inc:
                    seen_inc.add(low)
                    include_case.setdefault(low, tok)
                    node.includes.append(include_case[low])
                    includes[include_case[low]] += 1
        node.includes.sort(key=str.lower)
        for trig in node.record.triggers:
            for tok in FILE_TOKEN.findall(trig):
                sources, conf, amb = resolve(tok, node.module)
                for s in sources:
                    add(s, node, REL_CALLS, trig, conf, amb)

    return DerivedGraph(nodes=nodes, edges=list(edges.values()), unresolved_pages=unresolved, includes=includes)


def derive_from_rows(rows: Iterable[Mapping[str, Any]]) -> DerivedGraph:
    return derive(record_from_row(r) for r in rows)
