---
name: rally-hierarchy
description: Fetches and formats Rally portfolio hierarchy (Epic→Capability→Feature) for BRD traceability matrices. Wraps Rally API to provide hierarchy-specific queries with validation.
license: Proprietary
compatibility: Requires rally-api skill from AIDLC-scrum-master plugin. Rally API access configured via ~/.claude/aig.json.
metadata:
  author: ADLC Business Analyst Team
  version: "1.0.0"
  organization: AIG
  plugin: AIDLC-business-analyst
allowed-tools: Skill Read
---

# Rally Hierarchy Skill

Use this skill to fetch Rally portfolio hierarchy data (Epic→Capability→Feature) for inclusion in BRD traceability matrices.

---

## Purpose

This skill provides a bridge between the BRD generation system and the Rally API, specifically for retrieving and formatting portfolio hierarchy data. It wraps the rally-api skill to provide:

1. Epic→Capability→Feature hierarchy retrieval
2. Parent-child relationship validation
3. Hierarchy completeness checks
4. BRD-friendly formatting

---

## When to Use This Skill

Use this skill when:
- Generating BRD traceability matrices (Section 15)
- User provides an Epic ID for requirement traceability
- You need to validate Epic→Capability→Feature relationships
- Building portfolio hierarchy tables for documentation

**Do NOT use this skill for:**
- Creating or modifying Rally items (use rally-api skill directly)
- User story management (use rally-api skill directly)
- Sprint planning (use rally-api skill directly)
- General Rally queries outside of hierarchy traceability

---

## How to Use This Skill

### Step 1: Invoke the Skill

```markdown
Use Skill tool: Skill(skill: "business-analyst:rally-hierarchy")
```

### Step 2: Provide Epic ID

When invoked, provide the Epic FormattedID (e.g., "E12345", "E67890").

### Step 3: Receive Hierarchy Data

The skill returns a JSON structure with complete hierarchy:

```json
{
  "epic_id": "E12345",
  "epic_name": "Customer Authentication System",
  "epic_state": "In Progress",
  "hierarchy": [
    {
      "level": "Epic",
      "formatted_id": "E12345",
      "name": "Customer Authentication System",
      "parent_id": null,
      "parent_name": null,
      "state": "In Progress",
      "validation_status": "Valid"
    },
    {
      "level": "Capability",
      "formatted_id": "C67890",
      "name": "User Login and Session Management",
      "parent_id": "E12345",
      "parent_name": "Customer Authentication System",
      "state": "Active",
      "validation_status": "Valid"
    },
    {
      "level": "Feature",
      "formatted_id": "F11111",
      "name": "OAuth2 Authentication",
      "parent_id": "C67890",
      "parent_name": "User Login and Session Management",
      "state": "Planning",
      "validation_status": "Valid"
    }
  ],
  "validation_summary": {
    "complete": true,
    "validated_at": "2026-02-03T10:35:00Z",
    "all_features_linked": true,
    "total_items": 15,
    "epic_count": 1,
    "capability_count": 3,
    "feature_count": 11,
    "validation_errors": []
  }
}
```

---

## Implementation Instructions

When this skill is invoked, follow these steps:

### Phase 1: Validate Input

1. Check that Epic ID is provided
2. Validate Epic ID format (starts with "E", followed by digits)
3. If invalid: Return error with helpful message

### Phase 2: Invoke Rally API Skill

1. Use Skill tool to invoke rally-api:
   ```markdown
   Skill(skill: "scrum-master:rally-api")
   ```

2. Request Epic details:
   ```
   Find Epic by FormattedID: {epic_id}
   Include fields: FormattedID, Name, State, Children, _ref
   ```

3. If Epic not found: Return error with clear message

### Phase 3: Fetch Capabilities

1. Use rally-api to get Capabilities under the Epic:
   ```
   Get children of Epic {epic_ref}
   Child type: portfolioitem/capability
   Include fields: FormattedID, Name, State, Parent, Children, _ref
   ```

2. Store all Capabilities with parent reference to Epic

### Phase 4: Fetch Features

1. For each Capability, use rally-api to get Features:
   ```
   Get children of Capability {capability_ref}
   Child type: portfolioitem/feature
   Include fields: FormattedID, Name, State, Parent, PlannedEndDate, _ref
   ```

2. Store all Features with parent reference to Capability

### Phase 5: Validate Hierarchy

