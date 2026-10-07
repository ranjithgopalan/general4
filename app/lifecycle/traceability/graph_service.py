"""Item-level trace graph (RTM Phase 2).

Assembles a workspace's ITEM-level traceability graph — the requirement → systems → rules →
stories → dev-files → tests chain — from the stored stage artifacts (JSON) plus the item-level
GROUNDS rows (``fe_artifact_kb_grounds.source_item_id``).

Unlike ``fe_artifact_relationships`` (artifact→artifact) and the raw grounds table (artifact→KB),
this produces a graph whose nodes are individual items (a specific business rule, a specific story,
a specific test case) so the UI can trace/highlight a single item forward and backward.

Design notes:
- Read-only + defensive: every leg reads the artifact JSON with ``.get`` fallbacks, so a missing or
  renamed field yields no edges for that leg (never a crash). ``meta.legs`` reports what was built.
- All join keys are closed within artifact data — each artifact stage writes back the same IDs it
  read from the prior stage (requirement → systems → rules → stories → files → tests). Rule IDs are
  workspace-minted (e.g. PROP-FR-001) and do not exist in the KB graph; connecting them via KB graph
  expansion would always fail. The ``requirement → rule`` edge is the correct join: all FSD/SRD rules
  are produced by the requirement, matching the same pattern as ``requirement → system`` (leg 2).
"""

from __future__ import annotations

import json
from typing import Any

from app.config import get_settings
from app.utils.logging import log

# Node "levels" in SDLC order — used by the UI to lane the graph left→right.
LEVELS = ["requirement", "system", "rule", "story", "file", "test"]


