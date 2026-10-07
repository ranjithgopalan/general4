"""
Pre-Activation Review API (docs/25) — gap ledger + dispositions + the promote gate.

    GET  /re/kb/{kb_version}/review              aggregated gaps (+ merged dispositions + promotable flag)
    POST /re/kb/{kb_version}/review/{gap_id}     disposition a gap (FALSE_POSITIVE | RESOLVED | SME_CONFIRMED)
    POST /re/kb/{kb_version}/promote             gate on no OPEN blocking gap, then kb-indexer promote -> ACTIVE

Pass 'active' as kb_version to resolve the current ACTIVE version.
Auth: behind the standard middleware chain (Okta JWT + entitlement + API-key), like the other /re routes.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
from pathlib import Path

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.config.settings import get_settings
from app.dao import graph_dao, review_dao
from app.dao.postgres import get_pool
from app.models.review import DISPOSITIONS, KbReview
from app.services.gap_service import GapService
from app.utils.logging import log

router = APIRouter(prefix="/re/kb", tags=["re-review"])
_service = GapService()


class DispositionReq(BaseModel):
    status: str = Field(description="FALSE_POSITIVE | RESOLVED | SME_CONFIRMED (OPEN to clear)")
    note: str | None = None
    artifact_ref: str | None = None
    by: str | None = Field(default=None, description="reviewer AIG email")


class PromoteReq(BaseModel):
    by: str = Field(description="approver AIG email (recorded as signed_off_by)")


async def _resolve(kb_version: str) -> str:
    if kb_version == "active":
        v = await graph_dao.active_version(get_pool(), get_settings().GEAR_ID)
        return v or kb_version
    return kb_version


@router.get("/{kb_version}/review", summary="Pre-activation gap ledger (+ dispositions + promotable)")
async def kb_review(kb_version: str) -> KbReview:
    return await _service.review(await _resolve(kb_version))


@router.post("/{kb_version}/review/{gap_id}", summary="Disposition a gap (false-positive / resolve / SME-confirm)")
async def disposition(kb_version: str, gap_id: str, body: DispositionReq) -> KbReview:
    if body.status not in DISPOSITIONS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"status must be one of {DISPOSITIONS}")
    version = await _resolve(kb_version)
    review = await _service.review(version)  # find the live gap to snapshot its type + severity
    gap = next((g for g in review.gaps if g.gap_id == gap_id), None)
    if gap is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"gap not found in current review: {gap_id}")
    await review_dao.set_disposition(get_pool(), kb_version=version, gap_id=gap_id, gap_type=gap.type,
                                     severity=gap.severity, status=body.status, note=body.note,
                                     by=body.by, artifact_ref=body.artifact_ref)
    return await _service.review(version)  # fresh review with the disposition applied


@router.post("/{kb_version}/promote", summary="Promote STAGING -> ACTIVE, gated on no OPEN blocking gap")
async def promote(kb_version: str, body: PromoteReq) -> dict:
    version = await _resolve(kb_version)
    review = await _service.review(version)
    if not review.promotable:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"promote blocked: {review.blocking_open} OPEN blocking gap(s) — "
                            "disposition (resolve/false-positive) them first")

    indexer = os.getenv("KB_INDEXER_DIR", getattr(get_settings(), "KB_INDEXER_DIR", "") or "")
    if not indexer or not Path(indexer).is_dir():
        # Direct-DB promote path: kb-indexer not deployed (laptop / AIDLC agent pipeline).
        # Write ACTIVE / SUPERSEDED directly via pg_sink when the DB is configured.
        try:
            from app.agentic_platform.fe_core.kb import pg_sink  # noqa: PLC0415
            from app.agentic_platform.fe_core.config import get_settings as _fe_settings  # noqa: PLC0415

            fe_cfg = _fe_settings()
            if not fe_cfg.uses_database():
                raise HTTPException(
                    status.HTTP_503_SERVICE_UNAVAILABLE,
                    "KB_INDEXER_DIR unset and FE_DB_URL not configured — cannot promote",
                )
            gear_id = getattr(get_settings(), "GEAR_ID", version.split("-")[0])
            await asyncio.to_thread(pg_sink.promote, version, gear_id)
            log.info(f"[promote] {version} -> ACTIVE by {body.by} (direct-DB path)")
            return {"ok": True, "kb_version": version, "promoted_by": body.by,
                    "message": f"promoted via direct DB (no kb-indexer)"}
        except HTTPException:
            raise
        except Exception as exc:
            log.error(f"[promote] direct-DB promote failed: {exc}")
            raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR,
                                f"direct-DB promote failed: {exc}") from exc

    py = _indexer_python(Path(indexer))
    cmd = [py, "-m", "app.cli", "promote", version, "--by", body.by]

    def _run() -> tuple[int, str]:
        p = subprocess.run(cmd, cwd=indexer, capture_output=True, text=True, encoding="utf-8", errors="replace")
        return p.returncode, (p.stdout or "") + (p.stderr or "")

    rc, out = await asyncio.to_thread(_run)
    if rc != 0:
        log.warning(f"[promote] rc={rc}: {out[-400:]}")
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, f"kb-indexer promote failed: {out[-200:]}")
    log.info(f"[promote] {version} -> ACTIVE by {body.by}")
    return {"ok": True, "kb_version": version, "promoted_by": body.by, "message": out.strip().splitlines()[-1:] or ""}


def _indexer_python(indexer: Path) -> str:
    override = os.getenv("KB_INDEXER_PYTHON", getattr(get_settings(), "KB_INDEXER_PYTHON", "") or "")
    if override and Path(override).exists():
        return override
    for rel in (".venv/Scripts/python.exe", ".venv/bin/python"):
        cand = indexer / rel
        if cand.exists():
            return str(cand)
    import sys
    return sys.executable
