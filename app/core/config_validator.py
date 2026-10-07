"""
Startup configuration validation.

Validates that settings are coherent for the active environment and surfaces
missing-but-required values early — fail-loud in uat/prod. Never logs secret
values, only presence/absence and names.
"""

from app.config.settings import Settings
from app.utils.exceptions import ConfigurationError
from app.utils.logging import log


def validate_configuration(settings: Settings) -> None:
    """Validate settings at startup. Raises ConfigurationError on a fatal issue (uat/prod)."""
    errors: list[str] = []
    is_dev = settings.ENV.lower() in ("dev", "local", "test")

    # ── Edge security must be enforced outside dev ────────────────────────────
    if not is_dev:
        if not settings.JWT_VALIDATION_ENABLED:
            errors.append("JWT_VALIDATION_ENABLED must be True outside dev.")
        if settings.JWT_DEV_BYPASS:
            errors.append("JWT_DEV_BYPASS must be False outside dev.")
        if not settings.ENTITLEMENT_ENABLED:
            errors.append("ENTITLEMENT_ENABLED must be True outside dev.")
        if not settings.API_KEY_ENABLED:
            errors.append("API_KEY_ENABLED must be True outside dev.")
        if "*" in settings.CORS_ORIGINS:
            errors.append("Wildcard CORS origins are not allowed outside dev.")
        if not settings.HASHICORP_API_URL:
            errors.append("HASHICORP_API_URL (Vault) is required outside dev.")

    # ── AuthN config presence when the relevant guard is on ───────────────────
    if settings.JWT_VALIDATION_ENABLED and not settings.OKTA_JWKS_URL:
        errors.append("OKTA_JWKS_URL is required when JWT_VALIDATION_ENABLED is True.")
    if settings.ENTITLEMENT_ENABLED and not settings.ENTITLEMENT_API_URL:
        errors.append("ENTITLEMENT_API_URL is required when ENTITLEMENT_ENABLED is True.")

    # ── Data layer ────────────────────────────────────────────────────────────
    if not is_dev and not settings.PGHOST:
        errors.append("PGHOST is required outside dev (Postgres is the sole relational + vector store).")
    if settings.EMBED_DIM <= 0:
        errors.append("EMBED_DIM must be a positive integer (1024 Titan v2 / 1536 v1).")
    if settings.GRAPH_PROVIDER not in ("postgres", "neo4j"):
        errors.append("GRAPH_PROVIDER must be 'postgres' (v1) or 'neo4j' (v2).")

    if errors:
        for e in errors:
            log.error(f"[config] {e}")
        raise ConfigurationError(
            "Invalid configuration for this environment.",
            details={"errors": errors, "env": settings.ENV},
        )

    log.info(f"[config] Configuration validated for env='{settings.ENV}'.")
