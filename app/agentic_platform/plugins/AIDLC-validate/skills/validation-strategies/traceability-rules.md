# Traceability Rules

Rules for building and validating requirements traceability across SDLC stages.

## Overview

Traceability connects requirements from source documents through implementation, enabling impact analysis and coverage verification.

---

## Traceability Chain

### Full Chain Structure

```
BRD Requirement
    └── EPIC Capability
        └── Feature
            └── User Story
                └── Code File
                    └── Test File
```

### Chain Levels

| Level | From | To | Validator |
|-------|------|-----|-----------|
| 1 | BRD | EPIC | epic-coverage-validator |
| 2 | PRD/EPIC | Features | feature-coverage-validator |
| 3 | Features | Stories | story-coverage-validator |
| 4 | Stories | Code | traceability-analyzer |
| 5 | Stories | Tests | (optional) |

---

## Traceability Matrix

### Matrix Template

```markdown
| Source Req | EPIC | Feature | Story | Code | Test | Status |
|------------|------|---------|-------|------|------|--------|
| BRD-001 | C-01 | F-001 | US-001 | auth.ts | auth.test.ts | Complete |
| BRD-002 | C-02 | F-002 | US-002 | - | - | Partial |
| BRD-003 | C-03 | - | - | - | - | Gap |
```

### Status Indicators

| Symbol | Meaning | Action Required |
|--------|---------|-----------------|
| ID (e.g., F-001) | Traced with identifier | None |
| Checkmark symbol | Traced without specific ID | None |
| Warning symbol | Partial trace | Review |
| X symbol | Not traced | Create/fix |
| Question symbol | Unknown/unclear | Investigate |
| Dash symbol | Not applicable at this level | Skip |

### Visual Status Summary

```markdown
Legend:
- F-001, US-001: Traced with confidence (identifier found)
- Checkmark: Traced (content match)
- Warning: Partial/unclear trace
- X: Not traced (gap)
- ?: Unknown status
- -: Not applicable
```

---

## Traceability Rules by Level

### Level 1: BRD → EPIC

```markdown
Rule TR-01: Each BRD capability should have EPIC capability
- Source: BRD capability statements
- Target: EPIC capabilities section
- Match: ID or term-based
- Gap: BRD capability not in EPIC
```

### Level 2: EPIC/PRD → Features

```markdown
Rule TR-02: Each EPIC capability/PRD feature should have Feature document
- Source: EPIC capabilities or PRD features
- Target: Feature documents
- Match: Feature ID or name
- Gap: No feature document for requirement
```

### Level 3: Features → Stories

```markdown
Rule TR-03: Each Feature should have User Stories
- Source: Feature documents
- Target: User Stories
- Match: Feature reference in story or keyword match
- Gap: Feature without stories
```

### Level 4: Stories → Code

```markdown
Rule TR-04: Each Story should trace to code (best effort)
- Source: User Stories
- Target: Source code files
- Match: Story ID in comment, filename, or keywords
- Gap: Story without code reference
- Note: Inherently less reliable
```

---

## Matching Rules for Code Traceability

### Priority 1: Explicit Reference (HIGH Confidence)

Look for story IDs in code:

```typescript
// Implements: US-001
export class AuthService { }

/**
 * @story US-002
 * @description Handles password reset flow
 */
function resetPassword() { }

/* Story: US-003 - User Profile Management */
class ProfileComponent { }
```

**Patterns to Search:**
- `// Implements: {story_id}`
- `// Story: {story_id}`
- `/* {story_id} */`
- `@story {story_id}`
- `{story_id}:` in comments

### Priority 2: Filename Convention (MEDIUM Confidence)

Check filenames for story references:

```
# Direct ID in filename
us-001-login.ts
US001LoginComponent.ts
story_001_auth.py

# Feature-based naming
F001-user-registration/
  registration.component.ts
  registration.service.ts
```

**Patterns to Search:**
- `**/us-{id}*`
- `**/US{id}*`
- `**/story-{id}*`
- `**/story_{id}*`

### Priority 3: Content Keywords (LOW Confidence)

Search code for story keywords:

```markdown
Story: "US-001: User can login with email and password"
Keywords: ["login", "email", "password", "authenticate"]

Search:
- Function names: loginUser, authenticateWithEmail
- Class names: LoginService, AuthenticationHandler
- Variable names: userEmail, passwordHash
```

