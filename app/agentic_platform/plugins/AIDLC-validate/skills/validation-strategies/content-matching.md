# Content Matching Strategies

Strategies for comparing source requirements against target artifacts.

## Overview

Content matching determines how to find source requirements in target documents/artifacts. Use the most precise strategy available, falling back to less precise as needed.

---

## Strategy 1: Exact ID Match

**Confidence Level:** HIGH

**Use When:** Documents have explicit IDs (BRD-001, F-001, US-001)

### Algorithm

```
1. Extract ID from source requirement
2. Search target for exact ID string
3. Return HIGH confidence if found
```

### Implementation

```bash
# Search for requirement ID
Grep: "BRD-001", "REQ-001", "F-001", "US-001"

# Common ID patterns
- BRD-{number}
- REQ-{number}
- F-{number} or FEAT-{number}
- US-{number} or STORY-{number}
- AC-{number}
```

### Example

```markdown
Source: "BRD-001: User Authentication"
Target search: grep "BRD-001"
Result: Found in EPIC.md line 45
Confidence: HIGH
```

---

## Strategy 2: Term-Based Match

**Confidence Level:** HIGH (90%+), MEDIUM (60-89%), LOW (<60%)

**Use When:** No explicit IDs, but named requirements

### Algorithm

```
1. Extract key terms from requirement
2. Remove stop words
3. Search target for term combinations
4. Calculate overlap percentage
5. Assign confidence based on overlap
```

### Stop Words to Remove

```
the, a, an, is, are, was, were, be, been, being,
have, has, had, do, does, did, will, would, could,
should, may, might, must, shall, can, need, ought,
of, in, to, for, with, on, at, by, from, as, into,
through, during, before, after, above, below, between,
this, that, these, those, it, its, and, or, but, if,
then, else, when, where, why, how, all, each, every,
both, few, more, most, other, some, such, no, not,
only, own, same, so, than, too, very, just, also
```

### Scoring

| Term Overlap | Confidence |
|--------------|------------|
| 90% or more | HIGH |
| 60% - 89% | MEDIUM |
| Below 60% | LOW |

### Example

```markdown
Source: "System shall support automated email notifications"
Key Terms: ["automated", "email", "notifications"]
Stop Words Removed: ["System", "shall", "support"]

Target Search:
- "automated" found: YES
- "email" found: YES
- "notifications" found: YES

Overlap: 3/3 = 100%
Confidence: HIGH
```

---

## Strategy 3: Semantic Match

**Confidence Level:** MEDIUM (never HIGH for semantic)

**Use When:** Paraphrased content, different terminology

### Algorithm

```
1. Identify core concept from requirement
2. Look up synonyms for key terms
3. Search target for concept or synonyms
4. Verify context alignment
```

### Common Synonym Mappings

| Term | Synonyms |
|------|----------|
| authenticate | login, sign in, verify identity, verify credentials |
| authorize | grant access, permit, allow |
| create | add, new, generate, insert |
| read | get, retrieve, fetch, view, display |
| update | edit, modify, change, alter |
| delete | remove, eliminate, destroy, drop |
| display | show, render, present, output |
| calculate | compute, process, evaluate, determine |
| validate | verify, check, confirm, ensure |
| submit | send, post, transmit |
| store | save, persist, record |
| search | find, lookup, query, filter |
| export | download, extract, output |
| import | upload, load, ingest |
| notify | alert, inform, message, email |

### Example

```markdown
Source: "User can authenticate with credentials"
Core Concept: User authentication
Synonyms: ["login", "sign in", "verify credentials"]

Target Search:
- "authenticate" not found
- "login" found in section 3.2
- Context: User login functionality

Result: Semantic match found
Confidence: MEDIUM (semantic matches never HIGH)
```

---

## Strategy 4: Hierarchical Match

**Confidence Level:** Varies based on coverage

**Use When:** Nested or decomposed requirements

### Algorithm

```
1. Identify parent requirement
2. Find all child artifacts
3. Check if children collectively cover parent
4. Calculate coverage percentage
5. Report partial coverage as gap
```

### Example

```markdown
Parent Feature: "User Management"
Expected Coverage: Create, Read, Update, Delete users

Child Stories Found:
- US-001: Create User (covers 25%)
- US-002: Edit User (covers 25%)
- US-003: Delete User (covers 25%)
- List Users: NOT FOUND (missing 25%)

Total Coverage: 75%
Gap: "View/List Users" functionality not found
Confidence: HIGH (explicit missing functionality)
```

---

## Fallback Strategy

When all strategies fail to find a match:

```markdown
1. Document all search attempts
2. List all search terms used
3. List all targets searched
4. Report as potential gap with LOW confidence
5. Flag for human review

Example Gap Report:
"Requirement XYZ not found after:
- Exact ID search: No matches
- Term search (terms: a, b, c): No matches
- Semantic search (synonyms: x, y, z): No matches
Confidence: LOW - may exist under different terminology
Requires human verification"
```

---

## Best Practices

### DO

- Start with most precise strategy (Exact ID)
- Document which strategy produced the match
- Include search terms in gap reports
- Use context to validate semantic matches

### DON'T

- Assign HIGH confidence to semantic matches
- Skip strategies - apply all before reporting gap
- Match without verifying context
- Claim certainty for content-only matches

---

## Strategy Selection Matrix

| Scenario | Primary Strategy | Fallback |
|----------|-----------------|----------|
| IDs present | Exact ID | Term-based |
| Named items, no IDs | Term-based | Semantic |
| Paraphrased content | Semantic | Term-based |
| Nested requirements | Hierarchical | Term-based |
| Code traceability | Exact ID → Filename → Content | All |

---

## Integration with Validators

Validators should:

1. **Load this skill** when starting validation
2. **Apply strategies in order** (Exact → Term → Semantic → Hierarchical)
3. **Record match strategy** in findings
4. **Report confidence** based on strategy used
5. **Document search terms** for transparency
