"""Structural validation of LLM-generated SDLC artifacts.

Checks that generated markdown contains the required section headings before
the artifact is presented to the approver.  Validation is SOFT — a missing
section is recorded in artifact metadata but does not block the approval gate.
This surfaces structural defects to the reviewer without stopping the pipeline.

Usage (called from StageExecutor._finalize):

    from app.services.artifact_schema_validator import validate_artifact
    result = validate_artifact(markdown, stage_key="frd")
    if not result.valid:
        logger.warning("Artifact missing sections: %s", result.missing_sections)
"""

from __future__ import annotations

import re
from typing import NamedTuple

# Required section headings per stage (case-insensitive substring match).
_REQUIRED_SECTIONS: dict[str, list[str]] = {
    "frd": [
        "Functional Requirements",
        "Acceptance Criteria",
    ],
    "prd": [
        "Business Requirements",
        "Scope",
    ],
    "nfr": [
        "Performance",
        "Security",
    ],
    "lld": [
        "Component",
        "Interface",
    ],
    "adr": [
        "Decision",
        "Consequences",
    ],
    "srd": [
        "System Requirements",
    ],
    "sdd": [
        "Architecture",
        "Components",
    ],
}

_HEADING_RE = re.compile(r"^#{1,4}\s+(.+)$", re.MULTILINE)


class ValidationResult(NamedTuple):
    valid: bool
    missing_sections: list[str]
    stage_key: str
    heading_count: int


def validate_artifact(markdown: str, stage_key: str) -> ValidationResult:
    """Validate that `markdown` contains the required headings for `stage_key`.

    Returns a ValidationResult with `valid=True` when all required sections
    are present, or `valid=False` with `missing_sections` listing what's absent.
    Stages without a schema definition always return valid=True.
    """
    required = _REQUIRED_SECTIONS.get(stage_key, [])
    if not required:
        return ValidationResult(valid=True, missing_sections=[], stage_key=stage_key, heading_count=0)

    headings = _HEADING_RE.findall(markdown)
    headings_lower = [h.lower() for h in headings]
    missing = [
        section for section in required
        if not any(section.lower() in h for h in headings_lower)
    ]
    return ValidationResult(
        valid=len(missing) == 0,
        missing_sections=missing,
        stage_key=stage_key,
        heading_count=len(headings),
    )
