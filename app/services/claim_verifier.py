"""
Claim Verifier Service — extracts claims from artifacts and grounds each one
against KB chunks via an LLM call.

Writes results to fe_claim_evidence. Called as a fire-and-forget task from
_finalize() for document-stage artifacts. Non-fatal: any error is logged and
swallowed.

Limitation: LLM extraction accuracy is not guaranteed. A hallucinated match is
worse than no match — confidence < 0.5 rows are marked PARTIAL, not SUPPORTED.
"""

from __future__ import annotations

import json
import logging
from typing import Any

log = logging.getLogger(__name__)

_CLAIM_SYSTEM = (
    "You are an evidence auditor. Extract factual claims from the artifact text, "
    "then for each claim determine whether it is SUPPORTED, UNSUPPORTED, or PARTIAL "
    "based on the provided KB excerpts. "
    "Return ONLY valid JSON: "
    '{"claims": [{"claim": "...", "verdict": "SUPPORTED|UNSUPPORTED|PARTIAL", '
    '"kb_chunk_id": "...|null", "evidence_quote": "...|null", "confidence": 0.0-1.0}]}'
)

_MAX_ARTIFACT_CHARS = 6000
_MAX_KB_CHARS = 4000
_MAX_CLAIMS = 20


async def verify_claims(
    *,
    artifact_id: str,
    workspace_id: str,
    stage: str,
    artifact_text: str,
    kb_chunks: list[dict],
    pool: Any,
    schema: str,
) -> None:
    """Extract and ground claims from artifact_text against kb_chunks. Non-fatal."""
    try:
        claims = await _extract_and_ground(artifact_text, kb_chunks)
        await _persist_claims(claims, artifact_id=artifact_id,
                              workspace_id=workspace_id, stage=stage,
                              pool=pool, schema=schema)
    except Exception as exc:
        log.warning("[claim_verifier] failed for %s (non-fatal): %s", artifact_id, exc)


async def _extract_and_ground(
    artifact_text: str, kb_chunks: list[dict]
) -> list[dict]:
    from app.services.model_router import get_model_router  # noqa: PLC0415

    kb_text = _format_kb(kb_chunks)
    user_msg = (
        f"## Artifact\n{artifact_text[:_MAX_ARTIFACT_CHARS]}\n\n"
        f"## KB Excerpts\n{kb_text}"
    )
    llm = get_model_router().get_llm("evaluate")
    messages = [
        {"role": "system", "content": _CLAIM_SYSTEM},
        {"role": "user", "content": user_msg},
    ]
    response = await llm.ainvoke(messages)
    raw = response.content if hasattr(response, "content") else str(response)
    return _parse_claims(raw)


def _format_kb(kb_chunks: list[dict]) -> str:
    lines = []
    total = 0
    for chunk in kb_chunks:
        chunk_id = chunk.get("id", "")
        text = chunk.get("text", "")[:300]
        entry = f"[{chunk_id}]: {text}"
        total += len(entry)
        if total > _MAX_KB_CHARS:
            break
        lines.append(entry)
    return "\n".join(lines)


def _parse_claims(raw: str) -> list[dict]:
    try:
        start = raw.find("{")
        end = raw.rfind("}") + 1
        if start < 0 or end <= start:
            return []
        data = json.loads(raw[start:end])
        return data.get("claims", [])[:_MAX_CLAIMS]
    except (json.JSONDecodeError, KeyError):
        return []


async def _persist_claims(
    claims: list[dict],
    *,
    artifact_id: str,
    workspace_id: str,
    stage: str,
    pool: Any,
    schema: str,
) -> None:
    if not claims:
        return
    sql = f"""
        INSERT INTO {schema}.fe_claim_evidence
            (artifact_id, workspace_id, stage, claim_text,
             kb_chunk_id, verdict, evidence_quote, confidence)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
    """
    async with pool.connection() as conn:
        for c in claims:
            verdict = c.get("verdict", "UNSUPPORTED")
            if verdict not in ("SUPPORTED", "UNSUPPORTED", "PARTIAL"):
                verdict = "UNSUPPORTED"
            await conn.execute(sql, (
                artifact_id, workspace_id, stage,
                c.get("claim", "")[:1000],
                c.get("kb_chunk_id"),
                verdict,
                c.get("evidence_quote", "")[:500] if c.get("evidence_quote") else None,
                c.get("confidence"),
            ))
