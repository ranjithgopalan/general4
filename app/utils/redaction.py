"""
RedactionFilter — production-active payload and log redaction.

Replaces the dev-only PIIMaskingFilter with a filter that is ALWAYS active in
all environments. Redaction patterns are configurable via the REDACTION_PATTERNS
environment variable (JSON list of regex strings) — no hard-coded credentials.

Redacted patterns (defaults, always active):
  - Authorization header values (Bearer tokens)
  - AWS secret/access key patterns
  - Passwords in key=value log strings
  - Connection string credentials
  - JSON fields: "password", "secret", "token", "api_key", "access_key", "private_key"

Security controls:
  - Never logs the raw patterns to avoid leaking what is being searched for
  - Fails OPEN — a redaction failure never blocks logging
  - Never stores the compiled patterns in a file (runtime only)
  - No credentials are hard-coded here

Usage:
    from app.utils.redaction import RedactionFilter, redact_dict
    logging.root.addFilter(RedactionFilter())

    # For telemetry metadata dicts before INSERT:
    safe_metadata = redact_dict(metadata)
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

_MASK = "[REDACTED]"

# Default patterns — applied to string representations of log messages and metadata values.
# These match common credential patterns WITHOUT listing actual credential values.
_DEFAULT_PATTERNS: list[str] = [
    r"(?i)(bearer\s+)[A-Za-z0-9\-_=\.]+",           # Authorization: Bearer <token>
    r"(?i)(password\s*[:=]\s*)[^\s,;\"\'}]+",         # password=xxx or "password": "xxx"
    r"(?i)(secret\s*[:=]\s*)[^\s,;\"\'}]+",
    r"(?i)(api[_-]?key\s*[:=]\s*)[^\s,;\"\'}]+",
    r"(?i)(access[_-]?key\s*[:=]\s*)[^\s,;\"\'}]+",
    r"(?i)(private[_-]?key\s*[:=]\s*)[^\s,;\"\'}]+",
    r"(?i)(connection[_-]?string\s*[:=]\s*)[^\s,;\"\'}]+",
    r"AKIA[0-9A-Z]{16}",                              # AWS access key ID pattern
    r"(?i)(token\s*[:=]\s*)[^\s,;\"\'}]+",
]

_JSON_SENSITIVE_KEYS = frozenset({
    "password", "secret", "token", "api_key", "access_key", "private_key",
    "connection_string", "client_secret", "jwt_secret", "db_password",
    "authorization",
})

_compiled_patterns: list[re.Pattern] | None = None


def _get_patterns() -> list[re.Pattern]:
    global _compiled_patterns
    if _compiled_patterns is not None:
        return _compiled_patterns

    patterns = list(_DEFAULT_PATTERNS)
    env_patterns = os.environ.get("REDACTION_PATTERNS", "")
    if env_patterns:
        try:
            extra = json.loads(env_patterns)
            if isinstance(extra, list):
                patterns.extend(str(p) for p in extra)
        except Exception:
            pass  # Never fail on bad env config

    try:
        _compiled_patterns = [re.compile(p) for p in patterns]
    except Exception:
        _compiled_patterns = []
    return _compiled_patterns


def redact_string(text: str) -> str:
    """Apply all redaction patterns to a string. Returns the original if redaction fails."""
    try:
        for pattern in _get_patterns():
            # If pattern has a capture group (the sensitive prefix), replace only group 1 content
            if pattern.groups:
                text = pattern.sub(lambda m: m.group(0)[:m.start(1) - m.start(0) + len(m.group(1))] + _MASK, text)
            else:
                text = pattern.sub(_MASK, text)
    except Exception:
        pass  # Fail open — never block on redaction error
    return text


def redact_dict(data: dict[str, Any], *, depth: int = 0) -> dict[str, Any]:
    """Return a copy of data with sensitive keys and values redacted.

    Operates recursively up to depth 5. Sensitive key detection uses
    _JSON_SENSITIVE_KEYS (case-insensitive prefix match). String values
    in non-sensitive keys are also scanned with regex patterns.
    Never raises — returns the original dict on any error.
    """
    if depth > 5:
        return data
    try:
        result: dict[str, Any] = {}
        for key, value in data.items():
            lower_key = str(key).lower()
            if any(lower_key == sk or lower_key.endswith(f"_{sk}") for sk in _JSON_SENSITIVE_KEYS):
                result[key] = _MASK
            elif isinstance(value, dict):
                result[key] = redact_dict(value, depth=depth + 1)
            elif isinstance(value, str):
                result[key] = redact_string(value)
            elif isinstance(value, list):
                result[key] = [
                    redact_dict(v, depth=depth + 1) if isinstance(v, dict) else
                    redact_string(v) if isinstance(v, str) else v
                    for v in value
                ]
            else:
                result[key] = value
        return result
    except Exception:
        return data  # Fail open


class RedactionFilter(logging.Filter):
    """Production-active log filter that redacts credentials and sensitive values.

    Unlike PIIMaskingFilter (dev-only), this filter is active in ALL environments.
    Attach to the root logger at startup so every log record is scanned.
    Fails open — never blocks logging on redaction error.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            if record.msg and isinstance(record.msg, str):
                record.msg = redact_string(record.msg)
            if record.args:
                if isinstance(record.args, dict):
                    record.args = {k: redact_string(str(v)) if isinstance(v, str) else v
                                   for k, v in record.args.items()}
                elif isinstance(record.args, tuple):
                    record.args = tuple(
                        redact_string(str(a)) if isinstance(a, str) else a
                        for a in record.args
                    )
        except Exception:
            pass  # Fail open
        return True
