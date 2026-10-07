"""
Session history surface (docs/20 §12) — list and inspect chatbot sessions.

Three endpoints:
  GET    /fe/sessions                 — paginated list of the caller's own sessions
  GET    /fe/sessions/{session_id}    — full turn-by-turn history for one of the caller's sessions
  DELETE /fe/sessions/{session_id}    — clear one of the caller's sessions

All three are scoped to the caller's ``user_id`` (Okta ``sub``/email): a user only ever sees and
deletes their own sessions, so two users may reuse the same client ``session_id`` without overlap.
The detail/delete endpoints return 404 when the session does not exist for that user.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from app.core.dependencies import get_current_user

router = APIRouter(prefix="/fe", tags=["sessions"])


# ── Response models ────────────────────────────────────────────────────────────

class SessionSummary(BaseModel):
    """One row in the session list — aggregate stats, no turn content."""
    session_id: str
    persona: str
    turn_count: int
    started_at: datetime
    last_at: datetime
    session_name: str | None = None  # first 50 chars of the first query; None for anonymous sessions


class SessionTurn(BaseModel):
    """One answered turn inside a session history."""
    session_id: str
    persona: str
    query: str
    answer: str
    citations: list[dict[str, Any]]
    confidence: float
    kb_version: str | None
    created_at: datetime


class SessionHistoryResponse(BaseModel):
    session_id: str
    turn_count: int
    turns: list[SessionTurn]


# ── Helpers ────────────────────────────────────────────────────────────────────

def _pool():
    """Return the Postgres pool, or None when unavailable (test / no-DB environments)."""
    try:
        from app.dao.postgres import get_pool
        return get_pool()
    except Exception:
        return None


# ── Endpoints ─────────────────────────────────────────────────────────────────

UserDep = Annotated[str, Depends(get_current_user)]


@router.get(
    "/sessions",
    response_model=list[SessionSummary],
    summary="List the caller's own chat sessions",
)
async def list_sessions(
    user_id: UserDep,
    limit: int = Query(default=50, ge=1, le=200, description="Max sessions to return"),
    offset: int = Query(default=0, ge=0, description="Pagination offset"),
) -> list[SessionSummary]:
    """Return a paginated summary of the caller's own sessions, most-recent first.

    Each entry contains the session id, persona, turn count, and the timestamps of the first and
    last turn. No turn content is included — use the detail endpoint for that.
    """
    pool = _pool()
    if pool is None:
        return []
    from app.dao.chat_history_dao import list_sessions as _list
    rows = await _list(pool, user_id=user_id, limit=limit, offset=offset)
    return [SessionSummary(**row) for row in rows]


@router.get(
    "/sessions/{session_id}",
    response_model=SessionHistoryResponse,
    summary="Full turn history for a session",
)
async def get_session(session_id: str, user_id: UserDep) -> SessionHistoryResponse:
    """Return every answered turn for the caller's own *session_id* in chronological order.

    Raises 404 when the session does not exist for this user or has no recorded turns.
    Scoped to the caller's ``user_id`` — a user cannot read another user's session.
    """
    pool = _pool()
    if pool is None:
        raise HTTPException(status_code=503, detail="Session store unavailable")
    from app.dao.chat_history_dao import session_history as _history
    rows = await _history(pool, session_id=session_id, user_id=user_id)
    if not rows:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found")
    turns = [
        SessionTurn(
            **{
                **row,
                "citations": row.get("citations") or [],
            }
        )
        for row in rows
    ]
    return SessionHistoryResponse(
        session_id=session_id,
        turn_count=len(turns),
        turns=turns,
    )


@router.delete(
    "/sessions/{session_id}",
    summary="Clear a session",
    status_code=status.HTTP_200_OK,
)
async def delete_session(session_id: str, user_id: UserDep) -> dict:
    """Delete all turns for the caller's own *session_id*. Returns the count of removed turns.

    Scoped to the caller's ``user_id`` — a user can only clear their own sessions; deleting
    another user's session id removes nothing (``deleted_turns: 0``).
    """
    pool = _pool()
    if pool is None:
        raise HTTPException(status_code=503, detail="Session store unavailable")
    from app.dao.chat_history_dao import delete_session as _delete
    deleted = await _delete(pool, session_id, user_id)
    return {"session_id": session_id, "deleted_turns": deleted}
