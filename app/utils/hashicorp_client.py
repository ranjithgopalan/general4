"""
HashiCorp Vault client.

Resolves a secret by appcode (``VAULT_APP_CODE``) + vault name and returns the
credential fields (username/password or key). Wraps the HTTP call in tenacity
retries and caches successful lookups in memory for ``VAULT_CRED_CACHE_TTL`` seconds.

Usage::

    from app.utils.hashicorp_client import get_vault_client

    creds = await get_vault_client().get_credentials(settings.PG_VAULT_NAME)
    # creds.username, creds.password (or creds.key for API tokens / HMAC secrets)
"""

import time
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

import httpx
from tenacity import before_sleep_log, retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.config.settings import get_settings
from app.utils.logging import log


@dataclass(frozen=True)
class VaultCredentials:
    """Immutable credential record returned by the vault client."""

    username: str = ""
    password: str = ""
    key: str = ""  # API tokens / HMAC secrets that don't fit user/pass
    extra: dict[str, Any] = field(default_factory=dict)

    def as_basic_auth(self) -> tuple[str, str]:
        return self.username, self.password


@dataclass
class _CacheEntry:
    creds: VaultCredentials
    fetched_at: float


class VaultClient:
    """Async-capable Vault credential client with a TTL in-memory cache (per process)."""

    def __init__(self) -> None:
        self._settings = get_settings()
        self._cache: dict[str, _CacheEntry] = {}
        log.info(
            f"[vault] Resolving secrets from {self._settings.HASHICORP_API_URL} "
            f"(app_code={self._settings.VAULT_APP_CODE})."
        )

    async def get_credentials(self, vault_name: str, app_code: str | None = None) -> VaultCredentials:
        """Resolve credentials for the given vault name (cache-first).

        ``app_code`` overrides the default ``VAULT_APP_CODE`` for this lookup only —
        secrets can live under different appcodes (e.g. Postgres under ``ah``,
        Bedrock under the platform default). The cache key includes the appcode so
        the same vault_name under two appcodes never collides.
        """
        code = app_code or self._settings.VAULT_APP_CODE
        cache_key = f"{code}:{vault_name}"
        entry = self._cache.get(cache_key)
        if entry and (time.monotonic() - entry.fetched_at) < self._settings.VAULT_CRED_CACHE_TTL:
            log.debug(f"[vault] Cache hit for {cache_key}")
            return entry.creds
        creds = await self._fetch_from_vault(vault_name, code)
        self._cache[cache_key] = _CacheEntry(creds=creds, fetched_at=time.monotonic())
        return creds

    def invalidate(self, vault_name: str | None = None) -> None:
        if vault_name:
            # A vault_name may be cached under several appcodes; drop them all.
            for key in [k for k in self._cache if k.endswith(f":{vault_name}")]:
                self._cache.pop(key, None)
            log.info(f"[vault] Cache invalidated for vault_name={vault_name}")
        else:
            self._cache.clear()
            log.info("[vault] Entire credential cache invalidated")

    async def _fetch_from_vault(self, vault_name: str, app_code: str) -> VaultCredentials:
        """Call the Vault HTTP API with retries and parse the response."""
        from app.utils.exceptions import VaultError  # avoid circular import at module load

        @retry(
            retry=retry_if_exception_type((httpx.TimeoutException, httpx.RequestError)),
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=1, min=1, max=10),
            before_sleep=before_sleep_log(log, 30),  # WARNING
            reraise=True,
        )
        async def _do_fetch() -> dict[str, Any]:
            payload = {
                "region": self._settings.HASHICORP_VAULT_REGION or self._settings.ENV,
                "app_code": app_code,
                "vault_name": vault_name,
            }
            headers = {"Content-Type": "application/json"}
            token = getattr(self._settings, "HASHICORP_AUTH_TOKEN", "") or ""
            if token:
                headers["Authorization"] = token if token.startswith("Bearer ") else f"Bearer {token}"
            async with httpx.AsyncClient(timeout=self._settings.HASHICORP_API_TIMEOUT, verify=False) as client:
                resp = await client.post(self._settings.HASHICORP_API_URL, json=payload, headers=headers)
                resp.raise_for_status()
                return resp.json()

        try:
            result = await _do_fetch()
        except (httpx.TimeoutException, httpx.RequestError, httpx.HTTPStatusError) as exc:
            raise VaultError(
                f"Vault API call failed for vault_name={vault_name}: {exc}",
                details={"vault_name": vault_name, "error": str(exc)},
            ) from exc
        except Exception as exc:
            raise VaultError(
                f"Unexpected error fetching vault_name={vault_name}: {exc}",
                details={"vault_name": vault_name},
            ) from exc

        return self._parse_vault_response(result, vault_name)

    @staticmethod
    def _parse_vault_response(result: dict[str, Any], vault_name: str) -> VaultCredentials:
        """Parse the Vault API response envelope into a VaultCredentials."""
        import json as _json

        body = result.get("body", {})
        if isinstance(body, str):
            try:
                body = _json.loads(body)
            except Exception:
                log.error(f"[vault] Could not parse body JSON for vault_name={vault_name}")
                body = {}
        if not isinstance(body, dict):
            log.warning(f"[vault] Unexpected body type for vault_name={vault_name}: {type(body)}")
            body = {}

        # Vault key casing varies (PG_DB_USER vs pg_db_user); match case-insensitively.
        low = {str(k).lower(): v for k, v in body.items()}
        username = low.get("username") or low.get("user") or low.get("pg_db_user") or ""
        password = low.get("password") or low.get("pwd") or low.get("pg_db_pwd") or ""
        key = low.get("key") or low.get("api_key") or low.get("hmac_secret") or low.get("secret") or ""

        log.info(f"[vault] Successfully resolved credentials for vault_name={vault_name}")
        return VaultCredentials(username=username, password=password, key=key, extra=body)


@lru_cache(maxsize=1)
def get_vault_client() -> VaultClient:
    """Return the process-wide singleton VaultClient."""
    return VaultClient()
