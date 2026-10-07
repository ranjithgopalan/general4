"""Evaluation and observability endpoints.

/evaluation/readiness/{workspace_id}/{stage}
    — Returns the readiness gate verdict for the workspace before advancing.

/evaluation/scorecard/{workspace_id}/{stage}
    — Returns aggregated evaluation metrics for a stage in a workspace.

/evaluation/context/{workspace_id}/{stage}
    — Returns what chunks were sent vs dropped for the most recent stage run.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from app.agentic_platform.fe_core.config import get_settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/evaluation", tags=["evaluation"])


@router.get("/readiness/{workspace_id}/{stage}")
async def get_readiness(workspace_id: str, stage: str) -> dict:
    """Evaluate whether a workspace is ready to advance past the given stage."""
    try:
        from app.dao.postgres import get_pool  # noqa: PLC0415
        from app.config import get_settings as _gs  # noqa: PLC0415
        from app.services.readiness_gate import evaluate_readiness  # noqa: PLC0415

        pool = get_pool()
        schema = _gs().PG_SCHEMA
        result = await evaluate_readiness(
            workspace_id=workspace_id, stage=stage, pool=pool, schema=schema,
        )
        return {
            "workspace_id": workspace_id,
            "stage": stage,
            "verdict": result.verdict,
            "reasons": result.reasons,
            "metrics": result.metrics,
        }
    except Exception as exc:
        logger.warning("Readiness gate failed for %s/%s: %s", workspace_id, stage, exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/scorecard/{workspace_id}/{stage}")
async def get_scorecard(workspace_id: str, stage: str) -> dict:
    """Return aggregated evaluation scores for a stage run."""
    try:
        from app.dao.postgres import get_pool  # noqa: PLC0415
        from app.config import get_settings as _gs  # noqa: PLC0415

        pool = get_pool()
        schema = _gs().PG_SCHEMA
        async with pool.connection() as conn:
            rows = await conn.fetch(
                f"""
                SELECT dimension, AVG(score) AS avg_score,
                       BOOL_AND(passed_threshold) AS all_passed,
                       COUNT(*) AS eval_count
                FROM {schema}.fe_evaluations e
                WHERE e.workspace_id = %s
                  AND EXISTS (
                      SELECT 1 FROM {schema}.fe_workspace_artifacts a
                      WHERE a.id = e.artifact_id AND a.workspace_id = %s
                  )
                GROUP BY dimension
                ORDER BY dimension
                """,
                workspace_id, workspace_id,
            )
        return {
            "workspace_id": workspace_id,
            "stage": stage,
            "dimensions": [
                {
                    "dimension": r["dimension"],
                    "avg_score": round(r["avg_score"], 3) if r["avg_score"] is not None else None,
                    "all_passed": r["all_passed"],
                    "eval_count": r["eval_count"],
                }
                for r in rows
            ],
        }
    except Exception as exc:
        logger.warning("Scorecard failed for %s/%s: %s", workspace_id, stage, exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/context/{workspace_id}/{stage}")
async def get_context_provenance(workspace_id: str, stage: str) -> dict:
    """Return what chunks were sent vs dropped during context assembly."""
    try:
        from app.dao.postgres import get_pool  # noqa: PLC0415
        from app.config import get_settings as _gs  # noqa: PLC0415

        pool = get_pool()
        schema = _gs().PG_SCHEMA
        async with pool.connection() as conn:
            rows = await conn.fetch(
                f"""
                SELECT chunk_id, lane, rrf_score, rank_position, was_sent, cap_reason
                FROM {schema}.fe_context_provenance
                WHERE workspace_id = %s AND stage = %s
                ORDER BY was_sent DESC, rrf_score DESC NULLS LAST
                LIMIT 100
                """,
                workspace_id, stage,
            )
        sent = [r for r in rows if r["was_sent"]]
        dropped = [r for r in rows if not r["was_sent"]]
        return {
            "workspace_id": workspace_id,
            "stage": stage,
            "sent_count": len(sent),
            "dropped_count": len(dropped),
            "sent": [dict(r) for r in sent],
            "dropped": [dict(r) for r in dropped],
        }
    except Exception as exc:
        logger.warning("Context provenance failed for %s/%s: %s", workspace_id, stage, exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/claims/{artifact_id}")
async def get_claim_evidence(artifact_id: str) -> dict:
    """Return per-claim grounding verdicts for an artifact."""
    try:
        from app.dao.postgres import get_pool  # noqa: PLC0415
        from app.config import get_settings as _gs  # noqa: PLC0415

        pool = get_pool()
        schema = _gs().PG_SCHEMA
        async with pool.connection() as conn:
            rows = await conn.fetch(
                f"""
                SELECT claim_text, kb_chunk_id, verdict, evidence_quote, confidence
                FROM {schema}.fe_claim_evidence
                WHERE artifact_id = %s
                ORDER BY evaluated_at DESC
                """,
                artifact_id,
            )
        total = len(rows)
        supported = sum(1 for r in rows if r["verdict"] == "SUPPORTED")
        return {
            "artifact_id": artifact_id,
            "total_claims": total,
            "supported": supported,
            "unsupported": sum(1 for r in rows if r["verdict"] == "UNSUPPORTED"),
            "partial": sum(1 for r in rows if r["verdict"] == "PARTIAL"),
            "groundedness_rate": round(supported / total, 3) if total else None,
            "claims": [dict(r) for r in rows],
        }
    except Exception as exc:
        logger.warning("Claim evidence failed for %s: %s", artifact_id, exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
