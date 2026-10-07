"""Card / graph knowledge base — typed, cited, graph-linked knowledge on disk.

The chunk RAG store (`fe_core.rag`) answers *"what does the corpus say about X"*
but cannot tell an agent *which* fact it is citing, whether that fact exists, or
what it is connected to. This module adds that layer: **knowledge cards** with
stable ids (``ENT-…``, ``BR-…``, ``FR-…``, ``SCR-…``, ``WF-…``, ``CMP-…``, ``API-…``),
a **graph** of typed edges between them, and an **evidence map** that anchors every
card to a source locus. Together they make grounding *checkable*: an artefact
that cites ``[BR-UWCR-0007]`` can be verified against the store, and one that
cites nothing can be rejected.

On-disk layout (a directory, ``<kb_root>/``) — deliberately the same shape a
reverse-engineering builder produces, so the KB can be built by our own
``AIDLC-kb`` skill today or by any compatible RE engine later without touching
the readers::

    kb/
      knowledge/**/*.md                 one card per file, YAML front-matter
                                        (id, kind, label, summary, sources[], tags[])
      knowledge/ontology/graph.json     {"nodes":[{id,kind,label,...}],
                                         "edges":[{source,target,label|type}]}
      cards.jsonl                       optional flat export {id,kind,label,summary,body}
      evidence/evidence-map.jsonl       {id, source_doc, locus:{...}, snippet, confidence}

Everything here is read-only and pure Python: no database, no LLM. Retrieval
is lexical (BM25-style over label + summary + body); the chunk RAG remains the
semantic path and the two are combined by the API layer.
"""

from __future__ import annotations

import json
import logging
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

logger = logging.getLogger(__name__)

#: A card id: 2-6 upper-case letters, then dash-separated upper-case/digit
#: segments (``ENT-UWCR-0012``, ``BR-7``, ``SCR-JAUTO-FORM-03``). Kept strict so
#: prose like "API-first" or "UI-2" is not mistaken for a citation.
CARD_ID_RE = re.compile(r"\b([A-Z]{2,6}(?:-[A-Z0-9]{1,24}){1,5})\b")

#: Citation position: anything inside square brackets. `[BR-1]`, `[SCR-001, API-002]`,
#: `[reverse-engineering · chunk 1308, FR-003]` all count; a bare `FR-003` in a
#: heading or table cell does not.
CITATION_RE = re.compile(r"\[([^\[\]]{1,300})\]")
_WORD_RE = re.compile(r"[A-Za-z0-9_]{2,}")
_FRONT_MATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.S)


@dataclass
class Card:
    id: str
    kind: str = ""
    label: str = ""
    summary: str = ""
    body: str = ""
    path: str | None = None
    sources: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)

    def as_dict(self, *, with_body: bool = True, max_body: int = 4000) -> dict[str, Any]:
        d = {"id": self.id, "kind": self.kind, "label": self.label,
             "summary": self.summary, "sources": self.sources, "tags": self.tags}
        if with_body:
            d["body"] = self.body[:max_body]
            d["truncated"] = len(self.body) > max_body
        return d


@dataclass(frozen=True)
class GraphNode:
    id: str
    kind: str = ""
    label: str = ""


@dataclass(frozen=True)
class GraphEdge:
    source: str
    target: str
    label: str = ""


@dataclass
class SearchHit:
    card: Card
    score: float


