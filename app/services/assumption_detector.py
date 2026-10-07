"""Detect and mark assumption vs fact lines in LLM-generated artifacts.

Scans produced markdown for phrases that indicate an assumption, placeholder,
or unverified claim.  The summary is stored in artifact metadata so reviewers
can identify silent requirement invention without reading every line.

Usage (called from StageExecutor._finalize):

    from app.services.assumption_detector import assumption_summary
    summary = assumption_summary(markdown)
    # attach summary to artifact.metadata["assumption_analysis"]
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Patterns that signal an assumption or unverified claim.
_ASSUMPTION_RE = re.compile(
    r"\b("
    r"assume[sd]?|assuming|we assume|it is assumed"
    r"|TBD|TODO|FIXME|placeholder|to be (confirmed|defined|determined|discussed)"
    r"|TBC|pending|subject to|not yet defined|needs confirmation"
    r"|may need|might need|could be|should be confirmed"
    r"|unclear|unknown at this time"
    r")\b",
    re.IGNORECASE,
)


@dataclass
class AssumptionMark:
    line_no: int
    text: str
    kind: str   # "ASSUMPTION" | "FACT"


def detect_assumptions(markdown: str) -> list[AssumptionMark]:
    """Return one AssumptionMark per non-empty line, tagged ASSUMPTION or FACT."""
    marks: list[AssumptionMark] = []
    for i, line in enumerate(markdown.splitlines(), 1):
        stripped = line.strip()
        if not stripped:
            continue
        kind = "ASSUMPTION" if _ASSUMPTION_RE.search(stripped) else "FACT"
        marks.append(AssumptionMark(line_no=i, text=stripped, kind=kind))
    return marks


def assumption_summary(markdown: str) -> dict:
    """Return a dict summarising assumption density suitable for artifact metadata.

    Keys:
        total_lines       — number of non-empty lines scanned
        assumption_count  — lines flagged as ASSUMPTION
        fact_count        — lines flagged as FACT
        assumption_ratio  — assumption_count / total_lines (0.0 – 1.0)
        assumption_lines  — list of 1-indexed line numbers flagged as ASSUMPTION
    """
    marks = detect_assumptions(markdown)
    total = len(marks)
    assumptions = [m for m in marks if m.kind == "ASSUMPTION"]
    assumption_count = len(assumptions)
    return {
        "total_lines": total,
        "assumption_count": assumption_count,
        "fact_count": total - assumption_count,
        "assumption_ratio": round(assumption_count / total, 3) if total else 0.0,
        "assumption_lines": [m.line_no for m in assumptions],
    }
