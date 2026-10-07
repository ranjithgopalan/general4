"""KbRefreshService — the central forward→reverse round-trip (docs/23 §3).

On workspace MERGE (DevOps), transform the accepted artifacts into a KB delta, merge it onto the CURRENT
ACTIVE KB base, and load it as a NEW immutable version (STAGING) via the central kb-indexer CLI
(subprocess) — awaiting human promotion to ACTIVE. Never overwrites the serving ACTIVE version.

**Central/S3, no local ``kb/`` folder.** The FE runs centrally (Fargate) where there is no source-tree
``kb/`` directory, so nothing here reads the filesystem KB:
  - the KB **base** is materialized from the DB SSOT via ``kb-indexer export`` (reverse of ``load``);
  - the workspace **artifacts** (FSD/SRD) come from the workspace stores (S3-first → DB) via the stage
    services — never a local file.
The only filesystem use is an ephemeral scratch dir (``KB_SYNC_WORK_DIR``, default the system temp) that
holds the exported base + merged delta just long enough to hand to the kb-indexer, then is disposable.

All I/O is best-effort: the service returns a ``KbRefreshResult`` (ok=False + message on failure) and
never raises into the caller, so a refresh problem never corrupts the workspace lifecycle.

Config (12-factor; env-overridable, never hardcoded):
    KB_INDEXER_DIR   the kb-indexer package dir (cwd for its CLI)   (required in central deployment)
    KB_INDEXER_PYTHON interpreter for the kb-indexer CLI            (default: its own .venv)
    KB_SYNC_WORK_DIR ephemeral scratch dir for export + merge       (default: <system temp>/aidlc-kb-refresh)
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from app.config.settings import get_settings
from app.lifecycle.kb_sync.transform import KbDelta, build_delta
from app.utils.logging import log

_KB_TABLES = (
    "fe_kb_nodes",
    "fe_kb_edges",
    "fe_kb_cards",
    "fe_kb_chunks",
    "fe_kb_evidence",
    "fe_kb_sources",
    "fe_kb_knowledge_gaps",
    "fe_kb_review_items",
)

_VERSION_RX = re.compile(r"kb_version=(\S+)")


@dataclass
class KbRefreshResult:
    """Outcome of a KB-refresh run — the new STAGING version (for human promote) + provenance."""

    ok: bool
    workspace_id: str
    new_kb_version: str | None = None
    status: str = "STAGING"
    delta_counts: dict[str, int] = field(default_factory=dict)
    working_dir: str | None = None
    message: str = ""


class KbRefreshService:
    """Transform merged workspace artifacts → new KB version (STAGING) via the central kb-indexer."""

    def __init__(self, *, fsd=None, architecture=None, settings=None) -> None:
        self._fsd = fsd                # FSDService (get -> FSDDocument)
        self._architecture = architecture  # ArchitectureService (get -> SRDDocument)
        self._s = settings or get_settings()

    # ── paths / config ──────────────────────────────────────────────────────────────
    def _indexer_dir(self) -> Path | None:
        d = os.getenv("KB_INDEXER_DIR", getattr(self._s, "KB_INDEXER_DIR", "") or "")
        return Path(d) if d else None

    def _work_base(self) -> Path:
        override = os.getenv("KB_SYNC_WORK_DIR", "") or (getattr(self._s, "KB_SYNC_WORK_DIR", "") or "")
        return Path(override) if override else Path(tempfile.gettempdir()) / "aidlc-kb-refresh"

    # ── public API ──────────────────────────────────────────────────────────────────
    async def run(self, workspace_id: str, *, stage_key: str | None = None) -> KbRefreshResult:
        """Full round-trip: export base (DB) → gather (S3) → merge → load new STAGING. Never raises."""
        try:
            indexer = self._indexer_dir()
            if not indexer or not indexer.is_dir():
                return KbRefreshResult(False, workspace_id,
                                       message="KB_INDEXER_DIR unset/not found — cannot source the KB base "
                                               "from the central store (DB SSOT)")

            working = self._reset_working_dir(workspace_id)
            base_ver = await self._export_base(working)
            if not base_ver:
                return KbRefreshResult(False, workspace_id, working_dir=str(working),
                                       message="could not export the ACTIVE KB base from the DB "
                                               "(kb-indexer export failed or no ACTIVE version)")

            new_ver = f"{base_ver}.ws-{workspace_id}"
            delta = await self._gather(workspace_id, new_ver)
            if not delta.cards and not delta.edges:
                return KbRefreshResult(False, workspace_id, message="no delta from artifacts (nothing to sync)")

            pr = await self._pr_provenance(workspace_id)
            self._stamp_version(working, new_ver, workspace_id, pr)
            self._merge(working, delta, workspace_id, pr)

            version = await self._run_indexer(working)
            if version is None:
                return KbRefreshResult(False, workspace_id, delta_counts=delta.counts(),
                                       working_dir=str(working),
                                       message="kb-indexer pipeline failed on the merged base "
                                               "(delta left in working dir for inspection)")
            await self._stamp_workspace_id(version, workspace_id, stage_key)
            log.info(f"[kb-refresh] ws={workspace_id} base={base_ver} -> new STAGING {version} {delta.counts()}")
            return KbRefreshResult(True, workspace_id, new_kb_version=version, delta_counts=delta.counts(),
                                   working_dir=str(working),
                                   message=f"loaded {version} (STAGING) — promote after sign-off")
        except Exception as exc:  # noqa: BLE001 — a refresh failure must never crash the lifecycle
            log.warning(f"[kb-refresh] ws={workspace_id} failed: {exc}")
            return KbRefreshResult(False, workspace_id, message=str(exc))

    # ── steps ─────────────────────────────────────────────────────────────────────
    async def _pr_provenance(self, workspace_id: str) -> dict | None:
        """The latest ``dev-pr`` artifact (pr_url/branch/commit/repo) — closes the story→code→PR→KB
        traceability round-trip (docs/27 §7). None if the workspace was delivered without a synced PR."""
        from app.services.workspace_service import WorkspaceService

        try:
            artifacts = await WorkspaceService().list_artifacts(workspace_id)
        except Exception as exc:  # noqa: BLE001 — provenance is best-effort; never fail the refresh over it
            log.warning(f"[kb-refresh] ws={workspace_id} could not read PR provenance: {exc}")
            return None
        prs = [a for a in artifacts if a.get("kind") == "dev-pr" and a.get("content")]
        if not prs:
            return None
        try:
            return json.loads(prs[-1]["content"])
        except (json.JSONDecodeError, TypeError):
            return None

    async def _gather(self, workspace_id: str, kb_version: str) -> KbDelta:
        """Load the accepted FSD + SRD artifacts (S3-first → DB) and transform them into a KB delta."""
        fsd = await self._fsd.get(workspace_id) if self._fsd else None
        srd = await self._architecture.get(workspace_id) if self._architecture else None
        return build_delta(workspace_id=workspace_id, kb_version=kb_version, fsd=fsd, srd=srd)

    def _reset_working_dir(self, workspace_id: str) -> Path:
        """A clean, isolated ephemeral scratch dir for this workspace's refresh (system temp by default)."""
        working = self._work_base() / workspace_id
        if working.exists():
            shutil.rmtree(working)
        working.mkdir(parents=True, exist_ok=True)
        return working

    async def _export_base(self, working: Path) -> str | None:
        """Materialize the gear's ACTIVE KB version from the DB into ``working`` (no local kb/ folder)."""
        indexer = self._indexer_dir()
        if not indexer:
            return None
        cmd = [self._indexer_python(indexer), "-m", "app.cli", "export",
               "--gear-id", self._s.GEAR_ID, "--out", str(working.resolve())]
        rc, text = await self._run_subprocess(cmd, indexer)
        if rc != 0:
            log.warning(f"[kb-refresh] indexer export rc={rc}: {text[-500:]}")
            return None
        report = working / "_build-report.json"
        if report.is_file():
            try:
                return json.loads(report.read_text(encoding="utf-8")).get("kb_version")
            except Exception:  # noqa: BLE001
                return None
        return None

    def _stamp_version(self, working: Path, new_ver: str, workspace_id: str, pr: dict | None = None) -> None:
        """Stamp the exported base's report with the NEW workspace-derived version + STAGING status
        (+ the delivering PR provenance when present, closing the traceability round-trip — docs/27 §7)."""
        report = working / "_build-report.json"
        meta = json.loads(report.read_text(encoding="utf-8")) if report.is_file() else {}
        meta["kb_version"] = new_ver
        meta["status"] = "STAGING"
        meta["source_workspace_id"] = workspace_id
        if pr:
            meta["source_pr"] = {k: pr.get(k) for k in ("pr_url", "branch", "commit", "repo") if pr.get(k)}
        report.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    def _merge(self, working: Path, delta: KbDelta, workspace_id: str, pr: dict | None = None) -> None:
        """Apply the delta onto the working copy: upsert cards + graph nodes/edges (delta wins), log it."""
        self._merge_cards(working / "records" / "cards.jsonl", delta.cards)
        self._merge_graph(working / "knowledge" / "graph.json", delta)
        self._merge_graph(working / "knowledge" / "ontology" / "graph.json", delta)
        self._append_changelog(working / "_change-log.md", delta, workspace_id, pr)

    @staticmethod
    def _merge_cards(path: Path, cards: list[dict]) -> None:
        if not cards:
            return
        existing: dict[str, dict] = {}
        if path.is_file():
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    row = json.loads(line)
                    existing[row["id"]] = row
        for c in cards:  # delta wins (enhancement overwrites the prior card of the same id)
            existing[c["id"]] = c
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in existing.values()) + "\n",
                        encoding="utf-8")

    @staticmethod
    def _merge_graph(path: Path, delta: KbDelta) -> None:
        if not path.is_file() or (not delta.nodes and not delta.edges):
            return
        graph = json.loads(path.read_text(encoding="utf-8"))
        nodes = {n["id"]: n for n in graph.get("nodes", [])}
        for n in delta.nodes:
            nodes[n["id"]] = n  # delta wins
        ekey = {(e["from"], e["to"], e["label"]) for e in graph.get("edges", [])}
        edges = list(graph.get("edges", []))
        for e in delta.edges:
            if (e["from"], e["to"], e["label"]) not in ekey:
                ekey.add((e["from"], e["to"], e["label"]))
                edges.append(e)
        graph["nodes"], graph["edges"] = list(nodes.values()), edges
        path.write_text(json.dumps(graph, ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def _append_changelog(path: Path, delta: KbDelta, workspace_id: str, pr: dict | None = None) -> None:
        pr_ref = ""
        if pr and pr.get("pr_url"):
            commit = f"@{pr['commit'][:8]}" if pr.get("commit") else ""
            pr_ref = f" via PR {pr['pr_url']}{commit}"
        line = (f"- workspace `{workspace_id}` merged{pr_ref}: "
                f"+{len(delta.cards)} cards, +{len(delta.nodes)} nodes, +{len(delta.edges)} edges\n")
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line)

    async def _stamp_workspace_id(
        self, kb_version: str, workspace_id: str, stage_key: str | None
    ) -> None:
        """Back-fill workspace_id and stage_key on all KB rows the indexer just wrote.

        Best-effort: a failure here never blocks the refresh result — rows are still
        queryable by kb_version; these columns are convenience filters only.
        """
        try:
            from app.dao.postgres import get_pool
            pool = get_pool()
            schema = getattr(self._s, "PGSCHEMA", "form_rationalization_anh")
            async with pool.connection() as conn:
                for table in _KB_TABLES:
                    await conn.execute(
                        f"UPDATE {schema}.{table}"
                        " SET workspace_id = %s, stage_key = %s"
                        " WHERE kb_version = %s AND workspace_id IS NULL",
                        (workspace_id, stage_key, kb_version),
                    )
        except Exception as exc:  # noqa: BLE001
            log.warning(
                f"[kb-refresh] could not stamp workspace_id/stage_key on kb_version={kb_version}: {exc}"
            )

    def _indexer_python(self, indexer: Path) -> str:
        """The interpreter to run the kb-indexer CLI with. Prefer the kb-indexer's OWN venv (its deps
        differ from the agents venv); fall back to KB_INDEXER_PYTHON, then this process' interpreter.
        Never a bare 'python' — that resolves to whatever is on PATH (often the wrong interpreter)."""
        override = os.getenv("KB_INDEXER_PYTHON", getattr(self._s, "KB_INDEXER_PYTHON", "") or "")
        if override and Path(override).exists():
            return override
        for rel in (".venv/Scripts/python.exe", ".venv/bin/python"):  # Windows / POSIX venv layouts
            cand = indexer / rel
            if cand.exists():
                return str(cand)
        return sys.executable  # last resort — this process' interpreter

    @staticmethod
    async def _run_subprocess(cmd: list[str], cwd: Path) -> tuple[int, str]:
        """Run a kb-indexer CLI command and return (rc, combined output).

        Uses subprocess.run in a thread — NOT asyncio.create_subprocess_exec: on Windows the app runs on
        the Selector event loop (psycopg requirement), which does not support asyncio subprocess (raises
        NotImplementedError). to_thread(subprocess.run) is loop-agnostic and works everywhere."""
        def _run() -> tuple[int, str]:
            proc = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True,
                                  encoding="utf-8", errors="replace")
            return proc.returncode, (proc.stdout or "") + (proc.stderr or "")

        return await asyncio.to_thread(_run)

    async def _run_indexer(self, working: Path) -> str | None:
        """Invoke the central kb-indexer `pipeline` (load + embed) on the working dir; parse new version."""
        indexer = self._indexer_dir()
        if not indexer or not indexer.is_dir():
            return None
        cmd = [self._indexer_python(indexer), "-m", "app.cli", "pipeline", "--kb-dir", str(working.resolve()),
               "--gear-id", self._s.GEAR_ID]
        rc, text = await self._run_subprocess(cmd, indexer)
        if rc != 0:
            log.warning(f"[kb-refresh] indexer pipeline rc={rc}: {text[-500:]}")
            return None
        m = _VERSION_RX.search(text)
        return m.group(1) if m else None
