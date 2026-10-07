"""Postgres sink for fe_telemetry.

Writes every TelemetryEvent to {PG_SCHEMA}.fe_telemetry using fire-and-forget
asyncio tasks so the calling request path is never blocked.  All errors are
swallowed with a warning — telemetry must never break a production request.

Register at startup (after the asyncpg pool is open):

    from app.services.telemetry import register_sink
    from app.services.telemetry_pg_sink import PostgresTelemetrySink
    register_sink(PostgresTelemetrySink(pool))
"""

from __future__ import annotations

import asyncio
import logging

log = logging.getLogger(__name__)


def _insert_sql() -> str:
    from app.config import get_settings
    schema = get_settings().PG_SCHEMA
    return f"""
        INSERT INTO {schema}.fe_telemetry
            (event_type, latency_ms, tokens_in, tokens_out, model_tier, kb_version, metadata,
             correlation_id, workflow_run_id, agent_run_id, stage, workspace_id,
             module_id, feature_id, rule_id, artifact_id,
             prompt_version, agent_version, validation_status, evaluation_status,
             human_approval_status, retry_count)
        VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """


class PostgresTelemetrySink:
    """Callable sink that writes TelemetryEvents to fe_telemetry via asyncpg."""

    def __init__(self, pool) -> None:
        self._pool = pool

    def __call__(self, event) -> None:
        """Enqueue a fire-and-forget write; never raises."""
        try:
            asyncio.create_task(self._insert(event))
        except RuntimeError:
            # No running event loop (e.g. called from a sync test context). Skip silently.
            pass

    async def _insert(self, event) -> None:
        import json

        payload = event.payload or {}
        # Embed trace IDs into metadata so every row is queryable by workflow/agent.
        metadata = {
            **payload,
            "correlation_id": event.correlation_id,
            "agent_run_id": event.agent_run_id,
            "workflow_run_id": event.workflow_run_id,
            "tool_call_id": event.tool_call_id,
            "session_id": event.session_id,
            "userid": event.userid,
            "persona": event.persona,
        }
        # rule_ids is a list — store only the first for the indexed column; full list lives in metadata.
        rule_ids = payload.get("rule_ids") or []
        rule_id_primary = rule_ids[0] if rule_ids else payload.get("rule_id") or None

        try:
            async with self._pool.connection() as conn:
                await conn.execute(
                    _insert_sql(),
                    (
                        event.event_type,
                        int(payload.get("latency_ms") or 0) or None,
                        payload.get("tokens_in") or payload.get("tokens_total"),
                        payload.get("tokens_out"),
                        payload.get("model_tier") or payload.get("model_id"),
                        payload.get("kb_version"),
                        json.dumps(metadata),
                        # Structured trace columns (migration 0010) — promoted from jsonb for indexed queries.
                        event.correlation_id or None,
                        event.workflow_run_id or None,
                        event.agent_run_id or None,
                        payload.get("stage") or None,
                        payload.get("workspace_id") or None,
                        # Traceability columns (migration 0026)
                        payload.get("module_id") or None,
                        payload.get("feature_id") or None,
                        rule_id_primary,
                        payload.get("artifact_id") or None,
                        payload.get("prompt_version") or None,
                        payload.get("agent_version") or None,
                        payload.get("validation_status") or None,
                        payload.get("evaluation_status") or None,
                        payload.get("human_approval_status") or None,
                        payload.get("retry_count") or 0,
                    ),
                )
        except Exception as exc:
            log.warning("[telemetry_pg_sink] write failed (non-fatal): %s", exc)

    # ── Traceability helpers ────────────────────────────────────────────────

    def record_traceability_link(
        self,
        *,
        workspace_id: str,
        from_artifact_id: str,
        to_artifact_id: str,
        link_type: str,
        stage: str,
    ) -> None:
        """Fire-and-forget telemetry for a traceability link creation.

        Emits event_type='traceability_link_created' to fe_telemetry.
        workspace_id and stage travel inside payload (TelemetryEvent has no
        top-level workspace_id/stage fields; _insert promotes them from payload).
        Never raises.
        """
        import time  # noqa: PLC0415
        from app.services.telemetry import TelemetryEvent  # noqa: PLC0415 — deferred to avoid circular import

        event = TelemetryEvent(
            event_type="traceability_link_created",
            payload={
                "workspace_id":     workspace_id,
                "from_artifact_id": from_artifact_id,
                "to_artifact_id":   to_artifact_id,
                "link_type":        link_type,
                "stage":            stage,
            },
            ts=time.time(),
        )
        self(event)  # → __call__ → asyncio.create_task(_insert(event))

    def record_traceability_view(self, *, workspace_id: str) -> None:
        """Fire-and-forget telemetry when the traceability page is loaded."""
        import time  # noqa: PLC0415
        from app.services.telemetry import TelemetryEvent  # noqa: PLC0415

        event = TelemetryEvent(
            event_type="traceability_page_viewed",
            payload={"workspace_id": workspace_id},
            ts=time.time(),
        )
        self(event)
