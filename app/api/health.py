"""
Health, liveness, and readiness endpoints (all unauthenticated; excluded from
API-key / entitlement middleware).

    GET /             — root liveness
    GET /health       — combined health + source connectivity (cached 30s)
    GET /health/live  — liveness probe (fast, no external checks)
    GET /health/ready — readiness probe (pings the Postgres pool)

Status values: "ok" (healthy) · "degraded" (reduced/uninitialized) · "down" (unreachable).
"""

from __future__ import annotations

import time

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.config import get_settings

router = APIRouter(tags=["health"])

# Lightweight in-memory cache: {key: (value, expires_at)}
_source_cache: dict[str, tuple] = {}
_CACHE_TTL = 30  # seconds


def _cached(key: str) -> str | None:
    entry = _source_cache.get(key)
    if entry and time.time() < entry[1]:
        return entry[0]
    return None


def _set_cached(key: str, value: str) -> None:
    _source_cache[key] = (value, time.time() + _CACHE_TTL)


async def _check_postgres() -> str:
    """Ping the psycopg async pool (M2). Degrades gracefully before the pool exists."""
    cached = _cached("postgres")
    if cached:
        return cached
    settings = get_settings()
    if not settings.PGHOST:
        # No DB configured for this env (local/no-DB) — boot-ready, not "down".
        result = "degraded"
    else:
        try:
            from app.dao.postgres import check_health

            result = "ok" if await check_health() else "down"
        except Exception:
            result = "down"
    _set_cached("postgres", result)
    return result


def _check_vault() -> str:
    """Check the Vault client is configured (lightweight)."""
    cached = _cached("vault")
    if cached:
        return cached
    try:
        from app.utils.hashicorp_client import get_vault_client  # type: ignore

        get_vault_client()
        result = "ok"
    except Exception:
        result = "degraded"
    _set_cached("vault", result)
    return result


def _check_bedrock() -> str:
    """Verify the Bedrock credential chain is functional (read-only list call, cached 30s).

    Uses ``list_foundation_models(maxResults=1)`` — the cheapest non-identity Bedrock API call.
    Returns ``"degraded"`` (not ``"down"``) so environments without Bedrock access (local dev,
    non-AWS CI) do not fail the readiness probe.
    """
    cached = _cached("bedrock")
    if cached:
        return cached
    settings = get_settings()
    if not settings.AWS_REGION:
        result = "degraded"
    else:
        try:
            import boto3

            client = boto3.client(
                "bedrock",
                region_name=settings.AWS_REGION,
            )
            client.list_foundation_models(byOutputModality="TEXT", maxResults=1)
            result = "ok"
        except Exception:
            result = "degraded"
    _set_cached("bedrock", result)
    return result


async def _source_checks() -> dict[str, str]:
    return {"postgres": await _check_postgres(), "vault": _check_vault(), "bedrock": _check_bedrock()}


def _overall(source_checks: dict[str, str]) -> str:
    values = set(source_checks.values())
    if "down" in values or "degraded" in values:
        return "degraded"
    return "healthy"


@router.get("/")
async def root() -> dict:
    """Liveness root."""
    s = get_settings()
    return {"service": s.APPLICATION_NAME, "version": s.APP_VERSION, "status": "ok"}


@router.get("/health")
async def health() -> dict:
    """Combined liveness + source-connectivity health check (cached 30s)."""
    s = get_settings()
    checks = await _source_checks()
    return {
        "status": _overall(checks),
        "service": s.APPLICATION_NAME,
        "version": s.APP_VERSION,
        "env": s.ENV,
        "gear_id": s.GEAR_ID,
        "sources": checks,
        "cache_ttl_seconds": _CACHE_TTL,
    }


@router.get("/health/live")
async def health_live() -> dict:
    """Liveness probe — fast, no external checks."""
    s = get_settings()
    return {"status": "ok", "service": s.APPLICATION_NAME, "version": s.APP_VERSION}


@router.get("/health/ready")
async def health_ready():
    """Readiness probe — pings Postgres. 200 when ok (or dev with no DB); 503 when down."""
    s = get_settings()
    pg = await _check_postgres()
    ready = pg in ("ok", "degraded")  # degraded = pool not wired yet in dev — still boot-ready
    body = {"status": "ready" if ready else "not_ready", "service": s.APPLICATION_NAME, "postgres": pg}
    if not ready:
        return JSONResponse(status_code=503, content=body)
    return body
