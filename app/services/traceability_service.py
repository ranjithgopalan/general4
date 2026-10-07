"""Traceability service (docs/19 §7) — the two-edge trace plumbing.

Writes **GROUNDS** (artifact -> KB card) and **DERIVES_FROM** (artifact -> upstream artifact) via the
``ArtifactRepository``, and assembles a walkable trace graph for a workspace (artifacts + KB cards as
nodes; GROUNDS/DERIVES_FROM as edges). Framework only — the (future) persona nodes emit the links.
Projecting these edges into the shared ``GraphProvider`` under ``kind='trace'`` is the RE-side
integration point (deferred with the KB graph).
"""

from __future__ import annotations

from typing import Any

from app.dao.workspace_dao import ArtifactRepository
from app.models.workspace import TraceEdge, TraceGraph, TraceNode


class TraceabilityService:
    """Write + read the workspace traceability edges (over ``fe_artifact_links``)."""

    def __init__(self, artifacts: ArtifactRepository | None = None) -> None:
        self._art = artifacts or ArtifactRepository()

    async def add_grounds(
        self,
        *,
        workspace_id: str,
        from_artifact_id: str,
        persona: str,
        artifact_kind: str,
        to_kb_card_id: str,
        kb_version: str,
        stage: str | None = None,
        source_locus: str | None = None,
        char_span: str | None = None,
        applied_because: str | None = None,
    ) -> dict[str, Any] | None:
        return await self._art.add_grounds_link(
            workspace_id=workspace_id,
            from_artifact_id=from_artifact_id,
            persona=persona,
            artifact_kind=artifact_kind,
            to_kb_card_id=to_kb_card_id,
            kb_version=kb_version,
            stage=stage,
            source_locus=source_locus,
            char_span=char_span,
            applied_because=applied_because,
        )

    async def add_derives(
        self,
        *,
        workspace_id: str,
        from_artifact_id: str,
        persona: str,
        artifact_kind: str,
        to_artifact_id: str,
        stage: str | None = None,
        diagram_ref: str | None = None,
        reason: str | None = None,
    ) -> dict[str, Any] | None:
        return await self._art.add_derives_link(
            workspace_id=workspace_id,
            from_artifact_id=from_artifact_id,
            persona=persona,
            artifact_kind=artifact_kind,
            to_artifact_id=to_artifact_id,
            stage=stage,
            diagram_ref=diagram_ref,
        )

    async def get_tracing_result(self, workspace_id: str):
        """Stub — returns None so the API falls back to the empty-graph response dict."""
        return None

    async def trace_graph(self, workspace_id: str) -> TraceGraph:
        """Assemble the walkable trace graph: artifacts + cited KB cards as nodes; links as edges."""
        artifacts = await self._art.list_for_workspace(workspace_id)
        links = await self._art.list_links(workspace_id)

        nodes: dict[str, TraceNode] = {
            a["artifact_id"]: TraceNode(id=a["artifact_id"], kind="artifact", label=a.get("kind")) for a in artifacts
        }
        edges: list[TraceEdge] = []
        for lk in links:
            frm = lk["from_artifact_id"]
            nodes.setdefault(frm, TraceNode(id=frm, kind="artifact"))
            if lk["link_type"] == "GROUNDS":
                target = lk["to_kb_card_id"]
                nodes.setdefault(target, TraceNode(id=target, kind="kb_card", label=lk.get("kb_version")))
                edges.append(
                    TraceEdge(
                        from_id=frm,
                        to_id=target,
                        link_type="GROUNDS",
                        stage=lk.get("stage"),
                        source_locus=lk.get("source_locus"),
                    )
                )
            else:
                target = lk["to_artifact_id"]
                nodes.setdefault(target, TraceNode(id=target, kind="artifact"))
                edges.append(TraceEdge(from_id=frm, to_id=target, link_type="DERIVES_FROM", stage=lk.get("stage")))

        return TraceGraph(workspace_id=workspace_id, nodes=list(nodes.values()), edges=edges)
