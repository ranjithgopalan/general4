"""
Cross-encoder reranking (docs/20 §8) — Bedrock Rerank, config-gated (OFF by default).

Reorders fused candidates by query↔passage relevance for higher precision at scale. Kept off until
the golden-set eval shows a precision gap (docs/20 §20). The model id resolves from the registry
(task ``rerank``); a disabled flag, an unresolved model, or any Bedrock error returns the input order
unchanged — reranking never fails the query.
"""

from __future__ import annotations

import asyncio
from functools import lru_cache
from typing import Any

from app.config.settings import Settings, get_settings
from app.utils.logging import log


class Reranker:
    """Optional relevance reranker over ``(id, text)`` candidates."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._client: Any = None

    @property
    def enabled(self) -> bool:
        return self._settings.RERANK_ENABLED

    async def rerank(self, query: str, items: list[tuple[str, str]]) -> list[str]:
        """Return candidate ids in reranked order; input order on disabled/unavailable/error."""
        ids = [cid for cid, _ in items]
        if not self.enabled or len(items) < 2:
            return ids
        model_id = self._settings.model_id_for("rerank")
        if not model_id:
            log.info("[reranker] enabled but no 'rerank' model configured; keeping fusion order")
            return ids
        try:
            return await asyncio.to_thread(self._rerank_sync, model_id, query, items)
        except Exception as exc:  # noqa: BLE001 — never fail the query on a rerank error
            log.warning(f"[reranker] rerank failed ({exc}); keeping fusion order")
            return ids

    def _rerank_sync(self, model_id: str, query: str, items: list[tuple[str, str]]) -> list[str]:
        ids = [cid for cid, _ in items]
        client = self._agent_runtime()
        if client is None:
            return ids
        resp = client.rerank(
            queries=[{"type": "TEXT", "textQuery": {"text": query}}],
            sources=[
                {"type": "INLINE", "inlineDocumentSource": {"type": "TEXT", "textDocument": {"text": text}}}
                for _, text in items
            ],
            rerankingConfiguration={
                "type": "BEDROCK_RERANKING_MODEL",
                "bedrockRerankingConfiguration": {
                    "modelConfiguration": {"modelArn": model_id},
                    "numberOfResults": self._settings.RERANK_TOP_N,
                },
            },
        )
        order = [r["index"] for r in resp.get("results", [])]
        return [ids[i] for i in order if 0 <= i < len(ids)] or ids

    def _agent_runtime(self) -> Any:
        if self._client is None:
            try:
                import boto3

                self._client = boto3.client("bedrock-agent-runtime", region_name=self._settings.AWS_REGION)
            except Exception as exc:  # noqa: BLE001
                log.warning(f"[reranker] no bedrock-agent-runtime client: {exc}")
                self._client = False
        return self._client or None


@lru_cache
def get_reranker() -> Reranker:
    """Process-wide singleton Reranker (cached)."""
    return Reranker()