1. For each Feature, validate parent-child chain:
   ```
   Use rally-api validate_hierarchy method:
   Validate Feature {feature_ref}
   Verify: Feature → Capability → Epic chain complete
   ```

2. Mark validation status:
   - "Valid": Complete parent-child chain
   - "Incomplete": Missing parent reference
   - "Invalid": Parent reference broken or incorrect

3. Collect validation errors for reporting

### Phase 6: Format Hierarchy Data

1. Build hierarchy array with structure:
   - Level 1: Epic (parent_id = null)
   - Level 2: Capabilities (parent_id = Epic FormattedID)
   - Level 3: Features (parent_id = Capability FormattedID)

2. Sort hierarchy:
   - First by level (Epic → Capability → Feature)
   - Then alphabetically by FormattedID within each level

3. Include for each item:
   - level (Epic/Capability/Feature)
   - formatted_id (E####/C####/F####)
   - name (item name from Rally)
   - parent_id (FormattedID of parent, or null for Epic)
   - parent_name (name of parent, or null for Epic)
   - state (Rally state field)
   - validation_status (Valid/Incomplete/Invalid)

### Phase 7: Build Validation Summary

1. Count items by level (epic_count, capability_count, feature_count)
2. Check completeness:
   - all_features_linked: true if all Features have valid Capability parent
   - complete: true if entire hierarchy is valid
3. Collect validation_errors array with any issues found
4. Add timestamp: validated_at

### Phase 8: Return JSON Response

Return the complete hierarchy data structure as shown in Step 3 above.

---

## Error Handling

### Epic Not Found

**Error Message:**
```json
{
  "error": "Epic not found",
  "epic_id": "E12345",
  "message": "Epic with FormattedID 'E12345' could not be found in Rally. Please verify the Epic ID and try again.",
  "suggestion": "Check the Epic ID in Rally UI or use /fesm-query to search for the Epic."
}
```

**Action:** Return error to caller, who should skip Rally traceability and log warning.

### Rally API Unavailable

**Error Message:**
```json
{
  "error": "Rally API unavailable",
  "message": "Could not connect to Rally API. Please verify your Rally API key is configured in ~/.claude/aig.json.",
  "suggestion": "Run /fesm-install to configure Rally API access."
}
```

**Action:** Return error to caller, who should skip Rally traceability and log warning.

### Incomplete Hierarchy

**Warning Response:**
```json
{
  "epic_id": "E12345",
  "hierarchy": [...],
  "validation_summary": {
    "complete": false,
    "all_features_linked": false,
    "validation_errors": [
      "Feature F11115 has no Capability parent",
      "Capability C67895 has no Epic parent"
    ]
  }
}
```

**Action:** Return data with warnings. Caller should include validation warnings in traceability matrix.

### No Capabilities or Features Found

**Response:**
```json
{
  "epic_id": "E12345",
  "epic_name": "Customer Authentication System",
  "hierarchy": [
    {
      "level": "Epic",
      "formatted_id": "E12345",
      "name": "Customer Authentication System",
      "parent_id": null,
      "parent_name": null,
      "state": "Planning",
      "validation_status": "Valid"
    }
  ],
  "validation_summary": {
    "complete": true,
    "all_features_linked": true,
    "total_items": 1,
    "epic_count": 1,
    "capability_count": 0,
    "feature_count": 0,
    "validation_errors": [],
    "note": "Epic has no Capabilities or Features yet."
  }
}
```

**Action:** Return Epic-only hierarchy. Caller should note in traceability matrix that hierarchy is incomplete.

---

## Usage Examples

### Example 1: Complete Hierarchy

**Input:**
```
Epic ID: E12345
```

**Rally API Calls:**
1. Find Epic E12345 → Success
2. Get Capabilities under E12345 → Found 2 (C67890, C67891)
3. Get Features under C67890 → Found 2 (F11111, F11112)
4. Get Features under C67891 → Found 1 (F11113)
5. Validate each Feature → All valid

**Output:**
```json
{
  "epic_id": "E12345",
  "epic_name": "Customer Authentication System",
  "hierarchy": [
    {"level": "Epic", "formatted_id": "E12345", "name": "Customer Authentication System", ...},
    {"level": "Capability", "formatted_id": "C67890", "name": "User Login", "parent_id": "E12345", ...},
    {"level": "Capability", "formatted_id": "C67891", "name": "Password Management", "parent_id": "E12345", ...},
    {"level": "Feature", "formatted_id": "F11111", "name": "OAuth2 Auth", "parent_id": "C67890", ...},
    {"level": "Feature", "formatted_id": "F11112", "name": "Session Mgmt", "parent_id": "C67890", ...},
    {"level": "Feature", "formatted_id": "F11113", "name": "Password Reset", "parent_id": "C67891", ...}
  ],
  "validation_summary": {
    "complete": true,
    "all_features_linked": true,
    "total_items": 6,
    "epic_count": 1,
    "capability_count": 2,
    "feature_count": 3,
    "validation_errors": []
  }
}
```

### Example 2: Epic with No Features Yet

**Input:**
```
Epic ID: E99999
```

**Rally API Calls:**
1. Find Epic E99999 → Success
2. Get Capabilities under E99999 → None found
3. Get Features → Skipped (no Capabilities)

**Output:**
```json
{
  "epic_id": "E99999",
  "epic_name": "New Initiative",
  "hierarchy": [
    {"level": "Epic", "formatted_id": "E99999", "name": "New Initiative", "parent_id": null, ...}
  ],
  "validation_summary": {
    "complete": true,
    "all_features_linked": true,
    "total_items": 1,
    "epic_count": 1,
    "capability_count": 0,
    "feature_count": 0,
    "validation_errors": [],
    "note": "Epic has no Capabilities or Features yet."
  }
}
```

### Example 3: Invalid Epic ID

**Input:**
```
Epic ID: E00000
```

**Rally API Calls:**
1. Find Epic E00000 → Not found

**Output:**
```json
{
  "error": "Epic not found",
  "epic_id": "E00000",
  "message": "Epic with FormattedID 'E00000' could not be found in Rally. Please verify the Epic ID and try again.",
  "suggestion": "Check the Epic ID in Rally UI or use /fesm-query to search for the Epic."
}
```

---

## Performance Considerations

1. **Parallel Queries**: Rally API client uses ThreadPoolExecutor for parallel queries (handled automatically by rally-api skill)
2. **Caching**: Rally API client caches workspace data (handled automatically by rally-api skill)
3. **Typical Performance**:
   - Epic with 5-10 Features: < 5 seconds
   - Epic with 20-30 Features: < 10 seconds
   - Epic with 50+ Features: < 20 seconds

---

## Skill Requirements

### Prerequisites

1. **Rally API Skill**: The scrum-master:rally-api skill must be available
2. **Rally API Key**: Configured in `~/.claude/aig.json`
3. **Network Access**: Internet connectivity to reach Rally API

### Configuration

Rally API configuration is handled by the rally-api skill. No additional configuration needed for rally-hierarchy skill.

If Rally API is not configured, user should run:
```
/fesm-install
```

---

## Integration with BRD Generator

The BRD generator (brd-generator.md) invokes this skill during Phase 1.7 (Traceability Collection):

**Invocation Pattern:**
```markdown
1. Check if Epic ID provided in user input
2. If yes:
   a. Invoke: Skill(skill: "business-analyst:rally-hierarchy")
   b. Provide Epic ID
   c. Receive hierarchy JSON
   d. Pass to traceability-builder agent for formatting
3. If no: Skip Rally traceability
```

**Error Handling in BRD Generator:**
- If skill returns error: Log warning, skip Rally traceability, continue with citations
- If hierarchy incomplete: Include validation warnings in traceability matrix
- If Rally API unavailable: Note in traceability matrix, continue with BRD generation

---

## Skill Version and Compatibility

**Version:** 1.0.0
**Compatible with:**
- BRD Generator v1.5.0+
- Traceability Builder Agent v1.0+
- Rally API Skill v1.4.5+

**Last Updated:** 2026-02-03

---

## Output Format Summary

**Successful Response:**
```json
{
  "epic_id": string,
  "epic_name": string,
  "epic_state": string,
  "hierarchy": [
    {
      "level": "Epic" | "Capability" | "Feature",
      "formatted_id": string,
      "name": string,
      "parent_id": string | null,
      "parent_name": string | null,
      "state": string,
      "validation_status": "Valid" | "Incomplete" | "Invalid"
    }
  ],
  "validation_summary": {
    "complete": boolean,
    "validated_at": string (ISO 8601),
    "all_features_linked": boolean,
    "total_items": number,
    "epic_count": number,
    "capability_count": number,
    "feature_count": number,
    "validation_errors": string[],
    "note": string (optional)
  }
}
```

**Error Response:**
```json
{
  "error": string,
  "epic_id": string,
  "message": string,
  "suggestion": string
}
```
