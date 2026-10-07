"""``/re/overview`` — the Active-KB Overview dashboard payload (docs/20 §4a)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.models.kb import KbOverview
from app.services.overview_service import OverviewService, get_overview_service

router = APIRouter(prefix="/re/overview", tags=["overview"])

SvcDep = Annotated[OverviewService, Depends(get_overview_service)]


@router.get("", summary="Active-KB Overview — families, tech-stack, repos, counts (computed from the ACTIVE version)")
async def kb_overview(svc: SvcDep) -> KbOverview:
    return await svc.get()
