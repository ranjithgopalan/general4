"""
Custom API-key + HMAC-signature middleware (service/client auth atop Okta).

Each client sends three headers:
  - ``X-Platform-Api-Key``   (settings.API_KEY_HEADER_NAME)     — client/key identifier
  - ``X-Platform-Signature`` (settings.API_KEY_SIGNATURE_HEADER) — HMAC-SHA256 signature
  - ``X-Platform-Timestamp``                                    — ISO-8601 UTC timestamp

The canonical string that is signed is::

    <ISO-8601 UTC timestamp>\\n<METHOD>\\n<path>\\n<hex(sha256(body))>

The HMAC secret is resolved from Vault via ``settings.API_KEY_VAULT_NAME``.
When ``API_KEY_ENABLED`` is ``False`` the middleware is transparent.

Sign helper (clients/tests)::

    from app.middleware.api_key_middleware import sign_request
    headers = sign_request(method="POST", path="/fe/generate", body=b"{...}", secret="my-secret")
"""

import hashlib
import hmac
from datetime import UTC, datetime

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.status import HTTP_401_UNAUTHORIZED

from app.config.settings import get_settings
from app.utils.exceptions import ApiKeyError
from app.utils.logging import log

TIMESTAMP_HEADER = "X-Platform-Timestamp"
_BASE_EXCLUDED: set[str] = {"/", "/health", "/docs", "/redoc", "/openapi.json"}


def _canonical_string(timestamp: str, method: str, path: str, body: bytes) -> str:
    body_hash = hashlib.sha256(body).hexdigest()
    return f"{timestamp}\n{method.upper()}\n{path}\n{body_hash}"


def sign_request(method: str, path: str, body: bytes, secret: str, timestamp: str | None = None) -> dict[str, str]:
    """Compute the HMAC-SHA256 signature and return the signature + timestamp headers."""
    settings = get_settings()
    ts = timestamp or datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    canon = _canonical_string(ts, method, path, body)
    sig = hmac.new(secret.encode(), canon.encode(), hashlib.sha256).hexdigest()
    return {settings.API_KEY_SIGNATURE_HEADER: sig, TIMESTAMP_HEADER: ts}


def verify_signature(
    signature: str,
    timestamp: str,
    method: str,
    path: str,
    body: bytes,
    secret: str,
    clock_skew_seconds: int = 300,
) -> None:
    """Verify HMAC-SHA256 signature + timestamp freshness. Raises ApiKeyError on failure."""
    try:
        ts = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        skew = abs((datetime.now(tz=UTC) - ts).total_seconds())
        if skew > clock_skew_seconds:
            raise ApiKeyError(
                f"Timestamp skew {skew:.0f}s exceeds allowed {clock_skew_seconds}s",
                details={"timestamp": timestamp, "skew_seconds": skew},
            )
    except ApiKeyError:
        raise
    except Exception as exc:
        raise ApiKeyError(f"Invalid timestamp format: {timestamp}") from exc

    canon = _canonical_string(timestamp, method, path, body)
    expected = hmac.new(secret.encode(), canon.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise ApiKeyError("API key signature mismatch")


class ApiKeyMiddleware(BaseHTTPMiddleware):
    """Require + verify the HMAC-signed API-key headers on protected routes.

    Runs BEFORE EntitlementMiddleware so an unsigned request is rejected before any
    JWT/entitlement work is done.
    """

    def __init__(self, app, excluded_paths: set[str] | None = None) -> None:
        super().__init__(app)
        self._settings = get_settings()
        self._enabled: bool = self._settings.API_KEY_ENABLED
        self._excluded: set[str] = _BASE_EXCLUDED | (excluded_paths or set())
        log.info(f"[api_key_mw] Initialized: enabled={self._enabled} excluded={sorted(self._excluded)}")

    async def dispatch(self, request: Request, call_next):
        request_id = getattr(request.state, "correlation_id", "unknown")
        path = request.url.path

        if path in self._excluded or any(
            path.startswith(ep) for ep in self._excluded if ep.endswith("/") and len(ep) > 1
        ):
            return await call_next(request)
        if request.method == "OPTIONS":
            return await call_next(request)
        if not self._enabled:
            log.debug(f"[api_key_mw] Disabled — passing through | id={request_id}")
            return await call_next(request)

        api_key = request.headers.get(self._settings.API_KEY_HEADER_NAME)
        signature = request.headers.get(self._settings.API_KEY_SIGNATURE_HEADER)
        timestamp = request.headers.get(TIMESTAMP_HEADER)
        if not api_key or not signature or not timestamp:
            log.warning(f"[api_key_mw] Missing required headers on {path} | id={request_id}")
            return self._error_response(
                f"Missing required headers ({self._settings.API_KEY_HEADER_NAME}, "
                f"{self._settings.API_KEY_SIGNATURE_HEADER}, {TIMESTAMP_HEADER})",
                request_id,
            )

        body = await request.body()
        try:
            secret = await self._get_hmac_secret(api_key)
        except Exception as exc:
            log.error(f"[api_key_mw] Failed to resolve HMAC secret: {exc} | id={request_id}")
            return self._error_response("API key secret unavailable", request_id)

        try:
            verify_signature(
                signature=signature,
                timestamp=timestamp,
                method=request.method,
                path=path,
                body=body,
                secret=secret,
                clock_skew_seconds=self._settings.API_KEY_CLOCK_SKEW_SECONDS,
            )
        except ApiKeyError as exc:
            log.warning(f"[api_key_mw] Signature rejected: {exc.message} | id={request_id}")
            return self._error_response(exc.message, request_id)

        log.debug(f"[api_key_mw] Signature verified for key={api_key} | id={request_id}")
        return await call_next(request)

    async def _get_hmac_secret(self, api_key: str) -> str:
        from app.utils.hashicorp_client import get_vault_client

        vault_name = self._settings.API_KEY_VAULT_NAME or api_key
        creds = await get_vault_client().get_credentials(vault_name)
        secret = creds.key or creds.password
        if not secret:
            raise ApiKeyError("HMAC secret not found in Vault for the provided API key")
        return secret

    @staticmethod
    def _error_response(message: str, request_id: str) -> JSONResponse:
        return JSONResponse(
            status_code=HTTP_401_UNAUTHORIZED,
            content={"error_code": "INVALID_API_KEY", "message": message, "request_id": request_id},
        )
