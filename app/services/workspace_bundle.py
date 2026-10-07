"""Workspace bundle — the single grounded read the local Developer plugin fetches by ``workspace_id``.

docs/27 §6.1 (P1). Aggregates the accepted upstream artifacts (impact / FSD / SRD / stories / dev) plus
the developer change-specs, the union of real ``target_files``, subsystem ``repo_hints`` (for the plugin's
corpus-prefix → local-repo-root map — central emits prefixes, never local paths), and the KB citations
that ground the change. Pure composition over the existing per-stage services — no new persistence.

The state model is unchanged: the DB (``fe_workspaces`` + ``fe_workspace_artifacts``) is the source of
truth; this is a read that composes the existing per-stage getters. See docs/27 §4.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

from app.services.workspace_service import WorkspaceService
from app.utils.logging import log

# Ordered (display kind → the stage service attribute) — impact first, dev last (dev carries change-specs).
_STAGES: tuple[str, ...] = ("impact", "fsd", "srd", "stories", "dev")


class BundleArtifact(BaseModel):
    """One upstream stage artifact, serialized for the plugin (``present=False`` when the stage is absent)."""

    kind: str
    present: bool
    document: dict[str, Any] | None = None


class WorkspaceBundle(BaseModel):
    """Everything the local Developer plugin needs to code a workspace, grounded and traceable."""

    workspace_id: str
    title: str = ""
    type: str = ""
    route: str = ""
    state: str = ""
    pinned_kb_version: str | None = None
    requirement_text: str | None = None
    artifacts: list[BundleArtifact] = Field(default_factory=list)
    change_specs: list[dict[str, Any]] = Field(default_factory=list)
    target_files: list[str] = Field(
        default_factory=list, description="union of every change-spec target file (corpus-rooted paths)"
    )
    repo_hints: list[str] = Field(
        default_factory=list,
        description="distinct subsystem prefixes — the plugin maps each corpus_prefix → a local repo root",
    )
    citations: list[str] = Field(
        default_factory=list, description="KB IDs grounding the change (change-spec kb_ids + impact scope/affected)"
    )
    generated_at: str = ""


def _dedupe(items: list[str]) -> list[str]:
    """Order-preserving de-duplication of non-empty strings."""
    seen: set[str] = set()
    out: list[str] = []
    for it in items:
        if it and it not in seen:
            seen.add(it)
            out.append(it)
    return out


def _repo_hint(path: str) -> str:
    """Subsystem-level prefix for the plugin's repo-root map — NOT the repo name.

    ``input/Auto/CodeBase/AIG-Connect-Services/src/Q.java`` → ``input/Auto/CodeBase/AIG-Connect-Services``.
    Cuts one segment past the ``CodeBase`` (or ``Auto``) anchor so each subsystem maps to one local repo;
    falls back to the parent directory when no anchor is present.
    """
    parts = [p for p in path.replace("\\", "/").split("/") if p]
    for anchor in ("CodeBase", "Auto"):
        if anchor in parts:
            i = parts.index(anchor)
            end = min(i + 2, len(parts))
            return "/".join(parts[:end])
    return "/".join(parts[:-1]) or path


def _collect_citations(impact: dict[str, Any] | None, change_specs: list[dict[str, Any]]) -> list[str]:
    """KB IDs that ground the change — from change-spec ``kb_ids`` + the impact scope/affected/downstream."""
    ids: list[str] = []
    for spec in change_specs:
        ids.extend(spec.get("kb_ids") or [])
    impact = impact or {}
    scope = impact.get("scope") or {}
    for bucket in ("new", "enhancement", "existing"):
        ids.extend(it.get("id") for it in (scope.get(bucket) or []) if it.get("id"))
    for key in ("affected", "downstream"):
        ids.extend(it.get("id") for it in (impact.get(key) or []) if it.get("id"))
    return _dedupe([i for i in ids if i])


class WorkspaceBundleService:
    """Composes the per-stage services into one grounded read for the local Developer plugin."""

    def __init__(self, *, workspace: WorkspaceService, impact: Any, fsd: Any, srd: Any, stories: Any, developer: Any) -> None:
        self._workspace = workspace
        self._svc = {"impact": impact, "fsd": fsd, "srd": srd, "stories": stories, "dev": developer}

    async def _safe_get(self, kind: str, workspace_id: str) -> dict[str, Any] | None:
        """Fetch one stage artifact as a JSON-mode dict; ``None`` if absent (never raises for a missing stage)."""
        svc = self._svc[kind]
        try:
            doc = await svc.get(workspace_id)
        # A missing/unreadable stage is "absent", not a bundle failure — degrade to None, never raise.
        except Exception as exc:  # noqa: BLE001
            log.warning(f"[bundle] {kind} unavailable for {workspace_id}: {type(exc).__name__}: {exc}")
            return None
        if doc is None:
            return None
        return doc.model_dump(mode="json") if hasattr(doc, "model_dump") else dict(doc)

    async def build(self, workspace_id: str) -> WorkspaceBundle:
        """Assemble the bundle. Raises 404 (via ``WorkspaceService.get``) only if the workspace itself is missing."""
        ws = await self._workspace.get(workspace_id)  # returns the Workspace model; 404 if it does not exist

        artifacts: list[BundleArtifact] = []
        docs: dict[str, dict[str, Any] | None] = {}
        for kind in _STAGES:
            doc = await self._safe_get(kind, workspace_id)
            docs[kind] = doc
            artifacts.append(BundleArtifact(kind=kind, present=doc is not None, document=doc))

        change_specs = list((docs.get("dev") or {}).get("change_specs") or [])
        target_files = _dedupe([f for spec in change_specs for f in (spec.get("target_files") or [])])
        repo_hints = _dedupe([_repo_hint(f) for f in target_files])
        citations = _collect_citations(docs.get("impact"), change_specs)

        return WorkspaceBundle(
            workspace_id=workspace_id,
            title=ws.title or "",
            type=str(ws.type) if ws.type is not None else "",
            route=str(ws.route) if ws.route is not None else "",
            state=str(ws.state) if ws.state is not None else "",
            pinned_kb_version=ws.pinned_kb_version,
            requirement_text=ws.requirement_text,
            artifacts=artifacts,
            change_specs=change_specs,
            target_files=target_files,
            repo_hints=repo_hints,
            citations=citations,
            generated_at=datetime.now(UTC).isoformat(),
        )