class CardStore:
    """Read-only view over one KB root. Loads lazily; ``reload()`` refreshes."""

    def __init__(self, root: Path | str):
        self.root = Path(root)
        self._loaded = False
        self._cards: dict[str, Card] = {}
        self._nodes: dict[str, GraphNode] = {}
        self._out: dict[str, list[GraphEdge]] = defaultdict(list)
        self._in: dict[str, list[GraphEdge]] = defaultdict(list)
        self._evidence: dict[str, list[dict]] = defaultdict(list)
        # lexical index
        self._df: Counter[str] = Counter()
        self._tf: dict[str, Counter[str]] = {}
        self._len: dict[str, int] = {}
        self._avg_len = 0.0

    # -- loading -----------------------------------------------------------
    @property
    def exists(self) -> bool:
        return self.root.is_dir() and (
            (self.root / "knowledge").is_dir() or (self.root / "cards.jsonl").is_file()
        )

    def _ensure(self) -> None:
        if not self._loaded:
            self.reload()

    def reload(self) -> "CardStore":
        self._cards.clear(); self._nodes.clear(); self._out.clear(); self._in.clear()
        self._evidence.clear(); self._df.clear(); self._tf.clear(); self._len.clear()
        if self.root.is_dir():
            self._load_cards_md(self.root / "knowledge")
            self._load_cards_jsonl(self.root / "cards.jsonl")
            self._load_graph(self.root / "knowledge" / "ontology" / "graph.json")
            self._load_graph(self.root / "graph.json")          # flat copy, if any
            self._load_evidence(self.root / "evidence" / "evidence-map.jsonl")
        # every graph node with no card still gets a stub so neighbours resolve
        for nid, node in self._nodes.items():
            self._cards.setdefault(nid, Card(id=nid, kind=node.kind, label=node.label))
        self._build_index()
        self._loaded = True
        logger.info("CardStore %s: %d cards, %d nodes, %d edges, %d evidence entries",
                    self.root, len(self._cards), len(self._nodes),
                    sum(len(v) for v in self._out.values()),
                    sum(len(v) for v in self._evidence.values()))
        return self

    def _load_cards_md(self, knowledge: Path) -> None:
        if not knowledge.is_dir():
            return
        for path in sorted(knowledge.rglob("*.md")):
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for card in _parse_card_markdown(text, path):
                self._cards[card.id] = card

    def _load_cards_jsonl(self, path: Path) -> None:
        if not path.is_file():
            return
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except ValueError:
                continue
            cid = str(d.get("id") or "").strip()
            if not cid:
                continue
            existing = self._cards.get(cid)
            card = Card(
                id=cid, kind=str(d.get("kind") or d.get("type") or (existing.kind if existing else "")),
                label=str(d.get("label") or d.get("title") or d.get("name") or (existing.label if existing else "")),
                summary=str(d.get("summary") or (existing.summary if existing else "")),
                body=str(d.get("body") or d.get("text") or (existing.body if existing else "")),
                path=(existing.path if existing else str(path)),
                sources=list(d.get("sources") or d.get("source_docs") or (existing.sources if existing else [])),
                tags=list(d.get("tags") or (existing.tags if existing else [])),
                meta={k: v for k, v in d.items() if k not in
                      ("id", "kind", "type", "label", "title", "name", "summary", "body", "text",
                       "sources", "source_docs", "tags")},
            )
            self._cards[cid] = card

    def _load_graph(self, path: Path) -> None:
        if not path.is_file():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except ValueError as exc:
            logger.warning("graph.json unreadable at %s: %s", path, exc)
            return
        for n in data.get("nodes") or []:
            nid = str(n.get("id") or "").strip()
            if not nid:
                continue
            self._nodes[nid] = GraphNode(id=nid, kind=str(n.get("kind") or n.get("type") or ""),
                                         label=str(n.get("label") or n.get("name") or nid))
        for e in data.get("edges") or data.get("links") or []:
            s, t = str(e.get("source") or e.get("from") or ""), str(e.get("target") or e.get("to") or "")
            if not (s and t):
                continue
            edge = GraphEdge(source=s, target=t,
                             label=str(e.get("label") or e.get("type") or e.get("kind") or "RELATED"))
            self._out[s].append(edge)
            self._in[t].append(edge)

    def _load_evidence(self, path: Path) -> None:
        if not path.is_file():
            return
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except ValueError:
                continue
            cid = str(d.get("id") or d.get("card_id") or "").strip()
            if cid:
                self._evidence[cid].append(d)

    def _build_index(self) -> None:
        total = 0
        for cid, card in self._cards.items():
            tokens = _tokens(f"{card.id} {card.label} {card.summary} {card.body} {' '.join(card.tags)}")
            tf = Counter(tokens)
            self._tf[cid] = tf
            self._len[cid] = len(tokens)
            total += len(tokens)
            for term in tf:
                self._df[term] += 1
        self._avg_len = (total / len(self._cards)) if self._cards else 0.0

    # -- reads -------------------------------------------------------------
    def ids(self) -> set[str]:
        self._ensure()
        return set(self._cards)

    def prefixes(self) -> set[str]:
        """Id prefixes present (``ENT``, ``BR``…) — used to recognise citations."""
        self._ensure()
        return {cid.split("-", 1)[0] for cid in self._cards}

    def get(self, card_id: str) -> Card | None:
        self._ensure()
        return self._cards.get(card_id.strip())

    def evidence_for(self, card_id: str) -> list[dict]:
        self._ensure()
        return list(self._evidence.get(card_id.strip(), []))

    def neighbors(self, node_id: str, *, depth: int = 1, direction: str = "both",
                  max_nodes: int = 60) -> tuple[list[GraphNode], list[GraphEdge]]:
        """Typed-edge neighbourhood of a node (breadth-first, bounded)."""
        self._ensure()
        node_id = node_id.strip()
        seen: dict[str, GraphNode] = {}
        edges: list[GraphEdge] = []
        frontier = [node_id]
        start = self._nodes.get(node_id) or (
            GraphNode(id=node_id, kind=self._cards[node_id].kind, label=self._cards[node_id].label)
            if node_id in self._cards else None)
        if start is None:
            return [], []
        seen[node_id] = start
        for _ in range(max(1, depth)):
            nxt: list[str] = []
            for nid in frontier:
                cand: list[GraphEdge] = []
                if direction in ("both", "out"):
                    cand += self._out.get(nid, [])
                if direction in ("both", "in"):
                    cand += self._in.get(nid, [])
                for e in cand:
                    other = e.target if e.source == nid else e.source
                    if e not in edges:
                        edges.append(e)
                    if other not in seen:
                        seen[other] = self._nodes.get(other) or GraphNode(
                            id=other, kind=self._cards.get(other, Card(id=other)).kind,
                            label=self._cards.get(other, Card(id=other, label=other)).label)
                        nxt.append(other)
                    if len(seen) >= max_nodes:
                        break
                if len(seen) >= max_nodes:
                    break
            frontier = nxt
            if not frontier or len(seen) >= max_nodes:
                break
        return list(seen.values()), edges

    def search(self, query: str, *, top_k: int = 8, kinds: Iterable[str] | None = None) -> list[SearchHit]:
        """BM25 over id/label/summary/body. Exact id mentions in the query win outright."""
        self._ensure()
        if not self._cards:
            return []
        wanted = {k.upper() for k in kinds} if kinds else None
        hits: dict[str, float] = {}
        # direct id references
        for cid in CARD_ID_RE.findall(query or ""):
            if cid in self._cards:
                hits[cid] = hits.get(cid, 0.0) + 100.0
        q_terms = _tokens(query or "")
        n = len(self._cards)
        k1, b = 1.5, 0.75
        for cid, tf in self._tf.items():
            if wanted and (self._cards[cid].kind or "").upper() not in wanted \
                    and cid.split("-", 1)[0] not in wanted:
                continue
            score = 0.0
            dl = self._len[cid] or 1
            for term in q_terms:
                f = tf.get(term)
                if not f:
                    continue
                df = self._df.get(term, 0)
                idf = math.log(1 + (n - df + 0.5) / (df + 0.5))
                score += idf * (f * (k1 + 1)) / (f + k1 * (1 - b + b * dl / (self._avg_len or 1)))
            if score > 0:
                hits[cid] = hits.get(cid, 0.0) + score
        ranked = sorted(hits.items(), key=lambda kv: kv[1], reverse=True)[:top_k]
        return [SearchHit(card=self._cards[cid], score=round(s, 4)) for cid, s in ranked]

    def stats(self) -> dict[str, Any]:
        self._ensure()
        kinds = Counter((c.kind or c.id.split("-", 1)[0]) for c in self._cards.values())
        return {
            "root": str(self.root), "exists": self.exists,
            "cards": len(self._cards), "nodes": len(self._nodes),
            "edges": sum(len(v) for v in self._out.values()),
            "evidence_entries": sum(len(v) for v in self._evidence.values()),
            "kinds": dict(kinds.most_common()),
        }

    # -- grounding ---------------------------------------------------------
    def check_citations(self, text: str) -> "GroundingReport":
        """Which card ids `text` cites, and which of them the store knows.

        Only ids in **citation position** count -- inside square brackets, the
        syntax every prompt asks for (``[BR-UWCR-0007]``, ``[SCR-001, API-002]``).
        A document also *authors* ids of its own (``### FR-AUTH-01 — …`` in a
        BRD, ``BR-BL-002`` in a rules catalogue) that merely share a prefix with
        the KB; those are definitions, not claims about the KB, and counting
        them made every well-formed BRD look 85% fabricated.
        """
        self._ensure()
        prefixes = self.prefixes()
        cited: list[str] = []
        for group in CITATION_RE.findall(text or ""):
            for cid in CARD_ID_RE.findall(group):
                if cid.split("-", 1)[0] in prefixes and cid not in cited:
                    cited.append(cid)
        known = [c for c in cited if c in self._cards]
        unknown = [c for c in cited if c not in self._cards]
        return GroundingReport(cited=cited, known=known, unknown=unknown,
                               store_available=self.exists and bool(self._cards))


