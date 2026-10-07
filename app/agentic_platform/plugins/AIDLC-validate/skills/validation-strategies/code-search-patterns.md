# Code Search Patterns for Traceability

Patterns and strategies for finding user story references in source code.

## Overview

Code traceability relies on finding story IDs or keywords in source code. This skill defines the search patterns used by traceability-analyzer to match stories to code with appropriate confidence levels.

---

## Three-Strategy Approach

Apply strategies in priority order:

| Priority | Strategy | Confidence | Description |
|----------|----------|------------|-------------|
| 1 | Explicit Reference | HIGH | Story ID in code comments |
| 2 | Filename Convention | MEDIUM | Story ID in file/folder names |
| 3 | Content Keywords | LOW | Story keywords in code |

**ALWAYS apply all 3 strategies before reporting a gap.**

---

## Strategy 1: Explicit Reference (HIGH Confidence)

### What to Search

Look for story IDs in:
- Single-line comments
- Multi-line comments
- JSDoc/docstring annotations
- Inline comments

### Search Patterns

```bash
# Story ID patterns
Grep: "US-001", "US001", "Story-001", "STORY-001"

# Common annotations
Grep: "Implements:", "@story", "Story:", "@implements"

# Combined patterns
Grep: "Implements:.*US-\d+", "@story\s+US-\d+"
```

### Match Examples

**TypeScript/JavaScript:**
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

// US-004: Export functionality
export function exportData() { }
```

**Python:**
```python
# Implements: US-001
class AuthService:
    pass

def reset_password():
    """
    @story US-002
    Handles password reset flow
    """
    pass

# Story: US-003
class ProfileManager:
    pass
```

**Java:**
```java
// Implements: US-001
public class AuthService { }

/**
 * @story US-002
 */
public void resetPassword() { }

/* Story: US-003 - User Profile */
public class ProfileComponent { }
```

### Confidence Assignment

- **HIGH:** Story ID found in comment directly above or inside function/class
- **HIGH:** JSDoc/docstring annotation with story ID
- **MEDIUM:** Story ID found elsewhere in file but not clearly associated

---

## Strategy 2: Filename Convention (MEDIUM Confidence)

### What to Search

Look for story references in:
- File names
- Directory names
- Module names

### Search Patterns

```bash
# Exact ID in filename
Glob: **/us-001*.*, **/US-001*.*, **/story-001*.*

# ID in directory
Glob: **/us-001/**/*.*, **/US001/**/*.*

# Underscore/hyphen variations
Glob: **/us_001*.*, **/story_001*.*, **/US-001-*.*, **/story-001-*.*
```

### Match Examples

**Direct ID:**
```
src/features/us-001-login/login.component.ts
src/components/US001LoginComponent.ts
handlers/story_001_auth.py
```

**Directory-based:**
```
features/us-001/
  ├── login.component.ts
  ├── login.service.ts
  └── login.spec.ts

stories/US-002-password-reset/
  ├── reset.handler.ts
  └── reset.test.ts
```

**Partial match:**
```
us001-auth-service.ts  (US-001)
story_002_reset.py      (US-002)
```

### Confidence Assignment

- **MEDIUM:** Story ID in filename or direct parent directory
- **LOW:** Story ID in ancestor directory (more than 1 level up)

---

## Strategy 3: Content Keywords (LOW Confidence)

### What to Search

Look for story keywords in:
- Function names
- Class names
- Variable names
- Method names

### Search Process

```
1. Extract keywords from story title
   Story: "User can reset password"
   Keywords: ["user", "reset", "password"]

2. Search code for keywords in identifiers
   Grep: "function.*reset.*password", "class.*Password.*Reset"

3. Check keyword density
   Found: function resetPassword()
   Match: 2/3 keywords in identifier

4. Assign confidence based on keyword overlap
   66% overlap → LOW confidence
