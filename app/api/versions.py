"""``/re/versions`` — KB version listing + deterministic version-diff (docs/20 §9)."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query

from app.models.kb import VersionDiff
from app.services.version_diff import VersionDiffService, get_version_diff_service

router = APIRouter(prefix="/re/versions", tags=["versions"])

SvcDep = Annotated[VersionDiffService, Depends(get_version_diff_service)]


@router.get("", summary="List KB versions (newest first)")
async def list_versions(
    svc: SvcDep,
    origin: Annotated[
        str | None,
        Query(description="Filter by build origin: 're' (reverse-engineered) | 'fe' (workspace-sync). Omit for all."),
    ] = None,
) -> list[dict[str, Any]]:
    return await svc.versions(origin=origin)


@router.get("/diff", summary="Deterministic diff between two KB versions (added/removed/changed)")
async def diff_versions(
    svc: SvcDep,
    from_version: Annotated[str, Query(alias="from", description="Base kb_version")],
    to_version: Annotated[str, Query(alias="to", description="Target kb_version")],
) -> VersionDiff:
    return await svc.diff(from_version, to_version)
