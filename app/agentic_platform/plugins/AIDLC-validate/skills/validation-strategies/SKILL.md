---
name: validation-strategies
description: Validation strategies for requirements completeness checking including content matching, confidence scoring, and gap reporting. Use when validating requirements coverage across SDLC stages.
---

# Validation Strategies Skill

This skill provides strategies for requirements validation across SDLC stages.

## Purpose

Provide consistent validation approaches for:
- Matching source requirements to target artifacts
- Assigning confidence levels to findings
- Reporting gaps with evidence
- Building traceability matrices

## Strategy Categories

| Category | File | Purpose |
|----------|------|---------|
| **Content Matching** | `content-matching.md` | Semantic comparison strategies |
| **Confidence Scoring** | `confidence-scoring.md` | HIGH/MEDIUM/LOW definitions |
| **Gap Reporting** | `gap-reporting.md` | Evidence-based gap formatting |
| **Traceability Rules** | `traceability-rules.md` | Coverage matrix generation |

## Load by Validation Type

| Validation Type | Strategies to Load |
|-----------------|-------------------|
| EPIC coverage | content-matching.md, confidence-scoring.md |
| Feature coverage | content-matching.md, confidence-scoring.md |
| Story coverage | content-matching.md, confidence-scoring.md |
| Traceability | traceability-rules.md, confidence-scoring.md |
| Full chain | All strategies |

## Usage Flow

1. **Router** determines validation type from user request
2. **Router** loads appropriate strategy files
3. **Validator** applies matching strategies from content-matching.md
4. **Validator** assigns confidence using confidence-scoring.md
5. **Validator** formats findings per gap-reporting.md
6. **Router** aggregates results using traceability-rules.md (if full chain)

## Quick Reference

### Matching Priority
1. **Exact ID** → HIGH confidence
2. **Term-based** → HIGH/MEDIUM based on overlap %
3. **Semantic** → MEDIUM confidence
4. **Content/Fuzzy** → LOW confidence

### Confidence Levels
- **HIGH**: Explicit requirement, comprehensive search, clear result
- **MEDIUM**: Implicit requirement, partial match possible
- **LOW**: Ambiguous requirement, fuzzy match only

### Gap Reporting
Every gap must include:
- Source citation (document, page, section)
- Requirement text
- Search strategy used
- Targets searched
- Confidence level
- Recommendation
- Human action required

## Integration

**Validators using this skill:**
- epic-coverage-validator
- feature-coverage-validator
- story-coverage-validator
- traceability-analyzer

**Router coordination:**
- requirements-validator-router passes skill references to validators
- Validators apply strategies consistently
- Results aggregated by router
