# Confidence Scoring

Definitions and criteria for confidence levels in requirements validation.

## Overview

Confidence scoring provides a measure of reliability for validation findings. All findings (both matches and gaps) must include a confidence level.

---

## Level Definitions

### HIGH Confidence

**Definition:** Very likely an accurate finding

**Criteria:**
- Explicit ID match found (BRD-001, F-001, etc.)
- 90% or more key terms match
- Exact phrase found in target
- Clear, unambiguous requirement statement
- Comprehensive search performed

**Interpretation:**
- Finding is very likely correct
- Low probability of error

**Human Action:**
- Brief verification sufficient
- Can likely proceed with finding

**Visual Indicator:** HIGH or HIGH-CONFIDENCE

---

### MEDIUM Confidence

**Definition:** Probably an accurate finding

**Criteria:**
- 60-89% key terms match
- Semantic equivalent found (synonym match)
- Requirement is partially explicit
- Context supports the finding
- Most relevant sections searched

**Interpretation:**
- Finding is probably correct
- Some probability of error

**Human Action:**
- Careful review needed
- Verify context alignment
- Check for alternative interpretations

**Visual Indicator:** MEDIUM or MED-CONFIDENCE

---

### LOW Confidence

**Definition:** May or may not be accurate

**Criteria:**
- Less than 60% key terms match
- Fuzzy or content-based match only
- Implicit or ambiguous requirement
- Limited search possible
- Context unclear

**Interpretation:**
- Finding may be incorrect
- Significant probability of error
- Could be false positive (gap that isn't real)
- Could be false negative (match that isn't valid)

**Human Action:**
- Thorough verification required
- Investigate alternative locations
- Consult with subject matter experts
- Do not act without verification

**Visual Indicator:** LOW or LOW-CONFIDENCE

---

## Scoring Algorithm

### For Match Detection

```
function calculateMatchConfidence(match):

  if match.type == "exact_id":
    return HIGH

  if match.type == "term_based":
    if match.termOverlap >= 0.90:
      return HIGH
    elif match.termOverlap >= 0.60:
      return MEDIUM
    else:
      return LOW

  if match.type == "semantic":
    return MEDIUM  # Semantic matches NEVER get HIGH

  if match.type == "filename":
    return MEDIUM

  if match.type == "content":
    return LOW  # Content-only matches always LOW

  return LOW  # Default for unclear matches
```

### For Gap Detection

```
function calculateGapConfidence(gap):

  # Factor 1: Requirement clarity
  if requirement.is_explicit:
    clarity_score = HIGH
  elif requirement.is_implicit:
    clarity_score = MEDIUM
  else:
    clarity_score = LOW

  # Factor 2: Search exhaustiveness
  if all_relevant_sections_searched:
    search_score = HIGH
  elif most_sections_searched:
    search_score = MEDIUM
  else:
    search_score = LOW

  # Factor 3: Match result
  if no_match_found:
    match_score = HIGH
  elif partial_match_found:
    match_score = MEDIUM
  else:
    match_score = LOW

  # Combined score (lowest wins)
  return min(clarity_score, search_score, match_score)
```

---

## Context Adjustments

### Upgrade Conditions (rare)

| From | To | Condition |
|------|-----|-----------|
| MEDIUM | HIGH | Multiple independent matches confirm finding |
| LOW | MEDIUM | Context strongly supports the finding |

### Downgrade Conditions (common for caution)

| From | To | Condition |
|------|-----|-----------|
| HIGH | MEDIUM | Large document, partial search only |
| MEDIUM | LOW | Ambiguous requirement text |
| Any | LOW | Code traceability (inherently unreliable) |

---

## Special Cases

### Code Traceability

Code traceability defaults to lower confidence due to:
- Code rarely contains requirement IDs
- Function names don't map to requirements
- One requirement spans multiple files

| Match Type | Max Confidence |
|------------|----------------|
| Explicit reference in code | HIGH |
| Filename convention | MEDIUM |
| Content/keyword match | LOW |

### Large Documents

For documents over 50 pages:
- Acknowledge partial processing
- Default to MEDIUM confidence maximum
- Note in report: "Large document - partial analysis"

### Multi-Format Sources

When source is PDF or scanned:
- Account for OCR errors
- Reduce confidence by one level
- Note: "OCR source - verify text accuracy"

---

## Reporting Confidence

### In Gap Reports

```markdown
**GAP-001: Missing Feature** - Confidence: HIGH

Confidence Factors:
- Requirement clarity: Explicit (numbered item)
- Search exhaustiveness: All feature docs searched
- Match result: No match or partial match found

Human Action: Brief verification - likely a real gap
```

### In Summary Tables

```markdown
| Category | Coverage | Confidence | Interpretation |
|----------|----------|------------|----------------|
| Features | ~85% | HIGH | Very reliable estimate |
| Stories | ~70% | MEDIUM | Probably accurate |
| Code trace | ~60% | LOW | Needs verification |
```

### In Executive Summary

```markdown
## Coverage Estimates

**Overall:** ~75% (+/- 15% margin)

| Confidence | Gaps | Action |
|------------|------|--------|
| HIGH | 5 | Address immediately |
| MEDIUM | 8 | Review carefully |
| LOW | 12 | Verify before acting |
```

---

## Human Action Matrix

| Confidence | Finding Type | Human Action |
|------------|--------------|--------------|
| HIGH | Gap | Brief verification, then fix |
| HIGH | Match | Accept as valid |
| MEDIUM | Gap | Review context, verify |
| MEDIUM | Match | Verify alignment |
| LOW | Gap | Thorough investigation |
| LOW | Match | Do not rely without verification |

---

## Quality Requirements

### All Reports Must Include

1. **Confidence level** for every finding
2. **Reasoning** for assigned level
3. **Human action** recommendation
4. **HITL disclaimer** - no action without verification

### Never Do

- Claim 100% confidence
- Skip confidence assignment
- Act on LOW confidence without verification
- Upgrade semantic matches to HIGH

---

## Integration Notes

**For validators:**
- Assign confidence using this scoring guide
- Document factors contributing to score
- Default to lower confidence when uncertain

**For router:**
- Aggregate confidence across validators
- Present confidence distribution in summary
- Highlight findings needing most attention
