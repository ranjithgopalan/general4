"""
Authentication service — Okta JWT validation (RS256 via JWKS).

When ``JWT_VALIDATION_ENABLED`` is ``False`` (dev/test bypass), ``validate_token``
returns a synthetic dev principal without any signature check — so routes stay
exercisable locally without a real Okta token. Inbound-validation only (no issuance).
"""

from functools import lru_cache
from typing import Any

import jwt

from app.config.settings import get_settings
from app.utils.exceptions import AuthenticationError
from app.utils.jwks_client import JWKSClient
from app.utils.logging import log

_DEV_PRINCIPAL: dict[str, Any] = {
    "sub": "dev-user@aig.com",
    "email": "dev-user@aig.com",
    "lanid": "DEV001",
    "user_id": "dev-001",
    "firstname": "Dev",
    "lastname": "User",
    "roles": ["admin"],
    "groups": [],
    "aud": "dev",
    "is_admin": True,
}


class AuthService:
    """Validate Okta JWTs and extract a principal dict (dev-bypass when disabled)."""

    def __init__(self) -> None:
        self._settings = get_settings()
        self._validation_enabled: bool = self._settings.JWT_VALIDATION_ENABLED
        self._algorithm: str = self._settings.JWT_ALGORITHM
        self._jwks_client: JWKSClient | None = None

        if self._validation_enabled and self._settings.OKTA_JWKS_URL:
            self._jwks_client = JWKSClient(self._settings.OKTA_JWKS_URL)
            log.info("[auth] JWT validation enabled; JWKS client initialized")
        elif not self._validation_enabled:
            log.warning(
                "[auth] JWT_VALIDATION_ENABLED=False — dev bypass active; ALL requests treated as authenticated"
            )

    def validate_token(self, token: str) -> dict[str, Any]:
        """Validate ``token`` and return a principal dict. Raises AuthenticationError (live mode)."""
        if not self._validation_enabled:
            log.debug("[auth] JWT validation disabled — returning dev principal")
            try:
                payload = jwt.decode(token, options={"verify_signature": False})
                return self._build_principal(payload, dev_bypass=True)
            except Exception:
                return dict(_DEV_PRINCIPAL)

        if self._algorithm == "RS256" and self._jwks_client:
            return self._validate_rs256(token)
        raise AuthenticationError("No JWKS client configured for RS256 validation; set OKTA_JWKS_URL.")

    def pre_warm_jwks(self) -> None:
        """Pre-fetch JWKS keys at startup to reduce first-request latency."""
        if self._jwks_client:
            try:
                self._jwks_client._fetch_keys()
                log.info("[auth] JWKS pre-warm completed")
            except Exception as exc:
                log.warning(f"[auth] JWKS pre-warm failed (non-fatal): {exc}")

    def _validate_rs256(self, token: str) -> dict[str, Any]:
        try:
            header = jwt.get_unverified_header(token)
            kid = header.get("kid")
            if not kid:
                raise AuthenticationError("Token missing key ID (kid)")

            signing_key = self._jwks_client.get_signing_key(kid)  # type: ignore[union-attr]
            if signing_key is None:
                raise AuthenticationError(f"Signing key not found for kid={kid}")

            decode_options: dict[str, Any] = {"verify_exp": True}
            decode_kwargs: dict[str, Any] = {"algorithms": ["RS256"], "options": decode_options}
            if self._settings.OKTA_ISSUER:
                decode_kwargs["issuer"] = self._settings.OKTA_ISSUER
            if self._settings.OKTA_AUDIENCE:
                decode_kwargs["audience"] = self._settings.OKTA_AUDIENCE
            else:
                decode_options["verify_aud"] = False

            payload = jwt.decode(token, signing_key.key, **decode_kwargs)
            principal = self._build_principal(payload, dev_bypass=False)
            log.debug(f"[auth] Token validated for user={principal.get('email', 'unknown')}")
            return principal

        except jwt.ExpiredSignatureError as exc:
            raise AuthenticationError("Token expired") from exc
        except jwt.InvalidTokenError as exc:
            raise AuthenticationError(f"Invalid token: {exc}") from exc
        except AuthenticationError:
            raise
        except Exception as exc:
            raise AuthenticationError(f"Unexpected token validation error: {exc}") from exc

    @staticmethod
    def _build_principal(payload: dict[str, Any], *, dev_bypass: bool) -> dict[str, Any]:
        """Extract a standardised principal dict from a decoded JWT payload."""
        groups = [g.lower() for g in payload.get("groups", [])]
        is_admin = dev_bypass or ("admin" in [r.lower() for r in payload.get("roles", [])])
        firstname = payload.get("firstname", "")
        lastname = payload.get("lastname", "")
        # Display name: explicit name claim → first+last → preferred_username → email/sub.
        name = (
            payload.get("name")
            or (f"{firstname} {lastname}".strip() if (firstname or lastname) else "")
            or payload.get("preferred_username")
            or payload.get("email")
            or payload.get("sub", "")
        )
        return {
            "sub": payload.get("sub", ""),
            "email": payload.get("email", payload.get("sub", "")),
            "name": name,
            "lanid": payload.get("lanid", ""),
            "user_id": payload.get("uid", payload.get("user_id", "")),
            "firstname": firstname,
            "lastname": lastname,
            "roles": payload.get("roles", []),
            "groups": groups,
            "aud": payload.get("aud", ""),
            "is_admin": is_admin,
        }


@lru_cache(maxsize=1)
def get_auth_service() -> AuthService:
    """Return the process-wide singleton AuthService."""
    return AuthService()