```

### Match Examples

**Story:** "US-001: User can login with email"
**Keywords:** login, email, authentication

```typescript
// Potential matches (LOW confidence)
function loginWithEmail() { }           // Keywords: login, email (2/3)
class EmailAuthenticationService { }    // Keywords: email, authentication (2/3)
const userLogin = () => { }             // Keywords: login (1/3)
```

**Story:** "US-002: Export data to CSV"
**Keywords:** export, data, CSV

```typescript
// Potential matches (LOW confidence)
function exportToCSV() { }              // Keywords: export, CSV (2/3)
class DataExporter { }                  // Keywords: data, export (2/3)
export const csvFormat = () => { }      // Keywords: CSV, export (2/3)
```

### Confidence Assignment

- **LOW:** 50%+ keyword overlap in identifier name
- **VERY LOW:** <50% keyword overlap (too unreliable to report)

### False Positive Risk

Content matching has HIGH false positive rate:
- Generic terms match unrelated code (e.g., "user" matches everywhere)
- Keywords may be part of different feature
- Coincidental naming

**WARNING:** Never assign HIGH or MEDIUM confidence to content-only matches.

---

## Exclusions and Filters

### Directories to Exclude

```bash
node_modules/
.git/
dist/
build/
out/
target/
__pycache__/
.pytest_cache/
coverage/
.next/
vendor/
```

### File Types to Scan

**Include:**
```
*.ts, *.tsx      # TypeScript
*.js, *.jsx      # JavaScript
*.py             # Python
*.java           # Java
*.cs             # C#
*.go             # Go
*.rb             # Ruby
*.php            # PHP
*.cpp, *.h       # C++
*.swift          # Swift
*.kt             # Kotlin
```

**Exclude:**
```
*.json           # Config files
*.md             # Documentation
*.test.*, *.spec.*  # Test files (optional)
*.min.js         # Minified files
*.lock           # Lock files
```

---

## Search Workflow

### Step-by-Step Process

```
For each User Story:

1. Extract story ID and title
   Story: US-001 - User can login

2. Strategy 1: Explicit Reference
   Grep code for "US-001"
   If found → Mark as HIGH confidence, done
   If not → Continue to Strategy 2

3. Strategy 2: Filename Convention
   Glob for "**/us-001*.*"
   If found → Mark as MEDIUM confidence, done
   If not → Continue to Strategy 3

4. Strategy 3: Content Keywords
   Extract keywords: ["login", "user"]
   Grep for "login.*user" or "user.*login" in identifiers
   If found with 50%+ overlap → Mark as LOW confidence
   If not → Report as GAP

5. Document search performed
   Record: strategies tried, patterns searched, results found
```

---

## Gap Reporting

### When No Match Found

Document all search attempts:

```markdown
**GAP-TC-001: US-001 - User Login** - Confidence: HIGH

- **Story:** US-001 - User can login with email
- **Search Strategies Applied:**
  1. **Explicit reference:** Searched for "US-001", "US001", "@story US-001" - No matches
  2. **Filename convention:** Globbed for "**/us-001*.*", "**/us_001*.*" - No matches
  3. **Content keywords:** Searched for "login" + "email" in identifiers - No matches
- **Code Paths Searched:** src/**/*.ts, src/**/*.js
- **Files Scanned:** 247 files
- **Result:** No traceability found
- **Confidence:** HIGH (exhaustive search performed)
- **Recommendation:** Verify implementation status with development team
```

---

## Integration with Traceability-Analyzer

The traceability-analyzer agent uses these patterns:

```markdown
### Matching Process

See `skills/validation-strategies/code-search-patterns.md` for detailed search patterns.

**Summary:**
1. Apply Strategy 1 (explicit) → HIGH confidence
2. If no match, apply Strategy 2 (filename) → MEDIUM confidence
3. If no match, apply Strategy 3 (content) → LOW confidence
4. If no match, report gap with all strategies documented
```

---

## Best Practices

### DO
- Apply all 3 strategies before reporting gap
- Document search patterns used
- Use conservative confidence levels
- Acknowledge false positive risk
- Include HITL verification guidance

### DON'T
- Skip strategies (always exhaustive)
- Assign HIGH confidence to filename matches
- Assign MEDIUM/HIGH to content matches
- Report gaps without documenting search
- Claim high accuracy for code traceability

---

## Accuracy Expectations

| Strategy | Accuracy | False Positives | False Negatives |
|----------|----------|-----------------|-----------------|
| Explicit | ~90% | Low | Medium (if IDs not used) |
| Filename | ~70% | Medium | Medium |
| Content | ~50% | High | High |

**Overall code traceability accuracy: ~60-70%**

---

## Improvement Recommendations

To improve traceability, developers should:

1. **Add story IDs to code comments:**
   ```typescript
   // Implements: US-001
   export class AuthService { }
   ```

2. **Use filename conventions:**
   ```
   features/us-001-login/
   ```

3. **Use JSDoc annotations:**
   ```typescript
   /**
    * @story US-002
    * @description Password reset functionality
    */
   ```

4. **Document in PR descriptions:**
   ```
   Implements: US-001, US-002
   ```
