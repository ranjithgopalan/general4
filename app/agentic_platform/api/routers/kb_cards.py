"""Card / graph knowledge base — read endpoints (fe_core.kb.cards).

Complements the chunk RAG (`rag.py`): where `/chat` answers a question from
chunks, these endpoints return *addressable* knowledge — a card by id, a node's
typed-edge neighbourhood, a ranked card search, and a grounding check for a
piece of text. They are what the `kb_card` / `graph_neighbors` /
`kb_cards_search` MCP tools call, and what the UI uses to show citation
coverage on a gate.

Read-only. Scoped by project (one KB root per application, `Settings.kb_root_for`).
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from app.agentic_platform.api.security import current_principal
from app.agentic_platform.fe_core.auth.roles import Principal
from app.agentic_platform.fe_core.config import get_settings
from app.agentic_platform.fe_core.kb.cards import CardStore

logger = logging.getLogger(__name__)
router = APIRouter(tags=["kb-cards"])


@lru_cache(maxsize=32)
def _store_for(root: str) -> CardStore:
    return CardStore(Path(root))


def store_for_project(project_id: str, *, require: bool = True) -> CardStore:
    root = get_settings().kb_root_for(project_id)
    store = _store_for(str(root))
    if require and not store.exists:
        raise HTTPException(
            status_code=404,
            detail=(f"no card KB for project '{project_id}' at {root}. Build one with the "
                    "re-kb-build pipeline (or set FE_KB_ROOT to an existing KB)."),
        )
    return store


@router.get("/projects/{project_id}/kb/stats")
async def kb_stats(project_id: str, refresh: bool = False,
                   principal: Principal = Depends(current_principal)) -> dict:
    store = store_for_project(project_id, require=False)
    if refresh:
        store.reload()
    return {"project_id": project_id, **store.stats()}


@router.get("/projects/{project_id}/kb/cards/{card_id}")
async def kb_card(project_id: str, card_id: str, max_body: int = Query(default=4000, le=60000),
                  principal: Principal = Depends(current_principal)) -> dict:
    store = store_for_project(project_id)
    card = store.get(card_id)
    if card is None:
        raise HTTPException(status_code=404, detail=f"card '{card_id}' not found")
    return {"project_id": project_id, **card.as_dict(max_body=max_body),
            "evidence": store.evidence_for(card_id)[:20]}


@router.get("/projects/{project_id}/kb/graph/{node_id}/neighbors")
async def kb_neighbors(project_id: str, node_id: str,
                       depth: int = Query(default=1, ge=1, le=3),
                       direction: str = Query(default="both", pattern="^(both|in|out)$"),
                       principal: Principal = Depends(current_principal)) -> dict:
    store = store_for_project(project_id)
    nodes, edges = store.neighbors(node_id, depth=depth, direction=direction)
    if not nodes:
        raise HTTPException(status_code=404, detail=f"node '{node_id}' not found")
    return {
        "project_id": project_id, "node_id": node_id, "depth": depth,
        "nodes": [{"id": n.id, "kind": n.kind, "label": n.label} for n in nodes],
        "edges": [{"source": e.source, "target": e.target, "label": e.label} for e in edges],
    }


@router.get("/projects/{project_id}/kb/cards")
async def kb_search(project_id: str, q: str = Query(min_length=1),
                    top_k: int = Query(default=8, ge=1, le=50),
                    kinds: str | None = None,
                    principal: Principal = Depends(current_principal)) -> dict:
    store = store_for_project(project_id)
    hits = store.search(q, top_k=top_k, kinds=[k for k in (kinds or "").split(",") if k] or None)
    return {"project_id": project_id, "query": q, "count": len(hits),
            "items": [{"score": h.score, **h.card.as_dict(with_body=False)} for h in hits]}


@router.post("/projects/{project_id}/kb/grounding")
async def kb_grounding(project_id: str, text: str = Body(..., embed=True),
                       principal: Principal = Depends(current_principal)) -> dict:
    """Which card ids `text` cites and whether they exist — the same check the
    stage executor applies after a run."""
    store = store_for_project(project_id, require=False)
    return {"project_id": project_id, **store.check_citations(text).as_dict()}
