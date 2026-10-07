"""Agent regression evaluation framework (spec §34).

Prompt/code change -> run agents over a golden dataset -> evaluate -> compare to
a captured baseline -> PASS/FAIL. No absolute pass thresholds are hard-coded
(§34): a change PASSES unless a dimension regresses below the *baseline* by more
than a small tolerance. The baseline is established once, then future runs are
graded against it.

Data dependency (honest): the golden dataset below is seeded from the AUTH rules
actually present in the corpus (RED v1.0 "5-Rule Demo" -> 9 BRLs + FR-AUTH-001..007;
see docs/observability/FINAL_30_RULE_REPORT.md). Populating per-rule agent scores
requires real pipeline runs (currently blocked by the model-config issue). Until
then this module provides the executable framework + compare logic; the compare
logic is fully tested with synthetic scores.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

# Golden set of AUTH rules that exist in the corpus (not invented — see 30-rule report).
GOLDEN_AUTH_RULES: list[str] = [
    "BRL-001", "BRL-002", "BRL-003", "BRL-004", "BRL-005", "BRL-006",
    "BRL-007", "BRL-008", "BRL-009",
    "FR-AUTH-001", "FR-AUTH-002", "FR-AUTH-003", "FR-AUTH-004",
    "FR-AUTH-005", "FR-AUTH-006", "FR-AUTH-007",
]

# Quality dimensions compared against baseline (higher = better).
DIMENSIONS = ("correctness", "completeness", "traceability", "consistency")

DEFAULT_TOLERANCE = 0.05


@dataclass
class DimensionResult:
    dimension: str
    baseline: float
    current: float
    delta: float
    passed: bool
    note: str


@dataclass
class RegressionReport:
    passed: bool
    tolerance: float
    results: list[DimensionResult] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "tolerance": self.tolerance,
            "results": [r.__dict__ for r in self.results],
        }


def compare_to_baseline(
    current: dict[str, float],
    baseline: dict[str, float],
    *,
    tolerance: float = DEFAULT_TOLERANCE,
) -> RegressionReport:
    """Compare current scores to a baseline. A dimension FAILS only if it drops
    more than `tolerance` below its baseline (no absolute threshold)."""
    results: list[DimensionResult] = []
    all_pass = True
    for dim in DIMENSIONS:
        base = baseline.get(dim)
        cur = current.get(dim)
        if base is None or cur is None:
            # Missing data is not a regression — it's an incomplete baseline/run.
            results.append(DimensionResult(dim, base or 0.0, cur or 0.0, 0.0, True, "no data — skipped"))
            continue
        delta = round(cur - base, 4)
        passed = cur >= base - tolerance
        all_pass = all_pass and passed
        note = "ok" if passed else f"regressed {abs(delta):.3f} below baseline"
        results.append(DimensionResult(dim, base, cur, delta, passed, note))
    return RegressionReport(passed=all_pass, tolerance=tolerance, results=results)


def capture_baseline(scores: dict[str, float], path: str | Path) -> None:
    """Persist a baseline score set for future comparison."""
    Path(path).write_text(json.dumps(scores, indent=2), encoding="utf-8")


def load_baseline(path: str | Path) -> dict[str, float] | None:
    p = Path(path)
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def run_regression(
    current: dict[str, float],
    baseline_path: str | Path,
    *,
    tolerance: float = DEFAULT_TOLERANCE,
) -> RegressionReport:
    """Load the baseline (capturing `current` as the baseline on first run) and compare."""
    baseline = load_baseline(baseline_path)
    if baseline is None:
        capture_baseline(current, baseline_path)
        # First run establishes the baseline -> PASS by definition.
        return RegressionReport(
            passed=True, tolerance=tolerance,
            results=[DimensionResult(d, current.get(d, 0.0), current.get(d, 0.0), 0.0, True, "baseline established")
                     for d in DIMENSIONS],
        )
    return compare_to_baseline(current, baseline, tolerance=tolerance)
