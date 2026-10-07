"""
Entitlement middleware.

Starlette ``BaseHTTPMiddleware`` that:
  1. Skips excluded paths (health probes, docs) and CORS OPTIONS pre-flight.
  2. When ``ENTITLEMENT_ENABLED`` is False, derives identity from the Bearer token
     (so sessions/audit are per-user) but does not enforce.
  3. Extracts the Bearer JWT, validates it via AuthService.
  4. Calls EntitlementService.check_entitlement(); on deny returns 403.
  5. Stores ``request.state.user`` + ``request.state.auth_token`` on success.

SSE endpoints (path suffix ``/stream``) bypass the dispatch/call_next relay — that
relay re-frames a long-lived StreamingResponse in ways that break behind an ALB under
HTTP/2 — running the identical auth check inline instead.
"""

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.status import HTTP_401_UNAUTHORIZED, HTTP_403_FORBIDDEN, HTTP_503_SERVICE_UNAVAILABLE

from app.config.settings import get_settings
from app.services.auth import get_auth_service
from app.services.entitlement import get_entitlement_service
from app.utils.exceptions import EntitlementError, ServiceUnavailableError
from app.utils.logging import log

_BASE_EXCLUDED: set[str] = {"/", "/health", "/docs", "/redoc", "/openapi.json"}
_SSE_PATH_SUFFIXES: tuple = ("/stream",)


class EntitlementMiddleware(BaseHTTPMiddleware):
    """Validate Okta JWT + entitlement before any protected route."""

    def __init__(self, app, excluded_paths: set[str] | None = None) -> None:
        super().__init__(app)
        settings = get_settings()
        self._auth_service = get_auth_service()
        self._entitlement_service = get_entitlement_service()
        self._enabled: bool = settings.ENTITLEMENT_ENABLED
        self._excluded: set[str] = _BASE_EXCLUDED | (excluded_paths or set())
        log.info(f"[entitlement_mw] Initialized: enabled={self._enabled} excluded={sorted(self._excluded)}")

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] == "http" and scope["path"].endswith(_SSE_PATH_SUFFIXES):
            request = Request(scope, receive=receive)
            path = request.url.path
            is_excluded = path in self._excluded or any(
                path.startswith(ep) for ep in self._excluded if ep.endswith("/") and len(ep) > 1
            )
            if not is_excluded and request.method != "OPTIONS":
                error_response = await self._authorize(request)
                if error_response is not None:
                    await error_response(scope, receive, send)
                    return
            await self.app(scope, receive, send)
            return
        await super().__call__(scope, receive, send)

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path in self._excluded or any(
            path.startswith(ep) for ep in self._excluded if ep.endswith("/") and len(ep) > 1
        ):
            return await call_next(request)
        if request.method == "OPTIONS":
            return await call_next(request)

        error_response = await self._authorize(request)
        if error_response is not None:
            return error_response
        return await call_next(request)

    async def _authorize(self, request: Request) -> JSONResponse | None:
        """JWT + entitlement checks. None on success (state set); JSONResponse on failure."""
        request_id = getattr(request.state, "correlation_id", "unknown")
        path = request.url.path

        if not self._enabled:
            # Derive identity for per-user scoping even when enforcement is off.
            auth_header = request.headers.get("Authorization", "")
            if auth_header.startswith("Bearer "):
                try:
                    request.state.user = self._auth_service.validate_token(auth_header.split("Bearer ", 1)[-1].strip())
                except Exception as exc:
                    log.debug(f"[entitlement_mw] identity not derived: {exc} | id={request_id}")
            return None

        auth_header = request.headers.get("Authorization", "")
        if not auth_header or not auth_header.startswith("Bearer "):
            log.warning(f"[entitlement_mw] Missing/invalid Authorization header | id={request_id}")
            return JSONResponse(
                status_code=HTTP_401_UNAUTHORIZED,
                content={
                    "error_code": "UNAUTHORIZED",
                    "message": "Authorization header missing or not Bearer",
                    "request_id": request_id,
                },
            )
        token = auth_header.split("Bearer ", 1)[-1].strip()

        try:
            user_info = self._auth_service.validate_token(token)
        except Exception as exc:
            log.warning(f"[entitlement_mw] JWT validation failed: {exc} | id={request_id}")
            return JSONResponse(
                status_code=HTTP_401_UNAUTHORIZED,
                content={"error_code": "INVALID_TOKEN", "message": f"Invalid token: {exc}", "request_id": request_id},
            )

        try:
            await self._entitlement_service.check_entitlement(
                user_info=user_info, request_path=path, request_method=request.method, request=request
            )
            user_info["is_admin"] = await self._entitlement_service.check_admin_permission(
                user_info=user_info, request=request
            )
            request.state.user = user_info
            request.state.auth_token = token
            log.info(
                f"[entitlement_mw] Passed for user={user_info.get('email', 'n/a')} on {request.method} {path} | id={request_id}"
            )
            return None
        except EntitlementError as exc:
            log.warning(f"[entitlement_mw] Denied: {exc.message} | id={request_id}")
            return JSONResponse(
                status_code=HTTP_403_FORBIDDEN,
                content={
                    "error_code": exc.error_code,
                    "message": exc.message,
                    "details": exc.details,
                    "request_id": request_id,
                },
            )
        except ServiceUnavailableError as exc:
            log.error(f"[entitlement_mw] Service unavailable: {exc.message} | id={request_id}")
            return JSONResponse(
                status_code=HTTP_503_SERVICE_UNAVAILABLE,
                content={
                    "error_code": exc.error_code,
                    "message": "Entitlement service unavailable",
                    "request_id": request_id,
                },
            )
        except Exception as exc:
            log.error(f"[entitlement_mw] Unexpected error: {exc} | id={request_id}", exc_info=True)
            return JSONResponse(
                status_code=500,
                content={
                    "error_code": "INTERNAL_SERVER_ERROR",
                    "message": "Internal server error during authorization",
                    "request_id": request_id,
                },
            )