@dataclass
class GroundingReport:
    cited: list[str]
    known: list[str]
    unknown: list[str]
    store_available: bool

    @property
    def coverage(self) -> float | None:
        return (len(self.known) / len(self.cited)) if self.cited else None

    @property
    def grounded(self) -> bool:
        """At least one known citation and nothing fabricated."""
        return bool(self.known) and not self.unknown

    def as_dict(self) -> dict[str, Any]:
        return {"cited": self.cited, "known": self.known, "unknown": self.unknown,
                "coverage": self.coverage, "grounded": self.grounded,
                "store_available": self.store_available}


# -- layout normalisation --------------------------------------------------

def normalize_kb_layout(root: Path | str) -> dict[str, Any]:
    """Convert a *variant* KB layout into the canonical one, in place.

    Builders (an LLM session following the AIDLC-kb skill, or another RE engine)
    sometimes emit an equivalent but differently arranged tree:

        cards/<ID>.json | cards/<ID>.md      instead of  knowledge/<kind>/<ID>.md
        graph.json  (from/to/type edges)     instead of  knowledge/ontology/graph.json
        evidence_map.json ({loci:[...]})     instead of  evidence/evidence-map.jsonl
        (no cards.jsonl)

    Rejecting that at the gate throws away paid work; converting it costs
    nothing and keeps every reader on one contract. Canonical files that already
    exist are left alone. Returns a summary of what was written.
    """
    root = Path(root)
    written = {"cards": 0, "graph": False, "evidence": 0, "export": False}
    if not root.is_dir():
        return written
    knowledge = root / "knowledge"
    kind_dir = {"ENT": "entities", "BR": "rules", "FR": "requirements", "SCR": "screens",
                "WF": "workflows", "CMP": "components", "API": "apis"}

    # 1. cards/*.json|md -> knowledge/<kind>/<ID>.md
    cards_dir = root / "cards"
    variant_cards: list[dict[str, Any]] = []
    if cards_dir.is_dir():
        for path in sorted(cards_dir.iterdir()):
            if path.suffix.lower() == ".json":
                try:
                    d = json.loads(path.read_text(encoding="utf-8", errors="replace"))
                except ValueError:
                    continue
                if isinstance(d, dict) and d.get("id"):
                    variant_cards.append(d)
            elif path.suffix.lower() == ".md":
                for card in _parse_card_markdown(path.read_text(encoding="utf-8", errors="replace"), path):
                    variant_cards.append({"id": card.id, "kind": card.kind, "label": card.label,
                                          "summary": card.summary, "body": card.body,
                                          "sources": card.sources, "tags": card.tags})
    for d in variant_cards:
        cid = str(d["id"]).strip()
        kind = str(d.get("kind") or cid.split("-", 1)[0]).upper()
        target = knowledge / kind_dir.get(kind, kind.lower()) / f"{cid}.md"
        if target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        sources = d.get("sources") or [
            f"{e.get('source')}#{e.get('locus')}" for e in (d.get("evidence") or []) if isinstance(e, dict)]
        tags = d.get("tags") or []
        label = str(d.get("label") or d.get("title") or cid).replace("\n", " ")
        summary = str(d.get("summary") or "").replace("\n", " ")
        fm = [f"id: {cid}", f"kind: {kind}", f"label: {_yaml_str(label)}", f"summary: {_yaml_str(summary)}",
              "sources: [" + ", ".join(_yaml_str(str(s)) for s in sources[:12]) + "]",
              "tags: [" + ", ".join(_yaml_str(str(t)) for t in tags) + "]"]
        body = str(d.get("body") or "")
        ev_lines = ""
        if d.get("evidence"):
            ev_lines = "\n\n**Evidence.**\n" + "\n".join(
                f"- {e.get('source')} `{e.get('locus')}`: {str(e.get('quote') or '')[:300]}"
                for e in d["evidence"] if isinstance(e, dict))
        target.write_text("---\n" + "\n".join(fm) + f"\n---\n# {cid} — {label}\n\n{body}{ev_lines}\n",
                          encoding="utf-8")
        written["cards"] += 1

    # 2. graph.json (root) -> knowledge/ontology/graph.json with source/target/label
    canon_graph = knowledge / "ontology" / "graph.json"
    if not canon_graph.exists() and (root / "graph.json").is_file():
        try:
            g = json.loads((root / "graph.json").read_text(encoding="utf-8", errors="replace"))
        except ValueError:
            g = {}
        nodes = [{"id": str(n.get("id")), "kind": str(n.get("kind") or n.get("type") or str(n.get("id")).split("-", 1)[0]),
                  "label": str(n.get("label") or n.get("name") or n.get("id"))}
                 for n in g.get("nodes", []) if n.get("id")]
        edges = [{"source": str(e.get("source") or e.get("from")), "target": str(e.get("target") or e.get("to")),
                  "label": str(e.get("type") or e.get("label") or e.get("kind") or "RELATED").split(" ")[0].upper()}
                 for e in (g.get("edges") or g.get("links") or [])
                 if (e.get("source") or e.get("from")) and (e.get("target") or e.get("to"))]
        # every card is a node
        known = {n["id"] for n in nodes}
        for d in variant_cards:
            cid = str(d["id"]).strip()
            if cid not in known:
                nodes.append({"id": cid, "kind": str(d.get("kind") or cid.split("-", 1)[0]),
                              "label": str(d.get("label") or cid)})
                known.add(cid)
        canon_graph.parent.mkdir(parents=True, exist_ok=True)
        canon_graph.write_text(json.dumps({"nodes": nodes, "edges": edges}, indent=1), encoding="utf-8")
        written["graph"] = True

    # 3. evidence_map.json ({loci:[{card_id,...}]} | list) + per-card evidence -> evidence-map.jsonl
    canon_ev = root / "evidence" / "evidence-map.jsonl"
    if not canon_ev.exists():
        lines: list[str] = []
        raw = None
        for name in ("evidence_map.json", "evidence-map.json", "evidence.json"):
            if (root / name).is_file():
                try:
                    raw = json.loads((root / name).read_text(encoding="utf-8", errors="replace"))
                except ValueError:
                    raw = None
                break
        sources_by_id: dict[str, str] = {}
        entries: list[dict] = []
        if isinstance(raw, dict):
            for s in raw.get("source_corpus") or raw.get("sources") or []:
                if isinstance(s, dict) and s.get("source_id"):
                    sources_by_id[str(s["source_id"])] = str(s.get("path") or s.get("original_file") or s["source_id"])
            entries = list(raw.get("loci") or raw.get("entries") or raw.get("evidence") or [])
        elif isinstance(raw, list):
            entries = raw
        seen: set[tuple] = set()
        for e in entries:
            if not isinstance(e, dict):
                continue
            cid = str(e.get("card_id") or e.get("id") or "").strip()
            if not cid:
                continue
            src = sources_by_id.get(str(e.get("source_id") or ""), e.get("source_doc") or e.get("source") or e.get("path") or "")
            locus = e.get("locus")
            if not isinstance(locus, dict):
                locus = {k: e[k] for k in ("lines", "line", "page", "section", "cell", "sheet") if e.get(k)} \
                    or ({"ref": str(locus)} if locus else {})
            key = (cid, str(src), json.dumps(locus, sort_keys=True))
            if key in seen:
                continue
            seen.add(key)
            lines.append(json.dumps({"id": cid, "source_doc": str(src), "locus": locus,
                                     "snippet": str(e.get("snippet") or e.get("quote") or "")[:400],
                                     "confidence": e.get("confidence", 0.8)}))
        # per-card evidence arrays cover cards the map missed
        for d in variant_cards:
            cid = str(d["id"]).strip()
            for e in d.get("evidence") or []:
                if not isinstance(e, dict):
                    continue
                locus_s = str(e.get("locus") or "")
                locus = {"ref": locus_s} if locus_s else {}
                key = (cid, str(e.get("source") or ""), json.dumps(locus, sort_keys=True))
                if key in seen or not e.get("source"):
                    continue
                seen.add(key)
                lines.append(json.dumps({"id": cid, "source_doc": str(e.get("source")), "locus": locus,
                                         "snippet": str(e.get("quote") or "")[:400], "confidence": 0.8}))
        if lines:
            canon_ev.parent.mkdir(parents=True, exist_ok=True)
            canon_ev.write_text("\n".join(lines) + "\n", encoding="utf-8")
            written["evidence"] = len(lines)

    # 4. cards.jsonl export from the canonical cards
    export = root / "cards.jsonl"
    if not export.exists() and knowledge.is_dir():
        store = CardStore(root).reload()
        rows = [json.dumps(store.get(c).as_dict()) for c in sorted(store.ids())]
        if rows:
            export.write_text("\n".join(rows) + "\n", encoding="utf-8")
            written["export"] = True
    return written


