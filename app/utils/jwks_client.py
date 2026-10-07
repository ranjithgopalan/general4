"""
JWKS client — fetches and caches JSON Web Key Sets for Okta RS256 token validation.

  * Explicit cache TTL (1 hour).
  * Fails open: network errors do NOT crash startup; they are logged and None is
    returned so the caller can decide (e.g. fall through to dev bypass).
  * httpx for the fetch.
"""

import time

import httpx
from jwt.api_jwk import PyJWK

from app.utils.logging import log


class JWKSClient:
    """Fetch and cache Okta JWKS for JWT signature verification."""

    _CACHE_TTL_SECONDS: int = 3600  # 1 hour

    def __init__(self, jwks_uri: str) -> None:
        self.jwks_uri = jwks_uri
        self._cache: dict[str, PyJWK] = {}
        self._last_update: float = 0.0
        log.info(f"[jwks] Initialized JWKS client for URI={jwks_uri}")

    def get_signing_key(self, kid: str) -> PyJWK | None:
        """Return the signing key for the given key-id, refreshing cache if stale."""
        self._refresh_if_stale()
        if kid not in self._cache:
            log.debug(f"[jwks] kid={kid} not in cache; re-fetching JWKS")
            self._fetch_keys()
        key = self._cache.get(kid)
        if key is None:
            log.error(f"[jwks] Signing key not found for kid={kid}; available={list(self._cache)}")
        return key

    def _refresh_if_stale(self) -> None:
        if time.time() - self._last_update > self._CACHE_TTL_SECONDS:
            log.debug("[jwks] Cache stale — refreshing")
            self._fetch_keys()

    def _fetch_keys(self) -> None:
        try:
            log.debug(f"[jwks] Fetching JWKS from {self.jwks_uri}")
            response = httpx.get(self.jwks_uri, timeout=10)
            response.raise_for_status()
            jwks = response.json()

            new_cache: dict[str, PyJWK] = {}
            for key_data in jwks.get("keys", []):
                kid = key_data.get("kid")
                kty = key_data.get("kty")
                if not kid or kty != "RSA":
                    log.debug(f"[jwks] Skipping key kid={kid} kty={kty}")
                    continue
                try:
                    new_cache[kid] = PyJWK(key_data)
                except Exception as exc:
                    log.error(f"[jwks] Failed to parse key kid={kid}: {exc}")

            self._cache = new_cache
            self._last_update = time.time()
            log.info(f"[jwks] Cached {len(self._cache)} RSA key(s) from JWKS")
        except (httpx.RequestError, httpx.HTTPStatusError) as exc:
            log.error(f"[jwks] Network error fetching JWKS: {exc}")
        except Exception as exc:
            log.error(f"[jwks] Unexpected error fetching JWKS: {exc}", exc_info=True)
