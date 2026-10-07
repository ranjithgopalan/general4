"""Okta OIDC token validation and FastAPI dependencies.

Design decisions worth stating
-----------------------------
1. **Auth-disabled mode exists, but it is loud and it is not the default in any
   deployment.** the knowledge base ships `AUTH_ENABLED=false` with a dependency that silently
   returns a mock admin; that is convenient locally and indistinguishable from
   working auth in production. Here, disabling auth requires setting
   `FE_AUTH_ENABLED=false`, logs a warning on every startup, and is reported by
   `/health/deep` as a degraded check.

2. **Tokens are validated against the issuer's JWKS, never merely decoded.**
   Reading claims from an unverified token is the same as having no auth.

3. **Deny by default.** An endpoint that forgets its dependency is closed, not
   open, because the artefact filter is applied inside the read path rather than
   bolted on per route.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.agentic_platform.fe_core.auth.roles import Persona, Principal, SystemRole, personas_from_claims
from app.agentic_platform.fe_core.config import get_settings

logger = logging.getLogger(__name__)

_bearer = HTTPBearer(auto_error=False)

#: JWKS cache: {issuer: (fetched_at, keys)}
_jwks_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_JWKS_TTL_SECONDS = 3600


class AuthConfigurationError(RuntimeError):
    """Auth is enabled but not configured well enough to validate anything."""


def _jwks_sync(jwks_uri: str) -> dict[str, Any]:
    """Fetch and cache the issuer's JWKS.

    Synchronous on purpose: `jose.jwt.decode` is synchronous, so an async fetch
    here would need a nested event loop. The call happens at most once per hour per
    issuer, and runs inside FastAPI's threadpool for sync dependencies.
    """
    cached = _jwks_cache.get(jwks_uri)
    now = time.time()
    if cached and now - cached[0] < _JWKS_TTL_SECONDS:
        return cached[1]
    with httpx.Client(timeout=10.0) as client:
        response = client.get(jwks_uri)
    response.raise_for_status()
    keys = response.json()
    _jwks_cache[jwks_uri] = (now, keys)
    return keys


def _decode_and_verify(token: str, settings: Any) -> dict[str, Any]:
    """Verify signature, issuer, audience and expiry.

    `python-jose` is imported lazily so the service starts without it when auth is
    disabled -- but when auth is ENABLED and it is missing, that is a hard failure
    rather than a silent downgrade to unverified decoding.
    """
    try:
        from jose import jwt  # type: ignore
        from jose.exceptions import JWTError  # type: ignore
    except ImportError as exc:  # pragma: no cover
        raise AuthConfigurationError(
            "FE_AUTH_ENABLED=true but python-jose is not installed. "
            "`pip install 'python-jose[cryptography]'`. Refusing to accept "
            "unverified tokens."
        ) from exc

    if not settings.fe_auth_issuer:
        raise AuthConfigurationError(
            "FE_AUTH_ENABLED=true but FE_AUTH_ISSUER is unset. A token cannot be "
            "validated without an issuer."
        )

    jwks_uri = (
        settings.fe_auth_jwks_uri
        or f"{settings.fe_auth_issuer.rstrip('/')}/v1/keys"
    )
    keys = _jwks_sync(jwks_uri)

    try:
        return jwt.decode(
            token,
            keys,
            algorithms=["RS256"],
            issuer=settings.fe_auth_issuer,
            audience=settings.fe_auth_audience or None,
            options={"verify_aud": bool(settings.fe_auth_audience)},
        )
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"token rejected: {exc}",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


def _principal_from_claims(claims: dict[str, Any]) -> Principal:
    groups = claims.get("groups") or claims.get("roles") or []
    if isinstance(groups, str):
        groups = [groups]
    scopes = str(claims.get("scp") or claims.get("scope") or "").split()
    personas, system_roles = personas_from_claims(list(groups), scopes)
    if not personas and not system_roles:
        # A valid token with no recognised group is authenticated but unauthorised.
        # Granting a default persona here is how quiet privilege escalation happens.
        logger.warning(
            "Token for %s carries no recognised persona (groups=%s)",
            claims.get("sub"), groups,
        )
    return Principal(
        subject=str(claims.get("sub") or "unknown"),
        email=claims.get("email"),
        personas=personas,
        system_roles=system_roles,
        scopes=frozenset(scopes),
        auth_mode="okta",
    )


async def current_principal(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> Principal:
    """Resolve the caller. Raises 401 when auth is enabled and the token is bad."""
    settings = get_settings()

    if not settings.fe_auth_enabled:
        # Development only. Warned at startup and surfaced by /health/deep.
        #
        # Two dev modes:
        #   - X-Dev-Personas header present: the caller has "signed in" as one
        #     or more named personas via the UI picker. FR-P1 (code visibility)
        #     and FR-P3 (approval authority) are then enforced against exactly
        #     those personas, which makes the persona model exercisable locally.
        #   - header absent: every persona plus administrator, so no gate is
        #     hidden by accident while auth is off.
        # Unrecognised names are ignored, not granted (same rule as Okta groups).
        raw_personas = request.headers.get("x-dev-personas", "").strip()
        if raw_personas:
            names = [p.strip() for p in raw_personas.split(",") if p.strip()]
            personas, system_roles = personas_from_claims(names)
            who = request.headers.get("x-dev-user", "").strip() or "dev-user"
            return Principal(
                subject=who,
                email=who if "@" in who else None,
                personas=personas,
                system_roles=system_roles,
                auth_mode="dev-persona",
            )
        return Principal(
            subject="dev-user",
            email="dev@local",
            personas=frozenset(Persona),
            system_roles=frozenset({SystemRole.ADMINISTRATOR}),
            auth_mode="disabled",
        )

    if credentials is None or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="a bearer token is required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        claims = _decode_and_verify(credentials.credentials, settings)
    except AuthConfigurationError as exc:
        # A misconfiguration must not silently accept traffic.
        logger.error("Auth misconfigured: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    principal = _principal_from_claims(claims)
    request.state.principal = principal
    return principal


def require_personas(*personas: Persona):
    """Dependency factory: the caller must hold at least one of `personas`.

    Administrator passes for *access* checks like this one, but never for
    approval (FR-P3) -- `Principal.may_approve` deliberately ignores admin.
    """

    async def _check(principal: Principal = Depends(current_principal)) -> Principal:
        if principal.is_admin or any(principal.has_persona(p) for p in personas):
            return principal
        held = ", ".join(sorted(p.value for p in principal.personas)) or "none"
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "requires one of: " + ", ".join(sorted(p.value for p in personas))
                + f"; you hold: {held}"
            ),
        )

    return _check


#: Retained alias so existing route wiring keeps working.
require_roles = require_personas


async def require_code_access(
    principal: Principal = Depends(current_principal),
) -> Principal:
    """FR-P1: code artefacts are invisible to Global-workspace roles."""
    if not principal.can_see_code:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "this artefact is source code, which the Global Workspace cannot "
                "access. A Hybrid persona (developer, tester, devops, uat) is "
                "required."
            ),
        )
    return principal
