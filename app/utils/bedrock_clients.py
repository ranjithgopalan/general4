"""
Bedrock boto3 client management (FE backend).

Lazily creates and caches a boto3 ``bedrock-runtime`` client configured from Settings
(region, timeouts, retries, pool size, optional AWS profile for local dev). Credentials
resolve via the standard boto3 chain (env / profile / instance role); the Vault-held
Bedrock key is wired at that layer, not here. **No model ids are hardcoded** — those come
from the Settings model registry (see ``app.services.model_router``).

Adopted (lean) from TMA ``app/utils/bedrock_clients.py``.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from app.config.settings import get_settings
from app.utils.logging import log


def _make_boto_config() -> Any:
    """Build a botocore Config from Settings (retries, timeouts, pool size)."""
    from botocore.config import Config

    s = get_settings()
    return Config(
        retries={"max_attempts": s.BEDROCK_MAX_ATTEMPTS, "mode": s.BEDROCK_RETRY_MODE},
        connect_timeout=s.BEDROCK_CONNECT_TIMEOUT,
        read_timeout=s.BEDROCK_READ_TIMEOUT,
        max_pool_connections=s.BEDROCK_MAX_POOL_CONNECTIONS,
    )


@lru_cache(maxsize=1)
def get_bedrock_runtime_client() -> Any:
    """Return a cached boto3 ``bedrock-runtime`` client, or ``None`` if creation fails.

    Never raises at import/call time — a ``None`` return lets the router surface a clear
    error at invoke time instead of crashing app startup.
    """
    import boto3

    s = get_settings()
    try:
        config = _make_boto_config()
        if s.LOCAL_RUN and s.AWS_PROFILE:
            log.info(f"[bedrock] Creating runtime client (local, profile={s.AWS_PROFILE})")
            session = boto3.Session(profile_name=s.AWS_PROFILE)
            client = session.client("bedrock-runtime", region_name=s.AWS_REGION, config=config)
        else:
            log.info(f"[bedrock] Creating runtime client (region={s.AWS_REGION})")
            client = boto3.client("bedrock-runtime", region_name=s.AWS_REGION, config=config)
        log.info("[bedrock] bedrock-runtime client created")
        return client
    except Exception as exc:
        log.error(f"[bedrock] Failed to create bedrock-runtime client: {exc}", exc_info=True)
        return None


def cleanup_bedrock_clients() -> None:
    """Reset the client cache (tests / shutdown)."""
    get_bedrock_runtime_client.cache_clear()
    log.info("[bedrock] Client cache cleared")
