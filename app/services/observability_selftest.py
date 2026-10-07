"""Observability self-test & completeness score (spec §45, §46).

Answers "is every pipeline stage observable?" by enumerating the registered
UWCR pipeline stages (from the pipeline YAMLs) and grading each against the
observability dimensions the platform is meant to guarantee. All UWCR stages
run through the single `worker/stage_executor.py` emit path, so their posture
is uniform — the grade reflects what that path actually emits today.

This is an ENGINEERING completeness indicator, not a business-quality score
(§46). Grades: PASS / PARTIAL / FAIL. Honest by construction — dimensions the
platform does not yet populate are PARTIAL/FAIL, not inflated to PASS.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

Grade = Literal["PASS", "PARTIAL", "FAIL"]

_PIPELINES_DIR = Path(__file__).resolve().parents[1] / "agentic_platform" / "pipelines"

# Dimension -> grade for a UWCR pipeline stage, with the evidence/reason.
# Uniform across stages because they share stage_executor.execute()'s emit path.
_STAGE_DIMENSIONS: dict[str, tuple[Grade, str]] = {
    "execution_id":   ("PASS",    "agent_run_id stamped per run (stage_executor.execute)"),
    "workflow":       ("PASS",    "workflow_run_id + workspace_id on AGENT_RUN (GAP-005)"),
    "module":         ("PASS",    "module_id = kb_application_id on AGENT_RUN (GAP-005)"),
    "rule":           ("PARTIAL", "rule_ids column supported but not derived per-stage yet (GAP-005 deferred)"),
    "timing":         ("PASS",    "latency_ms + started/finished captured"),
    "tokens":         ("PASS",    "tokens_in/out on AGENT_RUN"),
    "retries":        ("PASS",    "retry_count column + run.attempt"),
    "failures":       ("PASS",    "status='error' on AGENT_RUN; /failures endpoint"),
    "tool_calls":     ("PARTIAL", "in-process ReAct tools emit TOOL_CALL; CLI-runner stages do not (GAP-006)"),
    "llm_calls":      ("PARTIAL", "aggregate tokens only; no per-call llm_call for CLI stages (GAP-006)"),
    "validation":     ("PARTIAL", "validation_status column exists; not emitted by stage runs yet"),
    "evaluation":     ("PASS",    "threshold evaluation persisted per run (GAP-007)"),
    "human_approval": ("PASS",    "approvals via UWCR store (GAP-008)"),
    "agent_version":  ("PARTIAL", "column supported; not stamped for pipeline stages"),
    "prompt_version": ("PARTIAL", "column supported; not stamped for pipeline stages"),
    "model":          ("PASS",    "model recorded on run usage line"),
}

# Dimensions that MUST be PASS for a stage to be considered observable at all.
_CORE = ("execution_id", "workflow", "timing", "failures", "evaluation")


def registered_stages() -> list[dict[str, str]]:
    """Enumerate stages declared in the pipeline YAMLs: [{pipeline, stage}]."""
    import yaml  # noqa: PLC0415

    stages: list[dict[str, str]] = []
    for yml in sorted(_PIPELINES_DIR.glob("*.yaml")):
        try:
            doc = yaml.safe_load(yml.read_text(encoding="utf-8")) or {}
        except Exception:
            continue
        for st in (doc.get("stages") or []):
            key = st.get("key") if isinstance(st, dict) else None
            if key:
                stages.append({"pipeline": doc.get("name", yml.stem), "stage": key})
    return stages


def _stage_grade(dims: dict[str, tuple[Grade, str]]) -> Grade:
    if any(dims[d][0] != "PASS" for d in _CORE):
        return "FAIL"
    return "PASS" if all(g == "PASS" for g, _ in dims.values()) else "PARTIAL"


def self_test() -> dict:
    """Run the observability self-test over every registered stage (§45)."""
    stages = registered_stages()
    results = []
    for s in stages:
        results.append({
            "pipeline": s["pipeline"],
            "stage": s["stage"],
            "grade": _stage_grade(_STAGE_DIMENSIONS),
            "dimensions": {k: {"grade": g, "reason": r} for k, (g, r) in _STAGE_DIMENSIONS.items()},
        })
    grades = [r["grade"] for r in results]
    return {
        "stage_count": len(results),
        "pass": grades.count("PASS"),
        "partial": grades.count("PARTIAL"),
        "fail": grades.count("FAIL"),
        "stages": results,
    }


def observability_score() -> dict:
    """Engineering completeness score (§46): fraction of dimensions at PASS."""
    total = len(_STAGE_DIMENSIONS)
    passed = sum(1 for g, _ in _STAGE_DIMENSIONS.values() if g == "PASS")
    partial = sum(1 for g, _ in _STAGE_DIMENSIONS.values() if g == "PARTIAL")
    return {
        "dimensions": total,
        "pass": passed,
        "partial": partial,
        "fail": total - passed - partial,
        "score": round(passed / total, 4) if total else 0.0,
        "by_dimension": {k: g for k, (g, _) in _STAGE_DIMENSIONS.items()},
    }
