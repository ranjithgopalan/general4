"""Chat-model provider (Bedrock) — the specialist LLM seam.

Config-driven per the model registry/routing (``settings.MODEL_*``). In dev/test — no model configured,
no Bedrock creds/region, or ``langchain-aws`` absent — it returns ``None``, so specialists fall back to
the deterministic grounded path. No model ids are hardcoded; they resolve from the registry.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from app.config.settings import get_settings
from app.utils.logging import log


@lru_cache(maxsize=None)
def get_chat_model(task: str = "synthesize") -> Any | None:
    """Return a Bedrock chat model for a task (via model routing), or ``None`` when unavailable.

    Cached per task: building ``ChatBedrockConverse`` resolves AWS credentials from the profile (slow,
    ~100-700ms), and the client is reusable + thread-safe — so it must be a per-task singleton, not
    rebuilt on every request (the stage-service factories construct several models transitively)."""
    settings = get_settings()
    model_id = settings.model_id_for(task)
    if not model_id:
        return None
    try:
        from langchain_aws import ChatBedrockConverse

        return ChatBedrockConverse(
            model=model_id,
            region_name=settings.AWS_REGION,
            credentials_profile_name=settings.AWS_PROFILE or None,  # local: named profile; prod: task IAM role
            temperature=settings.BEDROCK_LLM_DEFAULT_TEMPERATURE,
            max_tokens=settings.BEDROCK_LLM_DEFAULT_MAX_TOKENS,
        )
    except Exception as exc:  # noqa: BLE001 — langchain-aws absent / Bedrock unreachable -> deterministic path
        log.info(f"[chat-model] no Bedrock model available ({exc}); specialists use the deterministic path.")
        return None
