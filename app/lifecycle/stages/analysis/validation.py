"""Requirement text validation & correction — detect and fix common parsing/concatenation errors.

Handles:
- Concatenated words (NeedThe → Need the, TheBasic → The Basic)
- Malformed separators (。 at wrong positions, double spaces)
- Encoding issues (mojibake, misaligned multi-byte chars)
- Common form-submission errors (missing line breaks, field collapse)

All corrections logged with before/after pairs for audit trail.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import NamedTuple

from app.utils.logging import log


class ValidationResult(NamedTuple):
    """Result of requirement validation + correction."""

    text: str  # The corrected text (same as input if no errors found)
    issues: list[str]  # List of detected issues (empty if text is clean)
    corrections: list[tuple[str, str]]  # (original, corrected) pairs for audit
    is_clean: bool  # True if no issues detected


# Common concatenation patterns — words that are frequently merged (title case helps detect)
# E.g., "TheBasic" (capital B after capital T), "NeedThe" (lower-case e before capital T)
_CONCAT_PATTERNS = [
    # (pattern_regex, replacement, description)
    (r"(\w)([A-Z][a-z]+)([A-Z]\w+)", r"\1 \2 \3", "multi-word concatenation"),
    (r"Need([A-Z])", r"Need \1", "Need+Word (e.g., NeedThe)"),
    (r"The([A-Z][a-z]+)([A-Z])", r"The \1 \2", "The+Words"),
    (r"Add([A-Z])", r"Add \1", "Add+Word"),
    (r"Update([A-Z])", r"Update \1", "Update+Word"),
    (r"Create([A-Z])", r"Create \1", "Create+Word"),
    (r"Modify([A-Z])", r"Modify \1", "Modify+Word"),
    (r"Change([A-Z])", r"Change \1", "Change+Word"),
    (r"Enhance([A-Z])", r"Enhance \1", "Enhance+Word"),
    (r"Remove([A-Z])", r"Remove \1", "Remove+Word"),
    (r"Delete([A-Z])", r"Delete \1", "Delete+Word"),
]

# Separator/spacing issues
_SPACING_PATTERNS = [
    (r"\s{2,}", " ", "multiple spaces"),
    (r"\n{3,}", "\n\n", "multiple blank lines"),
    (r"(\S)。(\S)", r"\1 。 \2", "Japanese period without spaces"),
    (r"(\S)\。(\S)", r"\1 。 \2", "malformed Japanese period"),
]

# Common mojibake / encoding patterns (Japanese-specific)
_ENCODING_PATTERNS = [
    (r"[\x80-\xFF][\x00-\x7F]", "possible mojibake"),  # High byte followed by ASCII
]


def _split_camel_case(text: str) -> str:
    """Insert space before capital letters in camelCase (e.g., 'BasicInfo' → 'Basic Info').

    NOTE: DO NOT split component/code identifiers (e.g., EmailEditorModalComponent, userId).
    Only split when it looks like a natural-language concatenation error, not a valid identifier.

    Identifiers to preserve:
    - *Component, *Modal, *Service, *Handler (known patterns in codebase)
    - camelCase words with 2-3 parts (likely single identifiers)
    - Words containing 'Editor', 'Modal', 'Dialog' (UI component names)
    """
    # P0 FIX: Don't split if it looks like a legitimate component/code identifier
    # Known component/code identifier patterns to PRESERVE (no splitting)
    preserve_patterns = [
        r"\w+(Component|Modal|Dialog|Service|Handler|Controller|Manager|Factory|Provider|Config|Setting|Option|List|Grid|Table|View|Form|Input|Output|Request|Response|Api|API|DTO|Entity|Repository|Dao|Helper)",
        r"\w+(Editor|Viewer|Parser|Generator|Builder|Mapper|Converter|Adapter|Decorator|Interceptor|Filter|Middleware)",
        r"[a-z][a-zA-Z]{2,}[A-Z][a-z]{2,}[A-Z]",  # camelCase with multiple capitals (likely identifier)
    ]

    for pattern in preserve_patterns:
        if re.search(pattern, text):
            # Found a component/code identifier pattern — don't split it
            return text

    # Safe to split: only split actual concatenation errors (e.g., "TheBasic", "NeedThe")
    # Split only lowercase→uppercase transitions that look like mistakes
    result = re.sub(r"([a-z])([A-Z][a-z]+)([A-Z])", r"\1 \2 \3", text)
    return result


def _fix_concatenations(text: str) -> tuple[str, list[tuple[str, str]]]:
    """Attempt to fix word concatenations using pattern matching."""
    corrected = text
    corrections: list[tuple[str, str]] = []

    for pattern, replacement, description in _CONCAT_PATTERNS:
        matches = re.finditer(pattern, corrected)
        for match in matches:
            original = match.group(0)
            new_text = re.sub(pattern, replacement, original)
            if original != new_text:
                corrections.append((original, new_text))
                corrected = corrected.replace(original, new_text, 1)
                log.debug(f"[validation] fixed concatenation '{original}' → '{new_text}' ({description})")

    # Fallback: split obvious camelCase
    if not corrections and re.search(r"[a-z][A-Z]", text):
        before = corrected
        corrected = _split_camel_case(corrected)
        if before != corrected:
            corrections.append((before, corrected))
            log.debug(f"[validation] split camelCase: '{before}' → '{corrected}'")

    return corrected, corrections


def _fix_spacing(text: str) -> tuple[str, list[tuple[str, str]]]:
    """Fix spacing/separator issues."""
    corrected = text
    corrections: list[tuple[str, str]] = []

    for pattern, replacement, description in _SPACING_PATTERNS:
        before = corrected
        corrected = re.sub(pattern, replacement, corrected)
        if before != corrected:
            corrections.append((pattern, description))
            log.debug(f"[validation] fixed spacing: {description}")

    return corrected, corrections


def _check_encoding(text: str) -> list[str]:
    """Check for encoding issues (mojibake, etc.)."""
    issues: list[str] = []

    # Check for common mojibake patterns
    for pattern, description in _ENCODING_PATTERNS:
        if re.search(pattern, text):
            issues.append(f"possible {description} detected")
            log.debug(f"[validation] {description} in requirement text")

    # Check for excessive null bytes or control characters
    control_chars = sum(1 for c in text if ord(c) < 32 and c not in "\n\r\t")
    if control_chars > 0:
        issues.append(f"contains {control_chars} control character(s)")

    return issues


def _analyze_structure(text: str) -> list[str]:
    """Analyze the requirement structure for logical issues."""
    issues: list[str] = []

    if not text or not text.strip():
        issues.append("requirement text is empty")
        return issues

    if len(text) < 10:
        issues.append("requirement text is suspiciously short (< 10 chars)")

    # Check if it looks like a requirement (should have action verbs or entities)
    action_verbs = {
        "add", "remove", "delete", "create", "modify", "update", "change",
        "enhance", "extend", "implement", "support", "enable", "disable",
        "fix", "improve", "refactor", "migrate",
    }
    has_verb = any(verb in text.lower() for verb in action_verbs)
    if not has_verb and len(text) < 50:
        issues.append("no clear action verb detected (text may be truncated)")

    return issues


def validate_and_correct(requirement: str | None) -> ValidationResult:
    """
    Validate requirement text and auto-correct common errors.

    Returns ValidationResult with:
    - text: the corrected requirement (same as input if clean)
    - issues: list of detected problems
    - corrections: list of (original, corrected) pairs showing what was fixed
    - is_clean: True if no issues detected
    """
    if not requirement:
        return ValidationResult(
            text="",
            issues=["requirement is None or empty"],
            corrections=[],
            is_clean=False,
        )

    original_text = requirement
    text = requirement
    all_corrections: list[tuple[str, str]] = []
    all_issues: list[str] = []

    # 1. Fix concatenations
    text, concat_fixes = _fix_concatenations(text)
    all_corrections.extend(concat_fixes)

    # 2. Fix spacing
    text, spacing_fixes = _fix_spacing(text)
    all_corrections.extend(spacing_fixes)

    # 3. Check encoding
    encoding_issues = _check_encoding(text)
    all_issues.extend(encoding_issues)

    # 4. Analyze structure
    structure_issues = _analyze_structure(text)
    all_issues.extend(structure_issues)

    is_clean = len(all_issues) == 0 and len(all_corrections) == 0

    if not is_clean:
        log.info(
            f"[validation] requirement text: issues={len(all_issues)}, corrections={len(all_corrections)}",
        )
        if all_corrections:
            for original, corrected in all_corrections:
                log.info(f"  corrected: '{original}' → '{corrected}'")
        if all_issues:
            for issue in all_issues:
                log.warning(f"  issue: {issue}")

    return ValidationResult(
        text=text,
        issues=all_issues,
        corrections=all_corrections,
        is_clean=is_clean,
    )


def get_validation_warning_banner(result: ValidationResult) -> str | None:
    """
    Generate a human-readable warning banner if validation found issues.

    Returns None if text is clean, otherwise a formatted message suitable for logs/UI.
    """
    if result.is_clean:
        return None

    lines = ["⚠️ Requirement text had issues that were auto-corrected:"]

    if result.corrections:
        lines.append("\nCorrected concatenations/spacing:")
        for original, corrected in result.corrections:
            lines.append(f"  • '{original}' → '{corrected}'")

    if result.issues:
        lines.append("\nRemaining issues/warnings:")
        for issue in result.issues:
            lines.append(f"  • {issue}")

    lines.append(
        "\n✓ Proceeding with corrected text. Check the analysis result carefully.",
    )

    return "\n".join(lines)
