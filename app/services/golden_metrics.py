"""
Golden Dataset Metrics (Finding 3) — compute precision, recall, and coverage
for a retrieval run against the seeded AUTH golden dataset.

Precision = |retrieved ∩ expected| / |retrieved|
Recall    = |retrieved ∩ expected| / |expected|
Coverage  = fraction of golden examples where all expected_chunk_ids were retrieved

Results are persisted to fe_experiment_run for trend analysis.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger(__name__)


@dataclass
class RetrievalMetrics:
    precision: float | None
    recall: float | None
    coverage: float | None
    retrieved_count: int
    expected_count: int
    intersection_count: int
    golden_id: str
    run_id: str


async def compute_retrieval_metrics(
    *,
    run_id: str,
    golden_dataset_name: str,
    retrieved_chunk_ids: list[str],
    pool: Any,
    schema: str,
) -> RetrievalMetrics:
    """Compare retrieved chunks against the golden dataset and persist to fe_experiment_run."""
    golden_records = await _load_golden(golden_dataset_name, pool, schema)
    metrics = _compute(retrieved_chunk_ids, golden_records, run_id, golden_dataset_name)
    await _persist(metrics, pool, schema)
    return metrics


async def _load_golden(dataset_name: str, pool: Any, schema: str) -> list[dict]:
    sql = f"""
        SELECT dataset_id, eval_criteria
        FROM {schema}.fe_golden_dataset
        WHERE dataset_id LIKE %s
    """
    try:
        async with pool.connection() as conn:
            from psycopg.rows import dict_row  # noqa: PLC0415
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(sql, (f"{dataset_name}%",))
                return await cur.fetchall()
    except Exception as exc:  # noqa: BLE001
        log.warning("[golden_metrics] load failed: %s", exc)
        return []


def _compute(
    retrieved: list[str],
    golden_records: list[dict],
    run_id: str,
    golden_id: str,
) -> RetrievalMetrics:
    retrieved_set = set(retrieved)
    all_expected: set[str] = set()
    covered = 0
    total = 0
    for rec in golden_records:
        criteria = rec.get("eval_criteria") or {}
        if isinstance(criteria, str):
            try:
                criteria = json.loads(criteria)
            except Exception:  # noqa: BLE001
                criteria = {}
        expected = set(criteria.get("expected_chunk_ids") or [])
        if not expected:
            continue
        all_expected.update(expected)
        total += 1
        if expected.issubset(retrieved_set):
            covered += 1

    intersection = retrieved_set & all_expected
    precision = len(intersection) / len(retrieved_set) if retrieved_set else None
    recall = len(intersection) / len(all_expected) if all_expected else None
    coverage = covered / total if total > 0 else None

    return RetrievalMetrics(
        precision=precision,
        recall=recall,
        coverage=coverage,
        retrieved_count=len(retrieved_set),
        expected_count=len(all_expected),
        intersection_count=len(intersection),
        golden_id=golden_id,
        run_id=run_id,
    )


async def _persist(metrics: RetrievalMetrics, pool: Any, schema: str) -> None:
    sql = f"""
        INSERT INTO {schema}.fe_experiment_run
            (dataset_id, stage, input_hash, eval_criteria, notes, created_by)
        VALUES (%s, %s, %s, %s::jsonb, %s, %s)
        ON CONFLICT DO NOTHING
    """
    payload = json.dumps({
        "precision": metrics.precision,
        "recall": metrics.recall,
        "coverage": metrics.coverage,
        "retrieved_count": metrics.retrieved_count,
        "expected_count": metrics.expected_count,
        "intersection_count": metrics.intersection_count,
        "run_id": metrics.run_id,
        "evaluated_at": datetime.now(tz=timezone.utc).isoformat(),
    })
    try:
        async with pool.connection() as conn:
            await conn.execute(sql, (
                f"{metrics.golden_id}:{metrics.run_id}",
                "retrieval",
                metrics.run_id,
                payload,
                f"Auto-computed retrieval metrics for {metrics.golden_id}",
                "golden_metrics",
            ))
    except Exception as exc:  # noqa: BLE001
        log.warning("[golden_metrics] persist failed (non-fatal): %s", exc)
