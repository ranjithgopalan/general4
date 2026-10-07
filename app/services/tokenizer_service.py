"""
Tokenizer Service (Finding 5) — accurate token counting for context budget guards.

Uses tiktoken cl100k_base (compatible with Claude/Anthropic models) when available.
Falls back to a char-based estimate (len(text)//4) with source='estimated' so callers
can distinguish exact from approximate counts.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache

log = logging.getLogger(__name__)

_CL100K_BASE = "cl100k_base"
_CHAR_ESTIMATE_DIVISOR = 4


@dataclass
class TokenCount:
    count: int
    source: str  # "tiktoken" | "estimated"


@lru_cache(maxsize=4)
def _get_encoding(encoding_name: str):  # type: ignore[return]
    try:
        import tiktoken  # noqa: PLC0415
        return tiktoken.get_encoding(encoding_name)
    except Exception as exc:  # noqa: BLE001
        log.debug("[tokenizer] tiktoken unavailable (%s); using char estimate", exc)
        return None


class TokenizerService:
    """Count tokens for prompt budget guards using tiktoken when available."""

    def count_tokens(self, text: str, model: str = "") -> TokenCount:
        enc = _get_encoding(_CL100K_BASE)
        if enc is not None:
            try:
                return TokenCount(count=len(enc.encode(text)), source="tiktoken")
            except Exception as exc:  # noqa: BLE001
                log.debug("[tokenizer] encode failed (%s); falling back to estimate", exc)
        return TokenCount(count=max(1, len(text) // _CHAR_ESTIMATE_DIVISOR), source="estimated")

    def model_context_window(self, model_name: str) -> int:
        """Return the context window for the given model id (substring match)."""
        from app.config.settings import get_settings  # noqa: PLC0415
        windows = get_settings().MODEL_CONTEXT_WINDOWS
        key = next((k for k in windows if k in model_name.lower()), None)
        if key:
            return windows[key]
        return 200_000  # safe default for Claude models


@lru_cache(maxsize=1)
def get_tokenizer_service() -> TokenizerService:
    return TokenizerService()
