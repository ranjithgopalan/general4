"""
Entitlement service — checks user access against the external entitlement API.

Features: response caching (TTL = ENTITLEMENT_CACHE_TTL), circuit-breaker on the
HTTP call, and stub pass-through when ``ENTITLEMENT_ENABLED`` is False.

Base check: the caller must hold a permitted entry for the application resource
(``ENTITLEMENT_APPLICATION_NAME``). The finer **persona → includeKinds / capability**
mapping (docs/04 §9, CLAUDE.md §6) is the extension point — a `# TODO(business):`
seam layered on top of this base grant.
"""

import time
from functools import lru_cache
from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.config.settings import get_settings
from app.utils.exceptions import AuthorizationError, EntitlementError, ServiceUnavailableError
from app.utils.logging import log

_ADMIN_ACTIONS = frozenset({"admin", "superadmin"})


class EntitlementService:
    """Check user entitlement via the external entitlement API (cached, breaker-protected)."""

    def __init__(self) -> None:
        self._settings = get_settings()
        self.enabled: bool = self._settings.ENTITLEMENT_ENABLED
        self._api_url: str = self._settings.ENTITLEMENT_API_URL
        self._timeout: int = self._settings.ENTITLEMENT_API_TIMEOUT
        self._cache_ttl: int = self._settings.ENTITLEMENT_CACHE_TTL
        self._resource_name: str = self._settings.ENTITLEMENT_APPLICATION_NAME or self._settings.APPLICATION_NAME
        self._user_agent: str = self._settings.ENTITLEMENT_USER_AGENT
        self._auth_cache: dict[str, tuple[bool, float]] = {}
        self._perm_cache: dict[str, tuple[Any, float]] = {}
        # Shared breaker: must live on the instance so failure counts accumulate across calls.
        from app.utils.circuit_breaker import CircuitBreaker  # noqa: PLC0415
        self._breaker = CircuitBreaker(
            failure_threshold=self._settings.CIRCUIT_BREAKER_FAILURE_THRESHOLD,
            timeout_duration=self._settings.CIRCUIT_BREAKER_TIMEOUT_DURATION,
            name="entitlement_api",
        )
        log.info(
            f"[entitlement] Initialized: enabled={self.enabled} api_url={self._api_url!r} resource={self._resource_name!r}"
        )

    async def check_entitlement(
        self,
        user_info: dict[str, Any],
        request_path: str,
        request_method: str,
        request: Any | None = None,
    ) -> bool:
        """Return True if the user is entitled to the application. Raises on deny/unavailable."""
        if not self.enabled:
            log.debug("[entitlement] Disabled — allowing request")
            return True

        user_id = self._get_user_id(user_info)
        cache_key = f"{user_id}:{request_path}:{request_method}"
        cached = self._auth_cache.get(cache_key)
        if cached and (time.time() - cached[1]) < self._cache_ttl:
            log.debug(f"[entitlement] Cache hit for user={user_id}")
            return cached[0]

        permissions = await self._fetch_permissions(user_id, request)
        self._perm_cache[user_id] = (permissions, time.time())
        is_authorized = self._has_permission(permissions, self._resource_name)
        self._auth_cache[cache_key] = (is_authorized, time.time())

        if not is_authorized:
            log.warning(f"[entitlement] Denied for user={user_id} on {request_method} {request_path}")
            raise EntitlementError(
                f"User is not entitled to access '{self._resource_name}'",
                details={"user_id": user_id, "resource": self._resource_name},
            )
        log.info(f"[entitlement] Granted for user={user_id}")
        # TODO(business): apply persona → includeKinds / capability guard here (docs/04 §9).
        return True

    async def check_admin_permission(self, user_info: dict[str, Any], request: Any | None = None) -> bool:
        """Return True if the user has admin/superadmin entitlement."""
        if not self.enabled:
            return True
        user_id = self._get_user_id(user_info)
        cached = self._perm_cache.get(user_id)
        if cached and (time.time() - cached[1]) < self._cache_ttl:
            return self._is_admin(cached[0], self._resource_name)
        try:
            permissions = await self._fetch_permissions(user_id, request)
            self._perm_cache[user_id] = (permissions, time.time())
            return self._is_admin(permissions, self._resource_name)
        except Exception as exc:
            log.warning(f"[entitlement] Admin check failed: {exc}")
            return False

    def clear_cache(self) -> None:
        self._auth_cache.clear()
        self._perm_cache.clear()
        log.info("[entitlement] Cache cleared")

    async def _fetch_permissions(self, user_id: str, request: Any | None) -> list[dict[str, Any]]:
        """Call the entitlement API and return the raw permission list (breaker-protected, retry on transient)."""
        from app.utils.circuit_breaker import CircuitBreakerOpenError  # noqa: PLC0415

        headers: dict[str, str] = {"User-Agent": self._user_agent, "Content-Type": "application/json"}
        if request is not None:
            auth_header = getattr(request, "headers", {}).get("Authorization")
            if auth_header:
                headers["Authorization"] = auth_header

        max_retries = getattr(self._settings, "ENTITLEMENT_MAX_RETRIES", 3)
        base_delay = getattr(self._settings, "ENTITLEMENT_RETRY_BASE_DELAY", 1.0)

        @self._breaker
        @retry(
            retry=retry_if_exception_type((httpx.TimeoutException, httpx.ConnectError,
                                           httpx.RemoteProtocolError)),
            stop=stop_after_attempt(max_retries),
            wait=wait_exponential(multiplier=base_delay, min=1, max=30),
            reraise=True,
        )
        async def _call() -> httpx.Response:
            async with httpx.AsyncClient(timeout=self._timeout, verify=False) as client:
                return await client.post(self._api_url, headers=headers)

        try:
            response: httpx.Response = await _call()
        except CircuitBreakerOpenError as exc:
            log.warning(f"[entitlement] Circuit breaker open: {exc}")
            raise ServiceUnavailableError("Entitlement service is temporarily unavailable") from exc
        except (httpx.TimeoutException, httpx.RequestError) as exc:
            log.error(f"[entitlement] API call failed after retries: {exc}")
            raise ServiceUnavailableError(f"Entitlement API unreachable: {type(exc).__name__}") from exc

        if response.status_code == 403:
            raise EntitlementError("Entitlement API returned 403", details={"user_id": user_id})
        if response.status_code != 200:
            raise ServiceUnavailableError(
                f"Entitlement API returned unexpected status {response.status_code}",
                details={"status_code": response.status_code},
            )
        result = response.json()
        return result if isinstance(result, list) else []

    @staticmethod
    def _get_user_id(user_info: dict[str, Any]) -> str:
        uid = user_info.get("lanid") or user_info.get("email") or user_info.get("user_id") or user_info.get("sub")
        if not uid:
            raise AuthorizationError("No user identifier found in token")
        return uid

    @staticmethod
    def _has_permission(permissions: list[dict[str, Any]], resource: str) -> bool:
        return any(p.get("resource") == resource and p.get("permitted") is True for p in permissions)

    @staticmethod
    def _is_admin(permissions: list[dict[str, Any]], resource: str) -> bool:
        return any(
            p.get("resource") == resource
            and p.get("permitted") is True
            and str(p.get("action", "")).lower() in _ADMIN_ACTIONS
            for p in permissions
        )


@lru_cache(maxsize=1)
def get_entitlement_service() -> EntitlementService:
    """Return the process-wide singleton EntitlementService."""
    return EntitlementService()
