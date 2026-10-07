---
name: ba-explore
description: Explore and understand existing business requirements and artifacts including EPIC definitions, business rules, personas, and their relationships. Use when you need to understand existing business documentation or analyze requirement artifacts.
license: Proprietary
compatibility: Requires business-analyst agent. Works with Rally API for EPIC queries (optional).
metadata:
  author: ADLC Business Analyst Team
  version: "1.0.0"
  organization: AIG
  plugin: AIDLC-business-analyst
allowed-tools: Task Read Glob Grep
---

# Business Analyst Explore Skill

Explore and understand existing business requirements and artifacts to help users understand EPIC definitions, business rules, personas, and how they relate to each other.

---

## When to Use This Skill

Use this skill when:
- User asks questions about existing business artifacts or requirements
- Need to understand EPIC definitions and their scope
- Need to analyze business rules and their relationships
- Need to review personas and stakeholder information
- Need to understand dependencies between business requirements
- Need to explain how different artifacts relate to each other

**Do NOT use this skill for:**
- Generating new artifacts (use ba-generate skill instead)
- Validating artifacts (use ba-validate skill instead)
- Creating BRDs (use ba-brd skill instead)

---

## How This Skill Works

### Step 1: Analyze User Intent

The skill will analyze what you want to understand:
- Specific artifact type (EPIC, business rules, personas)
- Scope of exploration (single artifact, multiple artifacts, relationships)
- Context needed (dependencies, traceability, impact)

### Step 2: Locate and Read Artifacts

The skill will search for and read relevant documents:
- EPIC definitions (typically in docs/, output/, or Rally)
- Business rules (in requirements documents or dedicated files)
- Personas (in user research documents or BRDs)
- Related documentation (technical specs, design docs)

### Step 3: Analyze Relationships

The skill will identify connections between artifacts:
- Which EPICs contain which features
- Which business rules apply to which requirements
- Which personas are affected by which features
- Dependencies and constraints between artifacts

### Step 4: Provide Clear Explanation

The skill will summarize findings:
- Concise overview of what was found
- Relationships and dependencies explained
- Gaps or missing information identified
- Suggested next steps if applicable

---

## Agent Invocation

This skill uses the Task tool with `subagent_type="business-analyst"`.

### Enriched Prompt Template

```
## User Request
{user_request}

## Intent: EXPLORE (Understand Business Artifacts)

### 1. Analyze User Intent
- Identify what user wants to understand
- Determine scope of exploration
- Identify specific artifacts or areas of interest

### 2. Explore Existing Artifacts
- Search for EPIC definitions, business rules, personas
- Use Glob to find relevant files
- Use Grep to search for specific content
- Read and analyze relevant documents

### 3. Analyze Relationships
- Identify relationships between artifacts
- Map dependencies and constraints
- Note any traceability information

### 4. Provide Clear Explanation
- Summarize findings in clear, structured format
- Explain relationships and dependencies
- Highlight gaps or missing information
- Suggest next steps if applicable
```

---

## Usage Examples

### Example 1: Understanding an EPIC

**Input:**
```
What is EPIC E12345 about and what features does it include?
```

**Process:**
1. Search for EPIC E12345 in documentation
2. Optionally query Rally API for EPIC details
3. Find related features and capabilities
4. Summarize EPIC scope and contents

**Output:**
```
EPIC E12345: Customer Authentication System

Scope: Implement secure authentication and authorization for customer-facing applications

Features:
- F11111: OAuth2 Authentication
- F11112: Session Management
- F11113: Password Reset Flow

Status: In Progress
Dependencies: Identity Provider API, Vault integration
```

### Example 2: Understanding Business Rules

**Input:**
```
What are the validation rules for customer address information?
```

**Process:**
1. Search for business rules documents
2. Find address validation rules
3. Identify related fields and validations
4. Summarize rules with examples

**Output:**
```
Customer Address Validation Rules:

Required Fields:
- Street Address (max 100 chars)
- City (max 50 chars)
- State (2-letter code)
- ZIP Code (5 or 9 digits)

Validation Rules:
1. ZIP code must match state
2. PO Box addresses require separate flag
3. International addresses use Country field
4. Address must be verified via USPS API

Related Business Rules: BR_ADDR_001, BR_ADDR_002, BR_ADDR_005
```

### Example 3: Understanding Personas

**Input:**
```
What personas are defined for the customer portal?
```

**Process:**
1. Search for persona definitions
2. Read persona documents
3. Identify goals, pain points, and journeys
4. Summarize key personas

**Output:**
```
Customer Portal Personas:

1. "Busy Professional" - Sarah
   - Demographics: Age 35-45, high income, time-constrained
   - Goals: Quick policy access, efficient claims filing
   - Pain Points: Complex navigation, slow load times

2. "First-time Buyer" - James
   - Demographics: Age 25-35, tech-savvy, price-conscious
   - Goals: Easy quote comparison, clear explanations
   - Pain Points: Insurance jargon, unclear pricing

3. "Retired Customer" - Margaret
   - Demographics: Age 65+, less technical, detail-oriented
   - Goals: Personal service, comprehensive coverage
   - Pain Points: Small fonts, complex workflows
```

---

## Output Format

The skill provides structured output:

```
[Artifact Type]: [Artifact Name/ID]

[Brief Description]

[Key Details Section 1]
- Detail 1
- Detail 2

[Key Details Section 2]
- Detail 1
- Detail 2

Related Artifacts: [List]
Dependencies: [List]
Status: [Current State]
```

---

## Error Handling

### Artifact Not Found

If the requested artifact cannot be found:
- Clearly state what was searched
- Suggest where to look (Rally, specific directories)
- Offer to help search with different terms

### Incomplete Information

If artifact exists but lacks detail:
- Provide what information is available
- Clearly mark missing sections
- Suggest sources for additional information

### Ambiguous Request

If the request is unclear:
- Ask clarifying questions
- Suggest specific artifacts that might match
- Offer to explore related areas

---

## Performance Considerations

- Uses Glob/Grep for efficient file searching
- Reads only relevant sections of large documents
- Caches Rally API queries when possible
- Provides progressive disclosure (summary first, details on request)

---

## Skill Requirements

### Prerequisites
1. Access to business artifacts (local files or Rally)
2. Read permissions for documentation directories
3. Optional: Rally API access for EPIC queries

### Typical File Locations
- EPICs: docs/, output/, Rally
- Business Rules: requirements/, docs/
- Personas: user-research/, docs/
- BRDs: output/, docs/

---

## Integration with Other Skills

This skill works alongside:
- **ba-generate**: After exploring, generate new artifacts
- **ba-validate**: After exploring, validate completeness
- **rally-hierarchy**: For Rally EPIC exploration
- **documents**: For reading DOCX/PDF artifacts

---

## Version and Compatibility

**Version:** 1.0.0
**Compatible with:**
- Business Analyst Agent v0.9.0+
- Rally API Skill v1.4.5+ (optional)
- Document Converter Skill v1.0.0+ (optional)

**Last Updated:** 2026-02-05
