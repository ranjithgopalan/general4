# Gap Reporting Format

Standard format for reporting potential gaps in requirements coverage.

## Overview

Gap reports must be evidence-based, actionable, and include human verification guidance. Every gap needs complete documentation of what was searched and what was found.

---

## Gap Entry Template

### Standard Format

```markdown
**GAP-{ID}: {Brief Title}** - Confidence: {HIGH|MEDIUM|LOW}

- **Source:** {document path}, Page {n}, Section {x.y}
- **Requirement:** "{exact text from source document}"
- **Type:** {Capability | Feature | AC | Story | Traceability}
- **Search Strategy:** {exact | term-based | semantic | hierarchical}
- **Search Terms:** {terms used in search}
- **Targets Searched:** {list of documents/files searched}
- **Result:** {no match | partial match | ambiguous}
- **Evidence:** {what was/wasn't found during search}
- **Impact:** {Critical | High | Medium | Low}
- **Recommendation:** {specific action to take}
- **Human Action Required:** {verification instruction}
```

### Minimal Format (for tables)

```markdown
| Gap ID | Title | Source | Confidence | Impact | Recommendation |
|--------|-------|--------|------------|--------|----------------|
| GAP-001 | Missing Feature | BRD p.5 | HIGH | Critical | Create Feature doc |
```

---

## Gap ID Convention

Format: `GAP-{LEVEL}-{NUMBER}`

| Level Code | Validation Type |
|------------|-----------------|
| EC | EPIC Coverage |
| FC | Feature Coverage |
| SC | Story Coverage |
| TC | Traceability (Code) |
| CH | Chain (Full) |

Examples:
- `GAP-EC-001` - First EPIC coverage gap
- `GAP-FC-003` - Third Feature coverage gap
- `GAP-SC-015` - Fifteenth Story coverage gap
- `GAP-TC-002` - Second Traceability gap

---

## Impact Assessment

### Impact Levels

| Level | Criteria | Example |
|-------|----------|---------|
| **Critical** | Core capability missing, blocks release | "User authentication missing" |
| **High** | Important feature missing, degrades value | "Password reset missing" |
| **Medium** | Useful feature missing, workaround exists | "Export to PDF missing" |
| **Low** | Nice-to-have missing, minimal impact | "Dark mode missing" |

### Impact Assignment Guidelines

```markdown
Critical:
- Security-related functionality
- Core business workflow
- Regulatory requirement
- No workaround possible

High:
- Major user-facing feature
- Integration dependency
- Performance requirement
- Difficult workaround

Medium:
- Secondary feature
- Enhancement to core
- Workaround available
- User convenience

Low:
- Polish feature
- Optional enhancement
- Easy workaround
- Aesthetic improvement
```

---

## Evidence Requirements

### What to Include

Every gap must document:

1. **Source Citation**
   - Document path
   - Page number (if applicable)
   - Section number/title
   - Line number (if applicable)

2. **Exact Requirement Text**
   - Quote directly from source
   - Use quotation marks
   - Keep context

3. **Search Documentation**
   - Strategy used (exact, term-based, semantic)
   - Search terms tried
   - All locations searched
   - What was found (partial matches)

4. **Result Explanation**
   - Why no match was found
   - Closest matches found
   - Why partial matches don't satisfy

### Example with Full Evidence

```markdown
**GAP-FC-001: Missing User Profile Feature** - Confidence: HIGH

- **Source:** PRD.pdf, Page 12, Section 4.3 "User Features"
- **Requirement:** "F-004: Users shall be able to view and edit their profile information including name, email, and preferences"
- **Type:** Feature
- **Search Strategy:** Exact ID, then term-based
- **Search Terms:** "F-004", "profile", "user profile", "edit profile", "preferences"
- **Targets Searched:**
  - features/F-001-registration.md
  - features/F-002-login.md
  - features/F-003-password-reset.md
  - features/*.md (all 6 files)
- **Result:** No match found
- **Evidence:**
  - "F-004" not found in any feature document
  - "profile" found in F-001 but context is registration, not management
  - "preferences" not found
- **Impact:** High - Core user functionality
- **Recommendation:** Create feature document `features/F-004-user-profile.md`
- **Human Action Required:** Verify F-004 is indeed required and not intentionally deferred
```

---

## Grouping Gaps in Reports

### Primary Grouping: By Confidence

```markdown
## Potential Gaps (Requires Verification)

### HIGH Confidence Gaps (5)
{Most likely real gaps - brief verification}

### MEDIUM Confidence Gaps (8)
{Probably gaps - careful review}

### LOW Confidence Gaps (12)
{May or may not be gaps - thorough verification}
```

### Secondary Grouping: By Impact

Within each confidence level:

```markdown
### HIGH Confidence Gaps (5)

#### Critical Impact (1)
{gaps}

#### High Impact (2)
{gaps}

#### Medium Impact (2)
{gaps}
```

### Alternative: By Category

```markdown
### Feature Gaps
{all feature-related gaps}

### Acceptance Criteria Gaps
{all AC-related gaps}

### Traceability Gaps
{all code traceability gaps}
```

---

## Gap Summary Table

Include at start of report:

```markdown
## Gap Summary

| Confidence | Count | Action Required |
|------------|-------|-----------------|
| HIGH | 5 | Address immediately |
| MEDIUM | 8 | Review carefully |
| LOW | 12 | Verify before acting |
| **Total** | **25** | **Human verification required** |

### By Category

| Category | Gaps | Most Critical |
|----------|------|---------------|
| Features | 3 | GAP-FC-001 (Critical) |
| Stories | 8 | GAP-SC-003 (High) |
| Traceability | 14 | GAP-TC-001 (Medium) |
```

---

## Human Verification Guidance

### Include in Every Gap

```markdown
- **Human Action Required:** {specific verification step}
```

### Standard Verification Instructions

| Confidence | Verification Instruction |
|------------|-------------------------|
| HIGH | "Verify this is a real gap, then address" |
| MEDIUM | "Review carefully - may be covered differently" |
| LOW | "Thorough investigation needed - may not be a gap" |

### Section-Level Disclaimer

```markdown
### Human Review Required

All gaps identified in this report require human verification before action.

**Reasons for verification:**
- AI matching has 20-30% error rate
- Requirements may be paraphrased
- Coverage may exist in different location
- Items may be intentionally deferred

**Do NOT:**
- Auto-fix gaps without verification
- Assume all gaps are real
- Skip LOW confidence gaps
```

---

## Recommendations Format

### Gap-Level Recommendation

```markdown
- **Recommendation:** Create feature document F-004-user-profile.md covering profile view/edit functionality
```

### Report-Level Recommendations

```markdown
## Recommendations

### Immediate Actions (HIGH confidence gaps)
1. Create missing Feature F-004 (GAP-FC-001)
2. Add missing AC to US-003 (GAP-SC-005)

### Review Required (MEDIUM confidence)
1. Verify story coverage for F-002 (GAP-SC-002)
2. Check if traceability exists in alternate location (GAP-TC-003)

### Optional Verification (LOW confidence)
1. Investigate implicit coverage for capability X (GAP-EC-010)
2. Check with team about deferred feature Y (GAP-FC-015)
```

---

## Integration with Validators

### Validator Responsibilities

1. Use this template for all gaps
2. Include complete evidence
3. Assign appropriate confidence
4. Provide actionable recommendations
5. Add human verification instruction

### Router Responsibilities

1. Aggregate gaps from validators
2. Deduplicate overlapping gaps
3. Sort by confidence then impact
4. Generate summary statistics
5. Include master disclaimer
