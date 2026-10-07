"""
GapService (docs/25 Phase 1) — read-only aggregation of every gap source for a kb_version.

Deterministic collectors, DB-first (works centrally with no local kb/): isolated nodes + ungrounded cards
come from the DB; gate results + review-queue are best-effort file reads (skipped if absent). No writes —
Phase 1 only surfaces gaps; disposition (false-positive/resolve) + the promote gate are Phase 2.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from psycopg.rows import dict_row

from app.dao import graph_dao, review_dao
from app.dao.postgres import get_pool
from app.models.review import GapItem, KbReview

# Families that are legitimately edge-less (not a graph gap): NFR-style FRs, glossary/segmentation terms.
_ISOLATED_OK = ("TERM", "ALIAS", "DOM")
_KB_DIR = os.getenv("KB_SOURCE_DIR", "kb")  # best-effort file reads only (central-safe: skipped if absent)


class GapService:
    """Aggregate the pre-activation gap ledger for a kb_version (read-only)."""

    async def review(self, kb_version: str) -> KbReview:
        pool = get_pool()
        # A forward-engineered (workspace-sync) build carries a ``.ws-`` suffix and is a FULL snapshot
        # (base KB + the workspace delta). Its review must show only the workspace's own gaps — the
        # forward-origin nodes/cards — not the inherited base-KB isolated nodes / ungrounded aliases.
        forward_only = ".ws-" in kb_version
        gaps: list[GapItem] = []
        gaps += await self._isolated_nodes(pool, kb_version, forward_only)
        gaps += await self._ungrounded_cards(pool, kb_version, forward_only)
        if not forward_only:
            # File-based gate results + review-queue are RE base-KB scoped, not workspace-specific.
            gaps += self._gate_failures()
            gaps += self._review_queue()
        # Phase 2: merge stored dispositions (false-positive / resolved / SME-confirmed) over the live gaps
        disp = await review_dao.dispositions(pool, kb_version)
        for g in gaps:
            d = disp.get(g.gap_id)
            if d:
                g.status = d.get("status", "OPEN")
                g.note = d.get("note")
                g.disposed_by = d.get("disposed_by")
                g.artifact_ref = d.get("artifact_ref")  # Phase 3 #11 — add-artifact linkage
        open_gaps = [g for g in gaps if g.status == "OPEN"]
        blocking_open = sum(1 for g in open_gaps if g.severity == "blocking")
        summary = {
            "total": len(gaps),
            "blocking": sum(1 for g in gaps if g.severity == "blocking"),
            "soft": sum(1 for g in gaps if g.severity == "soft"),
            "info": sum(1 for g in gaps if g.severity == "info"),
            "open": len(open_gaps),
            "dispositioned": len(gaps) - len(open_gaps),
        }
        status = await self._status(pool, kb_version)
        return KbReview(kb_version=kb_version, status=status, summary=summary, gaps=gaps,
                        blocking_open=blocking_open, promotable=(blocking_open == 0))

    # ── DB-derived collectors (always available) ─────────────────────────────────
    async def _isolated_nodes(self, pool: Any, version: str, forward_only: bool = False) -> list[GapItem]:
        nodes = await graph_dao.fetch_nodes(pool, version)
        edges = await graph_dao.fetch_edges(pool, version)
        # "touched" is computed over ALL edges (a forward node linked to a base node is not isolated).
        touched = {e.get("from_id") for e in edges} | {e.get("to_id") for e in edges}
        out = []
        for n in nodes:
            nid = n["id"]
            fam = nid.split("-", 1)[0].upper()
            if nid in touched or fam in _ISOLATED_OK:
                continue
            if forward_only and (n.get("metadata") or {}).get("origin") != "forward":
                continue  # base-KB node inherited by this snapshot — not this workspace's gap
            out.append(GapItem(gap_id=f"isolated_node:{nid}", type="isolated_node", severity="soft",
                               title=f"Isolated node: {n.get('label') or nid}",
                               detail=f"{fam} node with no graph edges (may be a missing relationship).",
                               source_ref=nid))
        return out

    async def _ungrounded_cards(self, pool: Any, version: str, forward_only: bool = False) -> list[GapItem]:
        sql = ("SELECT id, kind, label FROM fe_kb_cards WHERE kb_version = %s "
               "AND (source_locus IS NULL OR btrim(source_locus) = '')")
        if forward_only:
            sql += " AND metadata->>'origin' = 'forward'"  # workspace-delta cards only
        async with pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(sql, (version,))
            rows = await cur.fetchall()
        out = []
        for r in rows:
            blocking = r["kind"] in ("BusinessRule", "FunctionalReq")
            out.append(GapItem(gap_id=f"ungrounded_card:{r['id']}", type="ungrounded_card",
                               severity="blocking" if blocking else "soft",
                               title=f"Ungrounded {r['kind']}: {r.get('label') or r['id']}",
                               detail="Card has no source_locus (cite-or-abstain violation).",
                               source_ref=r["id"]))
        return out

    async def _status(self, pool: Any, version: str) -> str:
        async with pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            await cur.execute("SELECT status FROM fe_kb_versions WHERE kb_version = %s", (version,))
            row = await cur.fetchone()
        return (row or {}).get("status", "") if row else ""

    # ── file-based collectors (best-effort; central-safe) ────────────────────────
    def _gate_failures(self) -> list[GapItem]:
        p = Path(_KB_DIR) / "_gate-results.json"
        if not p.is_file():
            return []
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return []
        results = data.get("gate_results") or data if isinstance(data, dict) else {}
        out = []
        for gid, verdict in (results.items() if isinstance(results, dict) else []):
            if isinstance(verdict, str) and verdict.upper() not in ("PASS", "OK"):
                out.append(GapItem(gap_id=f"gate_failure:{gid}", type="gate_failure",
                                   severity="blocking" if verdict.upper() in ("FAIL", "FABRICATED", "ERROR") else "soft",
                                   title=f"Gate {gid}: {verdict}", detail="Deterministic gate not PASS.",
                                   source_ref=gid))
        return out

    def _review_queue(self) -> list[GapItem]:
        p = Path(_KB_DIR) / "_review-queue.md"
        if not p.is_file():
            return []
        out = []
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            m = re.match(r"^#{2,4}\s+(.+)", line.strip())
            if m:
                title = m.group(1).strip()
                sev = "soft"
                out.append(GapItem(gap_id=f"review_queue:{title[:48]}", type="review_queue", severity=sev,
                                   title=title, detail="Open item in the review queue (SME/tuning).",
                                   source_ref="_review-queue.md"))
        return out[:50]