class TraceGraphService:
    """Build the item-level trace graph for a workspace (RTM P2)."""

    def __init__(self, dao: Any, graph_provider: Any | None = None) -> None:
        # ``dao`` only needs ``fetch_all`` (BaseRepository) — reused for artifact + grounds queries.
        self._dao = dao
        self._graph = graph_provider

    # ── public ────────────────────────────────────────────────────────────────────
    async def build_item_graph(self, workspace_id: str) -> dict[str, Any]:
        """Return ``{nodes, edges, meta}`` — the item-level trace graph for the workspace."""
        arts = await self._load_artifacts(workspace_id)
        nodes: dict[str, dict[str, Any]] = {}
        edges: list[dict[str, str]] = []
        legs: list[str] = []

        def add_node(nid: str, label: str, kind: str, level: str) -> str | None:
            if not nid:
                return None
            if nid not in nodes:
                nodes[nid] = {"id": nid, "label": (label or nid), "kind": kind, "level": level}
            return nid

        def add_edge(src: str | None, dst: str | None, etype: str) -> None:
            if src and dst and src != dst:
                edges.append({"from": src, "to": dst, "type": etype})

        analysis = arts.get("analysis") or {}
        fsd = arts.get("fsd") or {}
        stories = arts.get("stories") or {}
        dev = arts.get("dev") or {}
        qa = arts.get("test-plan") or {}
        dev_pr = arts.get("dev-pr") or {}
        qa_offline = arts.get("qa_offline") or {}

        # 1) requirement (root) ------------------------------------------------------
        req_text = (analysis.get("requirement") or "").strip() or "Requirement"
        req_id = add_node("REQ", self._clip(req_text, 120), "Requirement", "requirement")

        # 2) systems — SYS-* nodes only (no components) ----------------------------
        system_ids: list[str] = []
        for s in self._as_list(analysis.get("systems_affected")):
            sid = s.get("id") or s.get("system_id")
            if add_node(sid, s.get("system_name") or s.get("label") or sid, "System", "system"):
                system_ids.append(sid)
                add_edge(req_id, sid, "affects")
        if system_ids:
            legs.append("requirement->system")

        # rule_ids collected across legs 3 and 3.5 before edges are drawn
        rule_ids: set[str] = set()

        # 3) code components → rule lane (not system lane) -------------------------
        # CMP/API nodes are implementable artefacts, not architectural boundaries —
        # they belong alongside business rules in the rule lane.  Keeping them in
        # system_ids would draw spurious system→component edges for every system.
        for c in self._as_list(analysis.get("code_affected")):
            cid = c.get("id")
            if cid and add_node(cid, c.get("label") or cid, "Component", "rule"):
                rule_ids.add(cid)

        # 3.5) FSD/SRD business rules + functional requirements --------------------
        for r in self._as_list(fsd.get("business_rules")):
            rid = r.get("id")
            if rid and add_node(rid, r.get("statement") or r.get("label") or rid, "BusinessRule", "rule"):
                rule_ids.add(rid)
        for fr in self._as_list(fsd.get("functional_requirements")):
            frid = fr.get("id")
            if frid and add_node(frid, fr.get("title") or fr.get("label") or frid, "FunctionalReq", "rule"):
                rule_ids.add(frid)

        # 3b) system → rule edges --------------------------------------------------
        # Two strategies by ID type:
        #
        # • KB-ID nodes (SCR-JAUTO-*, CMP-JAUTO-*, BR-JAUTO-*, etc.) have real edges
        #   in the KB graph.  Query graph.neighbors() per node and draw only the
        #   system edges that actually exist (e.g. SCR-JAUTO-AU-RN-013 → Angular UI,
        #   AIG Connect, Java Spring Boot; WEB_RECEIPTS entity → ASACDP).
        #
        # • PROP-* nodes are workspace-minted and absent from the KB graph.  Use
        #   all-pairs (every system governs every PROP rule) since no finer data exists.
        #
        # Falls back to REQ→RULE when no system nodes are present.
        if rule_ids and system_ids:
            prop_rule_ids = {rid for rid in rule_ids if rid.upper().startswith("PROP-")}
            kb_rule_ids   = rule_ids - prop_rule_ids

            # KB-ID rules: specific connections from KB graph
            if kb_rule_ids and self._graph is not None:
                try:
                    for rid in kb_rule_ids:
                        sub = await self._graph.neighbors(rid, direction="both", depth=1)
                        connected_sys = {n.id for n in sub.nodes if n.kind in ("System", "SYS")}
                        matched = [sid for sid in system_ids if sid in connected_sys]
                        targets = matched if matched else system_ids  # fallback: all-pairs
                        for sid in targets:
                            add_edge(sid, rid, "governs")
                except Exception as exc:  # noqa: BLE001
                    log.debug(f"[trace-graph] KB graph neighbor lookup failed: {exc}")
                    for sid in system_ids:
                        for rid in kb_rule_ids:
                            add_edge(sid, rid, "governs")

            # PROP-* rules: all-pairs (no KB graph data available)
            for sid in system_ids:
                for rid in prop_rule_ids:
                    add_edge(sid, rid, "governs")

            if rule_ids:
                legs.append("system->rule")

        elif rule_ids:
            for rid in rule_ids:
                add_edge(req_id, rid, "requires")
            legs.append("requirement->rule (no systems)")

        # 4) stories + rule → story --------------------------------------------------
        story_ids: list[str] = []
        for st in self._as_list(stories.get("stories")):
            sid = st.get("story_id")
            if not add_node(sid, st.get("title") or sid, "Story", "story"):
                continue
            story_ids.append(sid)
            # rule → story: each KB ref the story implements (screens/rules/FRs)
            for ref in self._as_list(st.get("source_refs")):
                ref_id = ref if isinstance(ref, str) else ref.get("id")
                # ensure the referenced item exists as a node even if it wasn't in the PRD rule set
                add_node(ref_id, ref_id, self._kind_of(ref_id), self._level_of(ref_id))
                add_edge(ref_id, sid, "implemented_by")
        if story_ids:
            legs.append("rule->story")

        # 4b) connect SCR-*/KB-ID nodes added by story source_refs to their systems
        # These nodes arrive after leg 3b so they miss the system→rule pass above.
        # Use KB graph neighbors to find specific system connections (same logic as 3b).
        if system_ids and self._graph is not None:
            late_kb_ids = {
                nid for nid, n in nodes.items()
                if n["level"] == "rule"
                and nid not in rule_ids          # added after leg 3b
                and not nid.upper().startswith("PROP-")
                and nid not in story_ids
            }
            if late_kb_ids:
                try:
                    for rid in late_kb_ids:
                        sub = await self._graph.neighbors(rid, direction="both", depth=1)
                        connected_sys = {n.id for n in sub.nodes if n.kind in ("System", "SYS")}
                        matched = [sid for sid in system_ids if sid in connected_sys]
                        targets = matched if matched else system_ids
                        for sid in targets:
                            add_edge(sid, rid, "governs")
                    legs.append("system->rule (source-ref KB nodes)")
                except Exception as exc:  # noqa: BLE001
                    log.debug(f"[trace-graph] late KB rule lookup failed: {exc}")

        # 5) dev files → linked to the producing story when it carries story_id (code_stubs,
        #    codegen_files); change_specs/change_plan have no story_id, so link their files to the
        #    rules/components they implement (kb_ids / component_id) — the chain still closes via
        #    rule→story. Files are deduped by path across collections.
        file_added = False

        def link_files(files: list[str], story_id: str | None, kb_refs: list[str]) -> None:
            nonlocal file_added
            for fpath in files:
                fid = "FILE:" + fpath
                add_node(fid, fpath.split("/")[-1], "File", "file")
                if story_id:
                    add_edge(story_id, fid, "produces")
                for ref in kb_refs:
                    if ref:
                        add_node(ref, ref, self._kind_of(ref), self._level_of(ref))
                        add_edge(ref, fid, "produces")
                file_added = True

        for cf in self._as_list(dev.get("codegen_files")):
            link_files(self._file_names(cf, "file_path"), cf.get("story_id"), [])
        for cs in self._as_list(dev.get("code_stubs")):
            link_files(self._file_names(cs, "filename"), cs.get("story_id"), [cs.get("component_id")])
        for spec in self._as_list(dev.get("change_specs")):
            link_files(self._file_names(spec, "target_files"), spec.get("story_id"),
                       [k for k in (spec.get("kb_ids") or []) if isinstance(k, str)])
        for cp in self._as_list(dev.get("change_plan")):
            link_files(self._file_names(cp, "file"), cp.get("story_id"), [cp.get("component_id")])
        if file_added:
            legs.append("story/rule->file")

        # 5b) dev-pr committed files → story (PR-level code traceability) ----------
        for fpath in (dev_pr.get("files_changed") or []):
            fid = "FILE:" + fpath
            add_node(fid, fpath.split("/")[-1], "File", "file")
            if story_ids:
                for sid in story_ids:
                    add_edge(sid, fid, "produces")
            else:
                add_edge(req_id, fid, "produces")
        if dev_pr.get("files_changed"):
            legs.append("dev-pr->file")

        # 6) tests + story/rule → test ----------------------------------------------
        test_added = False
        for tc in self._as_list(qa.get("story_test_cases")):
            tid = tc.get("id")
            if not add_node(tid, tc.get("title") or tid, "Test", "test"):
                continue
            add_edge(tc.get("story_id"), tid, "tested_by")
            if tc.get("source_ref"):
                add_edge(tc.get("source_ref"), tid, "tested_by")
            test_added = True
        for brt in self._as_list(qa.get("business_rule_tests")):
            tid = brt.get("id") or ("BRT:" + str(brt.get("rule_id")))
            if not add_node(tid, brt.get("title") or brt.get("scenario") or tid, "Test", "test"):
                continue
            add_edge(brt.get("rule_id"), tid, "tested_by")
            test_added = True
        # 6b) dev-pr tests_written → story (PR-level test traceability) -------------
        for tw in self._as_list(dev_pr.get("tests_written") or []):
            test_file = tw.get("test_file") if isinstance(tw, dict) else None
            if not test_file:
                continue
            tid = "FILE:" + test_file
            add_node(tid, test_file.split("/")[-1], "Test", "test")
            story_ref = tw.get("story_id") if isinstance(tw, dict) else None
            if story_ref:
                add_edge(story_ref, tid, "tested_by")
            elif story_ids:
                for sid in story_ids:
                    add_edge(sid, tid, "tested_by")
            test_added = True

        # 6c) qa_offline test_cases → story (offline QA traceability) ---------------
        for tc in self._as_list(qa_offline.get("test_cases") or []):
            tid = tc.get("test_id") or tc.get("id")
            if not add_node(tid, tc.get("scenario") or tc.get("title") or tid, "Test", "test"):
                continue
            for s_id in (tc.get("tested_stories") or []):
                add_edge(s_id, tid, "tested_by")
            if not tc.get("tested_stories") and story_ids:
                for sid in story_ids:
                    add_edge(sid, tid, "tested_by")
            test_added = True

        if test_added:
            legs.append("story/rule->test")

        return {
            "workspace_id": workspace_id,
            "nodes": list(nodes.values()),
            "edges": edges,
            "meta": {
                "node_count": len(nodes),
                "edge_count": len(edges),
                "levels": LEVELS,
                "legs": legs,
            },
        }

    async def item_neighbors(self, workspace_id: str, item_id: str) -> dict[str, Any]:
        """The connected subgraph for one item — its ancestors (upstream) + descendants (downstream).

        Powers the "highlight everything related to this item" interaction (both directions).
        """
        graph = await self.build_item_graph(workspace_id)
        out_adj: dict[str, list[str]] = {}
        in_adj: dict[str, list[str]] = {}
        for e in graph["edges"]:
            out_adj.setdefault(e["from"], []).append(e["to"])
            in_adj.setdefault(e["to"], []).append(e["from"])

        def bfs(adj: dict[str, list[str]]) -> set[str]:
            seen: set[str] = set()
            frontier = [item_id]
            while frontier:
                nxt: list[str] = []
                for n in frontier:
                    for m in adj.get(n, []):
                        if m not in seen:
                            seen.add(m)
                            nxt.append(m)
                frontier = nxt
            return seen

        descendants = bfs(out_adj)
        ancestors = bfs(in_adj)
        related = {item_id} | descendants | ancestors
        return {
            "workspace_id": workspace_id,
            "item_id": item_id,
            "ancestors": sorted(ancestors),
            "descendants": sorted(descendants),
            "related": sorted(related),
        }

    # ── internals ───────────────────────────────────────────────────────────────
    async def _load_artifacts(self, workspace_id: str) -> dict[str, dict[str, Any]]:
        """Latest artifact CONTENT (parsed JSON) per kind for the workspace."""
        schema = get_settings().PG_SCHEMA
        rows = await self._dao.fetch_all(
            f"""
            SELECT DISTINCT ON (kind) kind, content
            FROM {schema}.fe_workspace_artifacts
            WHERE workspace_id = %s
            ORDER BY kind, created_at DESC
            """,
            (workspace_id,),
        )
        out: dict[str, dict[str, Any]] = {}
        for r in rows:
            content = r.get("content")
            if not content:
                continue
            try:
                out[r["kind"]] = content if isinstance(content, dict) else json.loads(content)
            except (ValueError, TypeError):
                continue
        return out

    @staticmethod
    def _as_list(v: Any) -> list[dict[str, Any]]:
        return [x for x in v if isinstance(x, (dict, str))] if isinstance(v, list) else []

    @staticmethod
    def _clip(text: str, n: int) -> str:
        return text if len(text) <= n else text[: n - 1] + "…"

    @staticmethod
    def _file_names(item: dict[str, Any], file_key: str | None) -> list[str]:
        if not file_key:
            return []
        val = item.get(file_key)
        if isinstance(val, str) and val:
            return [val]
        if isinstance(val, list):
            return [f for f in val if isinstance(f, str) and f]
        return []

    @staticmethod
    def _kind_of(kb_id: str | None) -> str:
        p = (kb_id or "").split("-", 1)[0].upper()
        return {"BR": "BusinessRule", "FR": "FunctionalReq", "SCR": "Screen", "SYS": "System",
                "WF": "Workflow", "PROC": "Process", "ENT": "Entity"}.get(p, "KbCard")

    @staticmethod
    def _level_of(kb_id: str | None) -> str:
        p = (kb_id or "").split("-", 1)[0].upper()
        return "system" if p == "SYS" else "rule"
