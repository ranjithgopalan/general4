"""
Query-side PII masking (docs/20 §13 — TMA fold-in).

Build-time masks KB *content*; this masks the inbound *question* so structured PII never reaches the
model or the chat log. Light, deterministic regex (email / phone / Japanese My Number / card-like).
Names are intentionally NOT masked — too imprecise and they can be legitimate search keys, while the
KB itself is already PII-free. Config-gated (``QUERY_PII_MASK_ENABLED``).
"""

from __future__ import annotations

import re
from functools import lru_cache

from app.config.settings import Settings, get_settings

# Order matters: mask the more specific patterns first.
_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("<EMAIL>", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")),
    ("<MYNUMBER>", re.compile(r"\b\d{4}[- ]?\d{4}[- ]?\d{4}\b")),  # JP My Number = 12 digits
    ("<CARD>", re.compile(r"\b(?:\d[ -]?){13,16}\b")),
    ("<PHONE>", re.compile(r"\b0\d{1,4}[- ]?\d{1,4}[- ]?\d{3,4}\b")),
]


class PiiMasker:
    """Mask obvious structured PII in a query; returns (masked_text, labels_found)."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    def mask(self, text: str) -> tuple[str, list[str]]:
        if not self._settings.QUERY_PII_MASK_ENABLED or not text:
            return text, []
        found: list[str] = []
        out = text
        for label, pattern in _PATTERNS:
            if pattern.search(out):
                found.append(label)
                out = pattern.sub(label, out)
        return out, found


@lru_cache
def get_pii_masker() -> PiiMasker:
    """Process-wide singleton PiiMasker (cached)."""
    return PiiMasker()
