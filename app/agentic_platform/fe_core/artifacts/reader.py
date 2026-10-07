"""Bring an artefact's files to local disk, wherever they live.

`materialise(artifact)` is the ONLY way code should read artefact bodies:

* worktree still present locally (same host, recent run) -> that path
* otherwise `content_uri` / `manifest_uri` -> download the tree from the
  ArtifactStore (S3 or local root) into a per-artefact cache directory
* git-ref artefacts (Mini code, `storage_kind == "git"`) have no body here —
  callers get `None` and should show the repo/commit/PR link instead

Callers: prompt intake (upstream inputs), RAG indexer, KB publisher, artefact
download/content endpoints, EPIC extraction on fan-out.
"""

from __future__ import annotations

import logging
from pathlib import Path

from app.agentic_platform.fe_core.artifacts.store import MANIFEST_NAME, ArtifactStore, get_artifact_store

logger = logging.getLogger(__name__)


def _cache_root(settings=None) -> Path:
    if settings is None:
        from app.agentic_platform.fe_core.config import get_settings  # noqa: PLC0415
        settings = get_settings()
    return Path(settings.fe_workspace_root) / "_cache"


def artifact_prefix(artifact) -> str | None:
    """The store key prefix for an artefact, derived from manifest_uri/content_uri."""
    uri = getattr(artifact, "manifest_uri", None) or getattr(artifact, "content_uri", None) or ""
    if uri.startswith("s3://"):
        # s3://bucket/key[/manifest.json]
        key = uri.split("/", 3)[3] if uri.count("/") >= 3 else ""
    elif uri.startswith("file://"):
        # file:///abs/root/key — strip the store root when we know it
        try:
            store = get_artifact_store()
            root = getattr(store, "root", None)
            p = Path(uri[7:].lstrip("/") if uri.startswith("file:///") else uri[7:])
            if root is not None:
                # Windows: file:///C:/... -> "C:/..."
                p = Path(uri.replace("file:///", "", 1)) if uri.startswith("file:///") else p
                key = p.resolve().relative_to(Path(root).resolve()).as_posix()
            else:
                return None
        except Exception:  # noqa: BLE001
            return None
    else:
        return None
    if key.endswith("/" + MANIFEST_NAME):
        key = key[: -len(MANIFEST_NAME) - 1]
    return key.strip("/") or None


def materialise(artifact, *, dest: Path | None = None, store: ArtifactStore | None = None,
                settings=None) -> Path | None:
    """Return a local directory holding the artefact's files, or None for git-ref artefacts."""
    if getattr(artifact, "storage_kind", None) == "git":
        return None

    # 1. worktree still on this host
    path = getattr(artifact, "path", None)
    if path and Path(path).exists() and dest is None:
        return Path(path)

    # 2. from the artefact store
    prefix = artifact_prefix(artifact)
    if prefix is None:
        # Legacy artefact (pre-store) whose worktree is gone.
        return Path(path) if path and Path(path).exists() else None
    store = store or get_artifact_store(settings)
    target = Path(dest) if dest is not None else _cache_root(settings) / str(getattr(artifact, "id", "unknown"))
    if (target / MANIFEST_NAME).is_file() and dest is None:
        return target                     # cache hit
    try:
        store.get_tree(prefix, target)
    except FileNotFoundError:
        logger.warning("artefact %s: no manifest under %s", getattr(artifact, "id", "?"), prefix)
        return Path(path) if path and Path(path).exists() else None
    return target


def read_markdown_files(root: Path, *, max_chars: int = 30_000) -> dict[str, str]:
    """{relative path: text} for *.md under root, each capped at max_chars.

    The default max_chars=30_000 is preserved for backward compatibility.
    Callers should pass an explicit limit based on their context (e.g.,
    tier-specific limits from config.get_artifact_read_limit()).
    """
    out: dict[str, str] = {}
    if root is None or not Path(root).exists():
        return out
    for p in sorted(Path(root).rglob("*.md")):
        if p.name == MANIFEST_NAME:
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if len(text) > max_chars:
            text = text[:max_chars] + f"\n\n[... truncated at {max_chars} chars; full file at {p} ...]"
        out[p.relative_to(root).as_posix()] = text
    return out