**Warning:** Content matching has high false positive rate.

---

## Bidirectional Traceability

### Forward Traceability (Validation)

```
BRD → EPIC → Features → Stories → Code
"Does implementation cover all requirements?"
```

### Backward Traceability (Impact Analysis)

```
Code → Stories → Features → EPIC → BRD
"What requirements does this code implement?"
```

### Matrix Support for Both Directions

```markdown
| BRD Req | ... | Code File | Forward | Backward |
|---------|-----|-----------|---------|----------|
| BRD-001 | ... | auth.ts | Complete | Verified |
| BRD-002 | ... | - | Gap | N/A |
```

---

## Coverage Calculation

### Per-Level Coverage

```
Level Coverage = (Traced Items / Total Source Items) * 100

Example:
- BRD Requirements: 10
- Traced to EPIC: 9
- EPIC Coverage: 90%
```

### Chain Coverage

```
Chain Coverage = min(Level1%, Level2%, Level3%, Level4%)

Example:
- Level 1 (BRD→EPIC): 90%
- Level 2 (EPIC→Feature): 85%
- Level 3 (Feature→Story): 80%
- Level 4 (Story→Code): 70%
- Chain Coverage: 70% (bottleneck at code)
```

### Coverage with Confidence Weights

```markdown
Weighted Coverage:
- HIGH confidence traces: count as 100%
- MEDIUM confidence traces: count as 75%
- LOW confidence traces: count as 50%

Example:
- 5 HIGH traces (5 * 1.0 = 5.0)
- 3 MEDIUM traces (3 * 0.75 = 2.25)
- 2 LOW traces (2 * 0.5 = 1.0)
- Total weighted: 8.25 / 10 = 82.5%
```

---

## Gap Propagation

### Upstream Gap Impact

When a gap exists at one level, all downstream levels are affected:

```markdown
Gap at Feature level (F-003 missing)
    → All stories for F-003: Not applicable
    → All code for F-003 stories: Not applicable

Report as:
"F-003 missing - downstream validation not possible"
```

### Reporting Propagated Gaps

```markdown
## Propagated Gaps

| Source Gap | Affected Downstream |
|------------|---------------------|
| GAP-FC-001 (F-003) | Stories, Code |
| GAP-SC-005 (US-010) | Code |
```

---

## Full Chain Report Template

```markdown
# Full Chain Traceability Report

Generated: {timestamp}

## Chain Summary

| Level | From | To | Coverage | Confidence |
|-------|------|-----|----------|------------|
| 1 | BRD | EPIC | ~90% | HIGH |
| 2 | EPIC | Features | ~85% | HIGH |
| 3 | Features | Stories | ~80% | MEDIUM |
| 4 | Stories | Code | ~70% | LOW |

**End-to-End Coverage:** ~70%

## Traceability Matrix

| BRD Req | EPIC | Feature | Story | Code | Status |
|---------|------|---------|-------|------|--------|
| BRD-001 | C-01 | F-001 | US-001 | auth.ts | Complete |
| BRD-002 | C-02 | F-002 | US-002, US-003 | login.ts | Complete |
| BRD-003 | C-03 | F-003 | - | - | Gap at Feature |
| BRD-004 | C-04 | F-004 | US-004 | - | Gap at Code |

## Gap Analysis

### Level 1 Gaps (BRD → EPIC)
{gaps}

### Level 2 Gaps (EPIC → Features)
{gaps}

### Level 3 Gaps (Features → Stories)
{gaps}

### Level 4 Gaps (Stories → Code)
{gaps}

## Bottleneck Analysis

**Weakest Link:** Story → Code (70%)
**Recommendation:** Improve code traceability markers

## Human Review Required

Full chain traceability requires verification at each level.
Code traceability (Level 4) is particularly unreliable.
```

---

## Integration with Validators

### Router Coordination

1. Router determines if full chain or single level
2. For full chain, invoke validators sequentially
3. Pass upstream results to downstream validators
4. Aggregate all results into traceability matrix
5. Calculate chain coverage

### Validator Handoff

```yaml
# Context from router to validators
traceabilityContext:
  chainLevel: 3  # This validator's level
  upstreamResults:
    level1: {coverage: 90%, gaps: [...]}
    level2: {coverage: 85%, gaps: [...]}
  propagatedGaps: [...]  # Gaps from upstream
```
