"""Agent evaluation framework — per-run signal scoring and async DB-backed evaluation.

Two complementary evaluation systems in one module:

1. EvaluationBuilder / EvaluationRecord (signal-based, in-process)
   Captures objective, deterministic signals per agent run (compilation, tests, traceability,
   grounding score, acceptance coverage). Results are built synchronously via a fluent builder
   and logged immediately. No DB or LLM required.
   Pattern: Tikal 4-pillar converge gate, dsh-a event-log, msankarm metrics dict.

2. EvaluationService (async, DB-backed, LLM-as-judge)
   Scores agent output artifacts on correctness, completeness, and hallucination risk.
   Writes results to form_rationalization_anh.fe_evaluations (migration 0013) and emits
   EVALUATION_STARTED / EVALUATION_COMPLETED telemetry events.
   Three evaluator types: 'threshold' (nfr-thresholds.json), 'llm' (Claude via Bedrock),
   'human' (scores submitted via /observability/approvals API).
   Security: agent outputs must have PII stripped by RedactionFilter before reaching here.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from app.services.telemetry import EventType, get_telemetry_service
from app.utils.logging import log


# ── Part 1: In-process signal-based evaluation ────────────────────────────────


class EvalSignal(str, Enum):
    """Objective evaluation signals captured per agent run."""
    COMPILATION = "compilation"
    TESTS_PASSED = "tests_passed"
    ACCEPTANCE_COVERAGE = "acceptance_coverage"
    TRACEABILITY = "traceability"
    SCHEMA_VALID = "schema_valid"
    API_CONTRACT = "api_contract"
    ASSUMPTION_RATIO = "assumption_ratio"
    GROUNDING_SCORE = "grounding_score"
    EXECUTION_TIME_MS = "execution_time_ms"
    TOKEN_INPUT = "token_input"
    TOKEN_OUTPUT = "token_output"
    HUMAN_INTERVENTIONS = "human_interventions"


# Minimum thresholds for a PASS verdict (None = record-only, not gated).
_SIGNAL_THRESHOLDS: dict[str, float | None] = {
    EvalSignal.COMPILATION: 1.0,
    EvalSignal.TESTS_PASSED: 1.0,
    EvalSignal.ACCEPTANCE_COVERAGE: 0.70,
    EvalSignal.TRACEABILITY: 1.0,
    EvalSignal.SCHEMA_VALID: 1.0,
    EvalSignal.API_CONTRACT: None,
    EvalSignal.ASSUMPTION_RATIO: None,
    EvalSignal.GROUNDING_SCORE: 0.80,
    EvalSignal.EXECUTION_TIME_MS: None,
    EvalSignal.TOKEN_INPUT: None,
    EvalSignal.TOKEN_OUTPUT: None,
    EvalSignal.HUMAN_INTERVENTIONS: None,
}


@dataclass
class SignalResult:
    signal: str
    value: float | None
    threshold: float | None
    passed: bool | None    # None = record-only
    detail: str = ""


@dataclass
class EvaluationRecord:
    """Immutable evaluation record for one agent run."""

    agent_id: str
    agent_version: str
    task_id: str
    stage: str
    workspace_id: str
    model: str
    model_version: str
    prompt_version: str
    tools_used: list[str]
    execution_time_ms: int
    token_input: int
    token_output: int
    errors: list[str]
    retries: int
    human_interventions: int

    signals: list[SignalResult] = field(default_factory=list)
    overall_score: float = 0.0
    verdict: str = "NOT_RUN"    # PASS | FAIL | PARTIAL | NOT_RUN

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "agent_version": self.agent_version,
            "task_id": self.task_id,
            "stage": self.stage,
            "workspace_id": self.workspace_id,
            "model": self.model,
            "model_version": self.model_version,
            "prompt_version": self.prompt_version,
            "tools_used": self.tools_used,
            "execution_time_ms": self.execution_time_ms,
            "token_input": self.token_input,
            "token_output": self.token_output,
            "errors": self.errors,
            "retries": self.retries,
            "human_interventions": self.human_interventions,
            "overall_score": round(self.overall_score, 3),
            "verdict": self.verdict,
            "signals": [
                {
                    "signal": s.signal,
                    "value": s.value,
                    "threshold": s.threshold,
                    "passed": s.passed,
                    "detail": s.detail,
                }
                for s in self.signals
            ],
        }


class EvaluationBuilder:
    """Fluent builder for an EvaluationRecord.

    Usage:
        record = (
            EvaluationBuilder(agent_id="axis-ui-generator", stage="ui-code", workspace_id="ws-123")
            .set_model("claude-sonnet-4-6", "20250930")
            .set_execution(start_ms, end_ms, tokens_in=1200, tokens_out=800)
            .add_signal(EvalSignal.COMPILATION, value=1.0)
            .add_signal(EvalSignal.GROUNDING_SCORE, value=0.90)
            .build()
        )
    """

    def __init__(self, *, agent_id: str, stage: str, workspace_id: str) -> None:
        self._agent_id = agent_id
        self._agent_version = "1.0"
        self._task_id = ""
        self._stage = stage
        self._workspace_id = workspace_id
        self._model = ""
        self._model_version = ""
        self._prompt_version = "1.0"
        self._tools: list[str] = []
        self._start_ms: int = int(time.time() * 1000)
        self._end_ms: int = self._start_ms
        self._token_input = 0
        self._token_output = 0
        self._errors: list[str] = []
        self._retries = 0
        self._human_interventions = 0
        self._signal_values: dict[str, float | None] = {}
        self._signal_details: dict[str, str] = {}

    def set_task(self, task_id: str) -> "EvaluationBuilder":
        self._task_id = task_id
        return self

    def set_model(self, model: str, version: str = "") -> "EvaluationBuilder":
        self._model = model
        self._model_version = version
        return self

    def set_prompt_version(self, version: str) -> "EvaluationBuilder":
        self._prompt_version = version
        return self

    def set_tools(self, tools: list[str]) -> "EvaluationBuilder":
        self._tools = tools
        return self

    def set_execution(
        self,
        start_ms: int,
        end_ms: int,
        *,
        tokens_in: int = 0,
        tokens_out: int = 0,
    ) -> "EvaluationBuilder":
        self._start_ms = start_ms
        self._end_ms = end_ms
        self._token_input = tokens_in
        self._token_output = tokens_out
        return self

    def add_error(self, msg: str) -> "EvaluationBuilder":
        self._errors.append(msg)
        return self

    def set_retries(self, n: int) -> "EvaluationBuilder":
        self._retries = n
        return self

    def set_human_interventions(self, n: int) -> "EvaluationBuilder":
        self._human_interventions = n
        return self

    def add_signal(
        self, signal: EvalSignal | str, value: float | None, detail: str = ""
    ) -> "EvaluationBuilder":
        key = signal.value if isinstance(signal, EvalSignal) else str(signal)
        self._signal_values[key] = value
        self._signal_details[key] = detail
        return self

    def build(self) -> EvaluationRecord:
        elapsed = self._end_ms - self._start_ms

        signals: list[SignalResult] = []
        gated_total = 0
        gated_passed = 0

        for sig_key, value in self._signal_values.items():
            threshold = _SIGNAL_THRESHOLDS.get(sig_key)
            if threshold is not None and value is not None:
                passed: bool | None = value >= threshold
                gated_total += 1
                if passed:
                    gated_passed += 1
            else:
                passed = None

            signals.append(SignalResult(
                signal=sig_key,
                value=value,
                threshold=threshold,
                passed=passed,
                detail=self._signal_details.get(sig_key, ""),
            ))

        signals.append(SignalResult(signal=EvalSignal.EXECUTION_TIME_MS, value=elapsed, threshold=None, passed=None, detail=f"{elapsed}ms"))
        signals.append(SignalResult(signal=EvalSignal.TOKEN_INPUT, value=self._token_input, threshold=None, passed=None))
        signals.append(SignalResult(signal=EvalSignal.TOKEN_OUTPUT, value=self._token_output, threshold=None, passed=None))
        signals.append(SignalResult(signal=EvalSignal.HUMAN_INTERVENTIONS, value=self._human_interventions, threshold=None, passed=None))

        overall_score = gated_passed / gated_total if gated_total > 0 else 1.0
        failed_sigs = [s for s in signals if s.passed is False]

        if gated_total == 0:
            verdict = "NOT_RUN"
        elif not failed_sigs:
            verdict = "PASS"
        elif gated_passed == 0:
            verdict = "FAIL"
        else:
            verdict = "PARTIAL"

        log.info(
            "agent_evaluation agent_id=%s stage=%s workspace_id=%s verdict=%s score=%.3f gated=%d passed=%d elapsed_ms=%d",
            self._agent_id, self._stage, self._workspace_id, verdict, overall_score, gated_total, gated_passed, elapsed,
        )

        return EvaluationRecord(
            agent_id=self._agent_id,
            agent_version=self._agent_version,
            task_id=self._task_id,
            stage=self._stage,
            workspace_id=self._workspace_id,
            model=self._model,
            model_version=self._model_version,
            prompt_version=self._prompt_version,
            tools_used=self._tools,
            execution_time_ms=elapsed,
            token_input=self._token_input,
            token_output=self._token_output,
            errors=self._errors,
            retries=self._retries,
            human_interventions=self._human_interventions,
            signals=signals,
            overall_score=overall_score,
            verdict=verdict,
        )


# ── Part 2: Async DB-backed evaluation (observability SDK) ────────────────────

_NFR_THRESHOLDS_PATH = Path(__file__).parent.parent.parent / "nfr-thresholds.json"
_nfr_thresholds_cache: dict[str, Any] = {}


def _load_nfr_thresholds() -> dict[str, Any]:
    global _nfr_thresholds_cache
    if not _nfr_thresholds_cache and _NFR_THRESHOLDS_PATH.exists():
        try:
            _nfr_thresholds_cache = json.loads(_NFR_THRESHOLDS_PATH.read_text())
        except Exception as exc:
            log.warning(f"[evaluation] nfr-thresholds.json load failed: {exc}")
    return _nfr_thresholds_cache


_HALLUCINATION_PASS_THRESHOLD = 0.20  # PASS when hallucination_risk <= this
_GROUNDEDNESS_PASS_THRESHOLD = 0.80   # PASS when groundedness_score >= this
_DEFAULT_LLM_PASS_THRESHOLD = 0.70    # PASS when other scores >= this


@dataclass
class EvaluationResult:
    evaluator_type: str
    dimension: str
    score: float | None
    passed_threshold: bool | None
    threshold_value: float | None
    rationale: str
    agent_run_id: str
    artifact_id: str
    rule_id: str
    evidence_absent: bool = False


class EvaluationService:
    """Score agent output artifacts on configurable dimensions (threshold, LLM-as-judge, human)."""

    def evaluate_threshold(
        self,
        *,
        agent_run_id: str,
        artifact_id: str = "",
        rule_id: str = "",
        latency_ms: float | None = None,
        tokens_total: int | None = None,
    ) -> list[EvaluationResult]:
        """Check agent run metrics against nfr-thresholds.json."""
        thresholds = _load_nfr_thresholds()
        results: list[EvaluationResult] = []
        svc = get_telemetry_service()

        svc.emit(EventType.EVALUATION_STARTED, {"agent_run_id": agent_run_id, "evaluator_type": "threshold"})

        if latency_ms is not None:
            limit = thresholds.get("response_time_ms", 30000)
            passed = latency_ms <= limit
            results.append(EvaluationResult(
                evaluator_type="threshold", dimension="latency",
                score=1.0 if passed else 0.0, passed_threshold=passed,
                threshold_value=float(limit),
                rationale=f"{latency_ms:.0f}ms vs {limit}ms limit",
                agent_run_id=agent_run_id, artifact_id=artifact_id, rule_id=rule_id,
            ))

        if tokens_total is not None:
            limit = thresholds.get("token_limit", 4096)
            passed = tokens_total <= limit
            results.append(EvaluationResult(
                evaluator_type="threshold", dimension="token_budget",
                score=1.0 if passed else 0.0, passed_threshold=passed,
                threshold_value=float(limit),
                rationale=f"{tokens_total} tokens vs {limit} limit",
                agent_run_id=agent_run_id, artifact_id=artifact_id, rule_id=rule_id,
            ))

        svc.emit(EventType.EVALUATION_COMPLETED, {
            "agent_run_id": agent_run_id, "evaluator_type": "threshold",
            "dimensions_checked": len(results),
            "all_passed": all(r.passed_threshold for r in results if r.passed_threshold is not None),
        })
        return results

    async def evaluate_with_llm(
        self,
        *,
        agent_run_id: str,
        artifact_content: str,
        reference_rules: list[str],
        artifact_id: str = "",
        rule_id: str = "",
        workspace_id: str = "",
        evidence: list[str] | None = None,
    ) -> list[EvaluationResult]:
        """Use the ModelRouter's 'evaluate' task to score correctness, completeness, and hallucination.

        evidence: KB chunk texts to include in the judge prompt so it can verify grounding.
            When None or empty, evidence_absent=True is set on hallucination_risk result.

        Threshold semantics:
            hallucination_risk: PASS when score <= _HALLUCINATION_PASS_THRESHOLD (higher = worse)
            other dimensions:   PASS when score >= _DEFAULT_LLM_PASS_THRESHOLD
        """
        from app.services.model_router import get_model_router

        svc = get_telemetry_service()
        svc.emit(EventType.EVALUATION_STARTED, {
            "agent_run_id": agent_run_id, "evaluator_type": "llm",
            "rule_count": len(reference_rules), "workspace_id": workspace_id,
        })

        evidence_absent = not evidence
        results: list[EvaluationResult] = []
        t0 = time.perf_counter()

        try:
            llm = get_model_router().get_llm("evaluate")
            eval_prompt = _build_eval_prompt(artifact_content, reference_rules, evidence=evidence)
            response = await llm.ainvoke(eval_prompt)
            scores = _parse_eval_response(response.content if hasattr(response, "content") else str(response))

            for dimension, (score, rationale) in scores.items():
                if dimension == "hallucination_risk":
                    # Higher score = more hallucination risk; PASS when score <= threshold
                    threshold = _HALLUCINATION_PASS_THRESHOLD
                    passed = score <= threshold
                    is_ev_absent = evidence_absent
                else:
                    threshold = _DEFAULT_LLM_PASS_THRESHOLD
                    passed = score >= threshold
                    is_ev_absent = False
                results.append(EvaluationResult(
                    evaluator_type="llm", dimension=dimension, score=score,
                    passed_threshold=passed, threshold_value=threshold, rationale=rationale,
                    agent_run_id=agent_run_id, artifact_id=artifact_id, rule_id=rule_id,
                    evidence_absent=is_ev_absent,
                ))
        except Exception as exc:
            log.warning(f"[evaluation] LLM evaluation failed (non-fatal): {exc}")
            results.append(EvaluationResult(
                evaluator_type="llm", dimension="error", score=None,
                passed_threshold=None, threshold_value=None,
                rationale=f"Evaluation error: {type(exc).__name__}",
                agent_run_id=agent_run_id, artifact_id=artifact_id, rule_id=rule_id,
            ))

        latency_ms = round((time.perf_counter() - t0) * 1000.0, 1)
        svc.emit(EventType.EVALUATION_COMPLETED, {
            "agent_run_id": agent_run_id, "evaluator_type": "llm",
            "dimensions_scored": len([r for r in results if r.score is not None]),
            "latency_ms": latency_ms, "workspace_id": workspace_id,
        })
        return results

    async def persist(self, results: list[EvaluationResult], *, pool: Any, schema: str) -> None:
        """Write evaluation results to fe_evaluations. Fire-and-forget — errors are logged."""
        import asyncio
        for result in results:
            asyncio.create_task(self._insert_one(result, pool=pool, schema=schema))

    async def _insert_one(self, result: EvaluationResult, *, pool: Any, schema: str) -> None:
        sql = f"""
            INSERT INTO {schema}.fe_evaluations
                (agent_run_id, artifact_id, rule_id, evaluator_type, dimension,
                 score, passed_threshold, threshold_value, rationale, evaluated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
        """
        try:
            async with pool.connection() as conn:
                await conn.execute(sql, (
                    result.agent_run_id or None, result.artifact_id or None,
                    result.rule_id or None, result.evaluator_type, result.dimension,
                    result.score, result.passed_threshold, result.threshold_value, result.rationale,
                ))
        except Exception as exc:
            log.warning(f"[evaluation] persist failed (non-fatal): {exc}")


# ── LLM eval prompt helpers ───────────────────────────────────────────────────

_EVAL_SYSTEM = (
    "You are an expert software requirements auditor. "
    "Evaluate the provided artifact against the reference business rules. "
    "Return ONLY valid JSON with the structure: "
    '{{"correctness": [0.0-1.0, "rationale"], '
    '"completeness": [0.0-1.0, "rationale"], '
    '"hallucination_risk": [0.0-1.0, "rationale (0=no hallucination, 1=severe)"]}}. '
    "Do not include any text outside the JSON."
)


def _build_eval_prompt(
    artifact_content: str,
    reference_rules: list[str],
    evidence: list[str] | None = None,
) -> list[dict]:
    rules_text = "\n".join(f"- {r}" for r in reference_rules[:50])
    evidence_section = ""
    if evidence:
        ev_text = "\n".join(f"[{i+1}] {e[:400]}" for i, e in enumerate(evidence[:20]))
        evidence_section = f"\n\n## Knowledge Base Evidence\n{ev_text}"
    return [
        {"role": "system", "content": _EVAL_SYSTEM},
        {"role": "user", "content": (
            f"## Reference Business Rules\n{rules_text}\n\n"
            f"## Artifact to Evaluate\n{artifact_content[:8000]}"
            f"{evidence_section}"
        )},
    ]


def _parse_eval_response(raw: str) -> dict[str, tuple[float, str]]:
    """Parse the LLM evaluation JSON response. Returns empty dict on parse failure."""
    try:
        start = raw.find("{")
        end = raw.rfind("}") + 1
        if start == -1 or end == 0:
            return {}
        data = json.loads(raw[start:end])
        result = {}
        for key in ("correctness", "completeness", "hallucination_risk"):
            if key in data and isinstance(data[key], list) and len(data[key]) >= 2:
                score = float(data[key][0])
                rationale = str(data[key][1])
                result[key] = (max(0.0, min(1.0, score)), rationale)
        return result
    except Exception:
        return {}


def get_evaluation_service() -> EvaluationService:
    return EvaluationService()
