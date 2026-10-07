"""
Input guardrails (docs/20 §13) — deterministic screen before retrieval/LLM.

Blocks prompt-injection / jailbreak / system-prompt probing / bulk exfiltration / PII-fishing with a
refuse-and-redirect. Pure regex (no LLM) so it is fast, testable, and cannot itself be prompted; the
grounding gate is the second line for anything that slips through. Config-gated (``GUARDRAILS_ENABLED``).
"""

from __future__ import annotations

import re
from functools import lru_cache

from app.config.settings import Settings, get_settings
from app.models.chat import GuardrailVerdict

# (category, pattern) — first match wins.
_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("injection", re.compile(r"ignore\s+(all\s+|your\s+|previous\s+|prior\s+)?(instructions|rules|prompt)", re.I)),
    ("injection", re.compile(r"(system\s+prompt|your\s+instructions|reveal\s+your|show\s+me\s+your\s+prompt)", re.I)),
    ("jailbreak", re.compile(r"\b(jailbreak|developer\s+mode|do\s+anything\s+now|\bDAN\b)", re.I)),
    ("exfiltration", re.compile(r"(dump|export|list)\s+(all|every|the\s+entire)\s+(cards?|records?|rows?|graph|kb|database)", re.I)),
    ("pii_fishing", re.compile(r"(my\s*number|個人番号|マイナンバー|credit\s*card|social\s*security|passport\s*number)", re.I)),
]

_REFUSALS = {
    "injection": "I can only answer grounded questions about the knowledge base; I can't change my instructions.",
    "jailbreak": "I can only answer grounded questions about the knowledge base.",
    "exfiltration": "I can't export the knowledge base in bulk; ask a specific question and I'll answer with citations.",
    "pii_fishing": "I can't provide personal data — the knowledge base holds design knowledge, not personal records.",
}


class Guardrails:
    """Deterministic input screen; returns a refuse-and-redirect verdict on a hit."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    def screen(self, text: str) -> GuardrailVerdict:
        if not self._settings.GUARDRAILS_ENABLED:
            return GuardrailVerdict(allowed=True)
        for category, pattern in _RULES:
            if pattern.search(text or ""):
                return GuardrailVerdict(allowed=False, category=category, reason=_REFUSALS[category])
        return GuardrailVerdict(allowed=True)


@lru_cache
def get_guardrails() -> Guardrails:
    """Process-wide singleton Guardrails (cached)."""
    return Guardrails()