def _yaml_str(s: str) -> str:
    s = s.replace('"', "'").replace("\n", " ")
    return '"' + s + '"'


# -- helpers ---------------------------------------------------------------

def _tokens(text: str) -> list[str]:
    return [t.lower() for t in _WORD_RE.findall(text or "")]


def _parse_front_matter(text: str) -> tuple[dict[str, Any], str]:
    m = _FRONT_MATTER_RE.match(text)
    if not m:
        return {}, text
    raw, body = m.group(1), text[m.end():]
    meta: dict[str, Any] = {}
    try:
        import yaml  # noqa: PLC0415
        loaded = yaml.safe_load(raw)
        if isinstance(loaded, dict):
            meta = loaded
    except Exception:  # noqa: BLE001 — fall back to key: value lines
        for line in raw.splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                meta[k.strip()] = v.strip().strip("'\"")
    return meta, body


_H1_ID_RE = re.compile(r"^#\s+(?:\[?([A-Z]{2,6}(?:-[A-Z0-9]{1,24}){1,5})\]?)\s*[—:-]?\s*(.*)$", re.M)


def _parse_card_markdown(text: str, path: Path) -> list[Card]:
    """One card per file (front-matter id) — or several when the file is a
    catalogue with ``# ID — label`` headings and no front-matter id."""
    meta, body = _parse_front_matter(text)
    cid = str(meta.get("id") or "").strip()
    if cid:
        label = str(meta.get("label") or meta.get("title") or meta.get("name") or "")
        if not label:
            h = re.search(r"^#\s+(.+)$", body, re.M)
            label = h.group(1).strip() if h else cid
        return [Card(
            id=cid, kind=str(meta.get("kind") or meta.get("type") or cid.split("-", 1)[0]),
            label=label, summary=str(meta.get("summary") or ""), body=body.strip(),
            path=str(path),
            sources=[str(s) for s in (meta.get("sources") or meta.get("source_docs") or [])]
            if isinstance(meta.get("sources") or meta.get("source_docs"), list) else
            ([str(meta.get("sources") or meta.get("source_docs"))] if (meta.get("sources") or meta.get("source_docs")) else []),
            tags=[str(t) for t in (meta.get("tags") or [])] if isinstance(meta.get("tags"), list) else [],
            meta={k: v for k, v in meta.items()
                  if k not in ("id", "kind", "type", "label", "title", "name", "summary", "sources",
                               "source_docs", "tags")},
        )]
    # catalogue file: split on H1 headings that start with an id
    cards: list[Card] = []
    matches = list(_H1_ID_RE.finditer(body))
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        section = body[m.end():end].strip()
        cid = m.group(1)
        label = (m.group(2) or "").strip() or cid
        first_para = section.split("\n\n", 1)[0].strip() if section else ""
        cards.append(Card(id=cid, kind=cid.split("-", 1)[0], label=label,
                          summary=first_para[:400], body=section, path=str(path)))
    return cards
