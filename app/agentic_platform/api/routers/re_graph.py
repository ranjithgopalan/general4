"""Proxy endpoints for the external RE Graph service.

Bridges the Angular UI (port 4200) → FE API (port 8080) → RE Graph (port 9090).
"""

from __future__ import annotations

import logging
from typing import Any, Optional

import httpx
from fastapi import APIRouter, HTTPException, Query

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/re-graph", tags=["re-graph"])


@router.get("/modules")
async def list_modules(gear_id: Optional[str] = Query(default=None)) -> list[dict[str, Any]]:
    """Return available module scopes for a gear as [{name, nodeCount}] list.

    Calls /re/graph/overview on the RE Graph service, extracts unique category
    values, and returns them sorted — suitable for populating the PRD dialog dropdown.
    """
    from app.config.settings import get_settings as _gs  # noqa: PLC0415
    base_url = _gs().RE_GRAPH_BASE_URL.rstrip("/")

    params: dict[str, str] = {}
    if gear_id:
        params["gear_id"] = gear_id

    try:
        async with httpx.AsyncClient(timeout=15, verify=False) as client:
            resp = await client.get(f"{base_url}/re/graph/overview", params=params)
            resp.raise_for_status()
            nodes: list[dict] = resp.json().get("nodes", [])
    except httpx.HTTPError as exc:
        logger.warning("RE Graph overview fetch failed: %s", exc)
        raise HTTPException(status_code=502, detail=f"RE Graph unreachable: {exc}") from exc

    cat_count: dict[str, int] = {}
    for node in nodes:
        cat = node.get("category")
        if cat:
            cat_count[cat] = cat_count.get(cat, 0) + 1

    return [
        {"name": name, "nodeCount": count}
        for name, count in sorted(cat_count.items())
    ]
