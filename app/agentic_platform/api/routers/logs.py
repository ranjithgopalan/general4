"""Serve the tail of api.log to the UI log viewer.

Returns the last `lines` lines (default 200, max 2000) so the browser never
receives a huge payload even after a long session.
"""

from __future__ import annotations

from collections import deque
from pathlib import Path

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from app.agentic_platform.fe_core.config import SERVICE_ROOT

router = APIRouter(tags=["logs"])

_LOG_FILE = SERVICE_ROOT / "logs" / "api.log"


@router.get("/logs", tags=["logs"])
async def get_log(lines: int = Query(default=200, ge=1, le=2000)) -> JSONResponse:
    """Return the last *lines* lines of logs/api.log.

    Returns an empty list when the log file does not exist yet (e.g. before the
    first request is logged) so the UI can show a "no entries yet" message
    instead of an error.
    """
    if not _LOG_FILE.exists():
        return JSONResponse({"lines": [], "path": str(_LOG_FILE), "exists": False})

    # Read only the tail — deque with maxlen avoids loading the entire file.
    tail: deque[str] = deque(maxlen=lines)
    with _LOG_FILE.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            tail.append(line.rstrip("\n"))

    return JSONResponse({
        "lines": list(tail),
        "path": str(_LOG_FILE),
        "exists": True,
        "total_requested": lines,
    })
