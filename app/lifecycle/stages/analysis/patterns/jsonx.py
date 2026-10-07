"""Robust LLM-JSON extraction (deterministic, no LLM).

Adopted from xpf `_generate_with_llm`: strip markdown fences, isolate the outer JSON object, repair
common defects (trailing commas), and parse. Returns ``None`` on failure so callers fall back to the
deterministic path instead of raising.
"""

from __future__ import annotations

import json
import re
from typing import Any

_FENCE = re.compile(r"^```(?:json)?|```$", re.MULTILINE)
_TRAILING_COMMA = re.compile(r",(\s*[}\]])")


def extract_json(text: str | None) -> dict[str, Any] | None:
    """Best-effort parse of a JSON object from an LLM response; ``None`` if unrecoverable."""
    if not text:
        return None
    cleaned = _FENCE.sub("", text).strip()
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    candidate = cleaned[start : end + 1]
    for attempt in (candidate, _TRAILING_COMMA.sub(r"\1", candidate)):
        try:
            parsed = json.loads(attempt)
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            continue
    return None
