"""
Query embedder — Titan text embeddings via Bedrock for the dense retrieval lane (docs/20 §8).

The model id resolves from the config registry (task ``embed``) — never hardcoded. The output
dimension is config-driven (``EMBED_DIM``) and **must match** how ``kb_chunks`` were embedded by
the indexer, or cosine distances are meaningless. Returns ``None`` on any failure (no creds, model
unresolved, Bedrock error) so retrieval degrades to the BM25 + graph lanes instead of failing the
whole query.
"""

from __future__ import annotations

import asyncio
import json

from app.config.settings import get_settings
from app.utils.bedrock_clients import get_bedrock_runtime_client
from app.utils.logging import log


def _invoke(model_id: str, body: dict) -> list[float] | None:
    """Synchronous Bedrock ``invoke_model`` (Titan embeddings). Runs off-thread via ``embed_query``."""
    client = get_bedrock_runtime_client()
    if client is None:
        return None
    resp = client.invoke_model(modelId=model_id, body=json.dumps(body))
    payload = json.loads(resp["body"].read())
    vec = payload.get("embedding")
    return [float(x) for x in vec] if vec else None


async def embed_query(text: str) -> list[float] | None:
    """Embed a query string; ``None`` if unavailable (the dense lane is then skipped)."""
    s = get_settings()
    model_id = s.model_id_for("embed")
    if not model_id or not (text and text.strip()):
        return None
    # Titan Text Embeddings InvokeModel body is ``{"inputText": ...}`` — MUST be identical to how the
    # kb-indexer embedded the chunks (it sends exactly this), or the query vector lands in a different
    # space and cosine is meaningless. The previous chat-style ``{"messages":[…],"inferenceConfig":…}``
    # was wrong for the embeddings model (Bedrock rejected it: "3 schema violations") → dense lane silently
    # skipped on every query. Titan v2 defaults to EMBED_DIM=1024 normalized, matching the chunk embeds.
    body: dict = {"inputText": text}
    try:
        return await asyncio.to_thread(_invoke, model_id, body)
    except Exception as exc:  # noqa: BLE001 — degrade to the non-dense lanes, never fail the query
        log.warning(f"[embedder] query embed failed ({exc}); dense lane skipped")
        return None
