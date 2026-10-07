"""MERGE step (Plan v3 Phase C): the forward -> reverse round-trip.

When every Mini workspace of a programme is closed, the Global thread builds one
`kb-refresh` artefact from the approved Global documents (default prd/frd/sdd/srd)
plus the git-ref provenance of everything the laptops delivered, and queues it for
the KB as a **STAGING** version `<base>.ws-<workspace>`. A human then promotes
(`POST /workspaces/{id}/promote`) -- the platform records the decision; in
`manual` mode the ACTIVE flip happens in the KB tool, in `api` mode the KB client
is asked to promote. Mirrors the AIDLC MERGE -> PENDING_SYNC -> CLOSED states.

Generic: which document types feed the refresh is configuration
(`FE_MERGE_TYPES`), not code.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app.agentic_platform.fe_core.artifacts.reader import materialise, read_markdown_files
from app.agentic_platform.fe_core.artifacts.store import artifact_key, get_artifact_store
from app.agentic_platform.fe_core.kb.models import ArtifactStatus, SdlcArtifact
from app.agentic_platform.fe_core.store import make_idempotency_key

logger = logging.getLogger(__name__)

KB_REFRESH_TYPE = "kb-refresh"
KB_REFRESH_STAGE = "merge"


def merge_types(settings) -> list[str]:
    raw = getattr(settings, "fe_merge_types", None) or "prd,frd,sdd,srd"
    return [t.strip() for t in str(raw).split(",") if t.strip()]


def kb_refresh_version(base: str | None, workspace_id: str) -> str:
    return f"{base or 'kb'}.ws-{workspace_id}"


def build_kb_refresh(store, settings, *, project_id: str, workspace_id: str, pipeline: str,
                     mini_pipeline: str | None, base_kb_version: str | None,
                     created_by: str = "orchestrator") -> SdlcArtifact:
    """Assemble the delta (Markdown manifest + JSON) and register it as a Draft
    `kb-refresh` artefact in the Global workspace, queued on the outbox with
    operation `kb_refresh`. Idempotent per (workspace, set of source ids)."""
    types = merge_types(settings)
    docs: list[SdlcArtifact] = []
    for t in types:
        cands = store.approved_artifacts(pipeline, [t], workspace_ids=[workspace_id])
        if cands:
            docs.append(max(cands, key=lambda a: a.version))
    minis = [w for w in store.list_workspaces(project_id, tier="mini")]
    provenance: list[dict] = []
    for w in minis:
        for a in store.list_artifacts(workspace_id=w.id):
            if a.storage_kind == "git" and a.status is ArtifactStatus.APPROVED:
                provenance.append({"workspace_id": w.id, "epic_id": w.epic_id, "stage": a.stage_key,
                                   "artifact_type": a.artifact_type, "repo": a.git_repo, "branch": a.git_branch,
                                   "commit": a.git_commit, "pr_url": a.pr_url})
    version = kb_refresh_version(base_kb_version, workspace_id)
    fingerprint = hashlib.sha256(json.dumps(
        {"docs": sorted(d.id for d in docs), "prov": sorted(p.get("commit") or "" for p in provenance)},
        sort_keys=True).encode()).hexdigest()

    existing = [a for a in store.list_artifacts(workspace_id=workspace_id, artifact_type=KB_REFRESH_TYPE)
                if a.checksum == fingerprint]
    if existing:
        return existing[0]

    delta = {
        "kb_version": version, "status": "STAGING", "base_kb_version": base_kb_version,
        "source_workspace_id": workspace_id, "project_id": project_id,
        "documents": [{"artifact_id": d.id, "artifact_type": d.artifact_type, "version": d.version,
                       "approved_by": d.approved_by, "content_uri": d.content_uri} for d in docs],
        "source_prs": provenance,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    lines = [f"# KB refresh {version}", "",
             f"Programme `{project_id}` · Global workspace `{workspace_id}` · status **STAGING** "
             f"(promote to ACTIVE with `POST /api/v1/workspaces/{workspace_id}/promote`)", "",
             "## Documents merged into the knowledge base", ""]
    for d in docs:
        lines.append(f"- **{d.artifact_type}** v{d.version} (`{d.id}`, approved by {d.approved_by})")
    missing = [t for t in types if t not in {d.artifact_type for d in docs}]
    if missing:
        lines += ["", f"_Not available (no approved version): {', '.join(missing)}_"]
    lines += ["", "## Delivered code (provenance from the laptops)", ""]
    if provenance:
        for p in provenance:
            lines.append(f"- {p['workspace_id']} · {p['stage']} → {p.get('repo')}@{p.get('commit')}"
                         + (f" ({p['pr_url']})" if p.get("pr_url") else ""))
    else:
        lines.append("_(none recorded)_")
    lines += ["", "## Document bodies", ""]
    for d in docs:
        lines += [f"### {d.artifact_type} v{d.version}", ""]
        try:
            local = materialise(d, settings=settings)
            md = read_markdown_files(local, max_chars=60_000) if local else {}
            lines += [md.get(f"{d.artifact_type}.md") or (next(iter(md.values())) if md else "_(no body)_"), ""]
        except Exception as exc:  # noqa: BLE001
            lines += [f"_(body unavailable: {exc})_", ""]

    aid = uuid.uuid4().hex[:12]
    tmp = Path(settings.fe_workspace_root) / "_merge" / aid
    tmp.mkdir(parents=True, exist_ok=True)
    (tmp / f"{KB_REFRESH_TYPE}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (tmp / "kb-delta.json").write_text(json.dumps(delta, indent=2), encoding="utf-8")
    astore = get_artifact_store(settings)
    prev = store.list_artifacts(workspace_id=workspace_id, artifact_type=KB_REFRESH_TYPE)
    ver = 1 + max((a.version for a in prev), default=0)
    key = artifact_key(settings.fe_s3_prefix if astore.kind == "s3" else "fe", project_id, workspace_id,
                       KB_REFRESH_TYPE, ver, aid)
    manifest = astore.put_tree(tmp, key, artifact_id=aid, artifact_type=KB_REFRESH_TYPE, version=ver,
                               project_id=project_id, workspace_id=workspace_id)
    art = SdlcArtifact(
        id=aid, kb_application_id=project_id, pipeline=pipeline, stage_key=KB_REFRESH_STAGE,
        sdlc_stage=KB_REFRESH_STAGE, artifact_type=KB_REFRESH_TYPE, version=ver, workspace_id=workspace_id,
        tier="global", path=astore.uri(key), content_uri=astore.uri(key), checksum=fingerprint,
        manifest_uri=astore.uri(f"{key}/manifest.json"), storage_kind=astore.kind,
        files=[{"path": f.path, "sha256": f.sha256, "size": f.size} for f in manifest.files],
        status=ArtifactStatus.DRAFT, produced_by_persona="orchestrator", artifact_tier="release",
        source_artifact_ids=[d.id for d in docs],
        links=[{"type": "DERIVES_FROM", "artifact_id": d.id, "artifact_type": d.artifact_type,
                "version": d.version} for d in docs],
        created_by=created_by, created_at=datetime.now(timezone.utc),
    )
    store.put_artifact(art)
    logger.info("MERGE: kb-refresh %s (%s) recorded for %s docs, %s PRs", version, aid, len(docs), len(provenance))
    return art
