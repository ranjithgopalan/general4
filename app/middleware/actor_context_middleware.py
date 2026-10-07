"""Actor-context middleware — stamp the request-scoped actor (id + display name) for provenance.

WHY a raw ASGI middleware (not a ``Depends`` and not ``BaseHTTPMiddleware``):
artifact provenance writes (``triggered_by`` / ``reviewed_by``) read the actor from a ``ContextVar``
(``app.core.request_context``). For that read to see the value, the ``ContextVar.set()`` must happen in
the SAME asyncio task/context that runs the route handler AND any ``StreamingResponse`` generator body.

- An app/router-level ``Depends`` sets the var during dependency-solving, but the mutation does NOT
  propagate to the route handler's execution context — the write then reads the ContextVar default
  (``'system'``). (This was the observed bug: every artifact stamped ``triggered_by_user='system'``.)
- ``BaseHTTPMiddleware`` runs its ``dispatch`` in a separate anyio task, so a ContextVar set there is
  likewise invisible downstream.
- A **pure ASGI middleware** awaits ``self.app(...)`` in the same task, so the value set here is visible
  to the handler and to SSE generators (which are iterated within that same ``await``). This is the
  correct, framework-blessed pattern for request-scoped ContextVars.
"""

from __future__ import annotations

import uuid

from starlette.requests import Request
from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.dependencies import get_current_user, get_current_user_name
from app.core.request_context import set_actor
from app.utils.logging import log
from app.utils.request_context import set_correlation_id, set_pipeline_execution_id


class ActorContextMiddleware:
    """Resolve the authenticated actor and correlation ID for this request.

    WHY here (raw ASGI, not BaseHTTPMiddleware):
    ContextVar mutations must happen in the same asyncio task as the route handler
    and any SSE generator body.  BaseHTTPMiddleware runs in a separate anyio task,
    so mutations there are invisible downstream.  This raw ASGI middleware awaits
    self.app() in the same task, so both set_actor() and set_correlation_id() are
    visible to handlers and streaming responses.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        # Headers-only use of the Request (no body read) — safe without wiring ``receive``.
        request = Request(scope)

        # Stamp correlation ID from the inbound header (sent by the UI as X-Request-ID)
        # or generate a fresh UUID.  Must be done in this ASGI middleware — not in the
        # @app.middleware("http") correlation_middleware — so the ContextVar propagates.
        correlation_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        try:
            set_correlation_id(correlation_id)
        except Exception as exc:  # noqa: BLE001 — tracing is best-effort; never fail the request
            log.debug(f"[actor-context] correlation_id set skipped (non-fatal): {exc}")

        # X-Pipeline-ID bridges UI → Agents → Plugins for end-to-end trace correlation
        pipeline_execution_id = request.headers.get("X-Pipeline-ID", "")
        if pipeline_execution_id:
            try:
                set_pipeline_execution_id(pipeline_execution_id)
            except Exception as exc:  # noqa: BLE001
                log.debug(f"[actor-context] pipeline_execution_id set skipped (non-fatal): {exc}")

        try:
            set_actor(get_current_user(request), get_current_user_name(request))
        except Exception as exc:  # noqa: BLE001 — provenance is best-effort; never fail the request
            log.debug(f"[actor-context] actor resolution skipped (non-fatal): {exc}")

        await self.app(scope, receive, send)
