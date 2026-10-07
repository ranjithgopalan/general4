"""
AI Call Recorder — persists one row to fe_ai_call per AI invocation (Finding 1).

Never persists secrets, API keys, OAuth tokens, authorization headers, or credentials.
Prompt text is stored only as a SHA-256 hash (prompt_hash); the full text lives in
fe_prompt_snapshot and is linked by that hash.

Idempotency: duplicate inserts are silently ignored via ON CONFLICT on idempotency_key.
Non-fatal: any DB error is logged at DEBUG and swallowed so AI calls are never blocked.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger(__name__)

_INSERT_SQL = """
    INSERT INTO {schema}.fe_ai_call (
        ai_call_id, idempotency_key,
        execution_id, run_id, stage_id, stage_name, agent_id,
        model_provider, model_name, model_version,
        request_ts, response_ts, latency_ms,
        prompt_hash, prompt_version,
        input_tokens, output_tokens, cached_input_tokens, total_tokens,
        estimated_cost_usd, currency,
        temperature, max_tokens,
        request_status, error_type, error_message,
        retry_count, cache_hit, trace_id, parent_call_id
    ) VALUES (
        %s, %s,
        %s, %s, %s, %s, %s,
        %s, %s, %s,
        %s, %s, %s,
        %s, %s,
        %s, %s, %s, %s,
        %s, %s,
        %s, %s,
        %s, %s, %s,
        %s, %s, %s, %s
    )
    ON CONFLICT (idempotency_key) DO NOTHING
"""

_COST_PER_1K: dict[str, tuple[float, float]] = {
    "claude-3-5-sonnet": (0.003, 0.015),
    "claude-3-5-haiku": (0.0008, 0.004),
    "claude-3-opus": (0.015, 0.075),
    "claude-sonnet-4": (0.003, 0.015),
    "claude-haiku-4-5": (0.0008, 0.004),
}


def _estimate_cost(model_name: str, input_tokens: int, output_tokens: int) -> float | None:
    key = next((k for k in _COST_PER_1K if k in model_name.lower()), None)
    if key is None:
        return None
    in_rate, out_rate = _COST_PER_1K[key]
    return round(input_tokens / 1000 * in_rate + output_tokens / 1000 * out_rate, 8)


def prompt_hash(prompt_text: str) -> str:
    return hashlib.sha256(prompt_text.encode("utf-8", errors="replace")).hexdigest()


@dataclass
class AICallRecord:
    model_provider: str
    model_name: str
    request_ts: datetime
    response_ts: datetime | None = None
    latency_ms: int | None = None
    prompt_hash: str | None = None
    prompt_version: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_input_tokens: int | None = None
    total_tokens: int | None = None
    estimated_cost_usd: float | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    request_status: str = "success"
    error_type: str | None = None
    error_message: str | None = None
    retry_count: int = 0
    cache_hit: bool | None = None
    # linkage
    execution_id: str | None = None
    run_id: str | None = None
    stage_id: str | None = None
    stage_name: str | None = None
    agent_id: str | None = None
    trace_id: str | None = None
    parent_call_id: str | None = None
    model_version: str | None = None
    # computed on persist
    ai_call_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    def idempotency_key(self) -> str:
        parts = "|".join([
            self.run_id or "",
            self.stage_id or "",
            str(self.request_ts.isoformat()),
            self.model_name,
        ])
        return hashlib.sha256(parts.encode()).hexdigest()

    def _auto_fill(self) -> None:
        if self.latency_ms is None and self.response_ts is not None:
            delta = self.response_ts - self.request_ts
            self.latency_ms = int(delta.total_seconds() * 1000)
        if self.total_tokens is None:
            in_t = self.input_tokens or 0
            out_t = self.output_tokens or 0
            cached = self.cached_input_tokens or 0
            self.total_tokens = in_t + out_t + cached or None
        if self.estimated_cost_usd is None and self.input_tokens and self.output_tokens:
            self.estimated_cost_usd = _estimate_cost(
                self.model_name, self.input_tokens, self.output_tokens
            )


async def record(rec: AICallRecord, *, pool: Any = None, schema: str | None = None) -> None:
    """Persist an AI call record. Non-fatal — logs warning and returns on any error."""
    rec._auto_fill()
    try:
        if pool is None:
            from app.dao.postgres import get_pool  # noqa: PLC0415
            pool = get_pool()
        if schema is None:
            from app.config import get_settings  # noqa: PLC0415
            schema = get_settings().PG_SCHEMA
        if pool is None:
            return
        sql = _INSERT_SQL.format(schema=schema)
        async with pool.connection() as conn:
            await conn.execute(sql, (
                rec.ai_call_id,
                rec.idempotency_key(),
                rec.execution_id, rec.run_id, rec.stage_id, rec.stage_name, rec.agent_id,
                rec.model_provider, rec.model_name, rec.model_version,
                rec.request_ts, rec.response_ts, rec.latency_ms,
                rec.prompt_hash, rec.prompt_version,
                rec.input_tokens, rec.output_tokens, rec.cached_input_tokens, rec.total_tokens,
                rec.estimated_cost_usd, "USD",
                rec.temperature, rec.max_tokens,
                rec.request_status, rec.error_type, rec.error_message,
                rec.retry_count, rec.cache_hit, rec.trace_id, rec.parent_call_id,
            ))
    except Exception as exc:  # noqa: BLE001
        log.debug("[ai_call_recorder] insert failed (non-fatal): %s", exc)


def now_utc() -> datetime:
    return datetime.now(tz=timezone.utc)
