"""Request-scoped actor identity for artifact provenance capture.

Set once per request by the ``capture_actor`` route dependency (which runs in the endpoint's own
task, so the value propagates to downstream ``await``s like the artifact/transition DB writes) and
read by ``WorkspaceService`` when it stamps triggered_by / reviewed_by — so we don't thread the
authenticated user through every stage handler.
"""

from __future__ import annotations

from contextvars import ContextVar

_current_user: ContextVar[str] = ContextVar("aidlc_current_user", default="system")
_current_user_name: ContextVar[str] = ContextVar("aidlc_current_user_name", default="")


def set_actor(user: str | None, name: str | None) -> None:
    _current_user.set(user or "system")
    _current_user_name.set(name or "")


def current_actor() -> tuple[str, str]:
    """(user_id, display_name) for the current request — falls back to ('system', '')."""
    return _current_user.get(), _current_user_name.get()
