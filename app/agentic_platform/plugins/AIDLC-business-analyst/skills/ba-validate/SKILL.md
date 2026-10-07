---
name: ba-validate
description: Validate business artifacts against requirements, check coverage and completeness, or detect ambiguities and conflicts in requirements. Routes to artifact validator or ambiguity detector based on intent. Use for quality assurance of business documentation.
license: Proprietary
compatibility: Requires business-analyst agent and ambiguity-detector agent. Works with Rally API for EPIC validation (optional).
metadata:
  author: ADLC Business Analyst Team
  version: "1.0.0"
  organization: AIG
  plugin: AIDLC-business-analyst
allowed-tools: Task Read Glob Grep
---

# Business Analyst Validate Skill

Validate business artifacts for completeness, consistency, and quality. Detects ambiguities, conflicts, and gaps in requirements documentation.

---

## When to Use This Skill

Use this skill when:
- Validating business artifacts against source requirements
- Checking coverage and completeness of EPICs, business rules, personas, or BRDs
- Detecting ambiguities and conflicts in requirements documents
- Finding undefined terms, missing rules, or vague language
- Verifying consistency between multiple artifacts
- Quality assurance before implementation

**Do NOT use this skill for:**
- Generating new artifacts (use ba-generate skill instead)
- Exploring artifacts (use ba-explore skill instead)
- Creating BRDs (use ba-brd skill instead)

---

## Validation Types

This skill supports two validation intents:

### 1. Artifact Validation (Coverage & Completeness)

**Purpose:** Check if generated artifacts properly cover source requirements

**Triggers:**
- "validate artifacts"
- "check coverage"
- "validate BRD"
- "verify completeness"
- "check EPIC coverage"

**What It Validates:**
- EPIC coverage against requirements
- Business rules completeness
- Persona coverage
- BRD section completeness
- Consistency between artifacts

### 2. Ambiguity Detection (Requirements Quality)

**Purpose:** Detect ambiguities, conflicts, and gaps in requirements

**Triggers:**
- "validate ambiguities"
- "check for conflicts"
- "analyze clarity"
- "find ambiguities"
- "check requirements quality"
- "detect conflicts"

**What It Detects:**
- Conflicting statements across documents
- Missing business rules and undefined data fields
- Undefined actors and roles
- Vague language and subjective terms
- Requirements without acceptance criteria
- Incomplete specifications

---

## How This Skill Works

### Intent 1: Validate Artifacts (Coverage Check)

#### Step 1: Analyze User Intent
- Identify what to validate (EPIC, business rules, personas, BRD)
- Determine validation criteria (coverage, completeness, consistency)

#### Step 2: ADLC Validation Inputs
- Locate source requirements documents
- Locate generated artifacts (EPIC, business rules, personas, BRD)
- Identify expected coverage criteria

#### Step 3: Perform Validation
- Check completeness of artifacts against sources
- Verify consistency between artifacts
- Calculate coverage percentage by section/requirement
- Identify gaps or missing elements

#### Step 4: Generate Validation Report

```
Business Artifact Validation Report
===================================
Source: {source_documents}
Artifacts: {artifact_types}

EPIC Coverage: {covered}/{total} {status}
Business Rules: {covered}/{total} {status}
Personas: {covered}/{total} {status}
BRD Completeness: {percentage}%

Overall: {PASSED/FAILED}

Gaps Found:
- {gap_description}
- {gap_description}

Recommendations:
- {recommendation}
```

---

### Intent 2: Validate Ambiguities (Quality Check)

#### Step 1: Route to Ambiguity Detector

Directly invoke the ambiguity-detector agent:
```
Use Task tool with subagent_type="business-analyst:ambiguity-detector"
```

#### Step 2: Provide Documents

Pass document paths or BRD to analyze:
- Requirements documents
- BRD files
- Business rules documents
- Any requirements specification

#### Step 3: Comprehensive Analysis

The ambiguity-detector performs:
1. **Conflict Detection**: Find conflicting statements across documents
2. **Missing Definitions**: Identify undefined business rules and data fields
3. **Undefined Actors**: Find roles and actors without definitions
4. **Vague Language**: Flag subjective terms and unclear requirements
5. **Missing Acceptance Criteria**: Find requirements without validation criteria
6. **Severity Scoring**: Rate issues as High/Medium/Low

#### Step 4: Receive Ambiguity Report

```
Ambiguity Analysis Report
=========================
Clarity Score: {score}/100
Total Ambiguities: {count}

High Severity: {count} - Requires immediate clarification
Medium Severity: {count} - Should clarify before implementation
Low Severity: {count} - Minor improvements

Conflicts Detected: {count}
Missing Definitions: {count}
Vague Requirements: {count}
Missing Acceptance Criteria: {count}

[Detailed report with source attribution follows]
```

---

## Agent Invocation

### For Artifact Validation

Uses Task tool with `subagent_type="business-analyst"`:

```
## User Request
{user_request}

## Intent: VALIDATE_ARTIFACTS (Check Business Artifacts)

### 1. Analyze User Intent
- Identify what to validate
- Determine validation criteria

### 2. ADLC Validation Inputs
- Locate source requirements
- Locate generated artifacts (EPIC, business rules, personas, BRD)
- Identify expected coverage

### 3. Perform Validation
- Check completeness of artifacts
- Verify consistency between artifacts
- Calculate coverage percentage
- Identify gaps or missing elements

### 4. Generate Validation Report
[Format as shown above]
```

### For Ambiguity Detection

Uses Task tool with `subagent_type="business-analyst:ambiguity-detector"`:

```
Analyze the following documents for ambiguities, conflicts, and gaps:

Documents: {path or list of documents}

Perform comprehensive ambiguity detection:
1. Detect conflicting statements across documents
2. Identify missing business rules and undefined data fields
3. Find undefined actors and roles
4. Flag vague language and subjective terms
5. Identify requirements without acceptance criteria

Generate structured ambiguity report with:
- Executive summary with clarity score
- Detailed findings by category
- Severity scoring (High/Medium/Low)
- Source attribution with context
- Actionable recommendations
```

---

## Usage Examples

### Example 1: Validate EPIC Coverage

**Input:**
```
Validate EPIC E12345 against requirements in ./docs/auth-spec.md
```

**Process:**
1. Read EPIC E12345 definition
2. Read ./docs/auth-spec.md requirements
3. Map EPIC features to requirements
4. Calculate coverage percentage
5. Identify gaps

**Output:**
```
Business Artifact Validation Report
===================================
Source: ./docs/auth-spec.md
Artifacts: EPIC E12345 (Customer Authentication)

EPIC Coverage: 8/10 requirements (80%) ✓ PASSED

Features Covered:
- F11111: OAuth2 Auth → REQ-001, REQ-002, REQ-003
- F11112: Session Mgmt → REQ-004, REQ-005
- F11113: Password Reset → REQ-006, REQ-007, REQ-008

Gaps Found:
- REQ-009: Multi-factor authentication (not covered)
- REQ-010: Biometric login (not covered)

Recommendations:
- Add Feature for MFA (REQ-009)
- Add Feature for Biometric Auth (REQ-010)
```

### Example 2: Validate BRD Completeness

**Input:**
```
Validate BRD completeness for ./output/BRD.md
```

**Process:**
1. Read BRD.md
2. Check all 16 sections
3. Verify section content vs "[Not available]"
4. Calculate completeness percentage
5. Identify missing content

**Output:**
```
Business Artifact Validation Report
===================================
Source: ./output/BRD.md
Artifacts: Business Requirements Document

BRD Completeness: 13/16 sections (81%) ✓ PASSED

Complete Sections: ✓
- Executive Summary (100%)
- Business Context (100%)
- Business Outcomes (85%)
- Scope Definition (100%)
- Business Rules (92%)
- Functional Requirements (95%)
- Non-Functional Requirements (88%)
- Success Metrics (90%)
- Compliance Requirements (100%)
- Dependencies (85%)
- Risks and Mitigations (80%)
- Appendices (100%)
- Traceability Matrix (100%)

Incomplete Sections: ✗
- User Personas (0% - Not available in source documents)
- Source Documents Reference (0% - Not generated)

Overall: PASSED with minor gaps

Recommendations:
- Conduct user research to define personas
- Enable citation tracking for source reference
```

### Example 3: Detect Ambiguities in Requirements

**Input:**
```
Check for ambiguities in ./requirements/
```

**Process:**
1. Route to ambiguity-detector agent
2. Read all documents in ./requirements/
3. Analyze for conflicts, vague terms, missing definitions
4. Score severity of issues
5. Generate detailed report

**Output:**
```
Ambiguity Analysis Report
=========================
Clarity Score: 72/100
Total Ambiguities: 18

High Severity: 5 - Requires immediate clarification
Medium Severity: 8 - Should clarify before implementation
Low Severity: 5 - Minor improvements

Conflicts Detected: 2
Missing Definitions: 6
Vague Requirements: 7
Missing Acceptance Criteria: 3

=== HIGH SEVERITY ISSUES ===

1. CONFLICT: Authentication Timeout
   - Document: auth-spec.md, Section 3.2
     "Session timeout should be 30 minutes"
   - Document: security-req.md, Section 2.1
     "Sessions must expire after 15 minutes"
   - Impact: Implementation uncertainty
   - Recommendation: Clarify timeout requirement with stakeholders

2. MISSING DEFINITION: "Authorized User"
   - Referenced in: auth-spec.md (5 times), user-access.md (3 times)
   - Definition: Not provided
   - Impact: Role-based access unclear
   - Recommendation: Define user authorization levels

3. VAGUE REQUIREMENT: "System should be fast"
   - Document: nfr-spec.md, Section 4.1
   - Issue: No measurable criteria
   - Impact: Cannot validate performance
   - Recommendation: Define specific performance metrics (e.g., "Response time < 200ms")

[Additional issues...]

=== MEDIUM SEVERITY ISSUES ===
[...]

=== LOW SEVERITY ISSUES ===
[...]

=== RECOMMENDATIONS ===
1. Define "Authorized User" role taxonomy
2. Resolve authentication timeout conflict
3. Add quantitative metrics to NFR requirements
4. Define business rule formulas for calculations
5. Add acceptance criteria to all functional requirements
```

### Example 4: Validate Multiple Artifacts

**Input:**
```
Validate coverage of EPIC, business rules, and personas against ./business-case/
```

**Process:**
1. Read all artifacts (EPIC, business rules, personas)
2. Read source documents in ./business-case/
3. Map each artifact type to sources
4. Calculate coverage for each
5. Check consistency between artifacts

**Output:**
```
Business Artifact Validation Report
===================================
Source: ./business-case/ (6 documents)
Artifacts: EPIC E12345, 12 Business Rules, 3 Personas

EPIC Coverage: 15/18 requirements (83%) ✓ PASSED
Business Rules: 12/15 rules defined (80%) ✓ PASSED
Personas: 3/4 personas defined (75%) ⚠ WARNING

Cross-Artifact Consistency: ✓ PASSED
- All EPIC features have corresponding business rules
- All personas referenced in requirements
- No conflicts detected between artifacts

Overall: PASSED with minor gaps

Gaps Found:
- EPIC missing: REQ-014 (Data Export), REQ-016 (Audit Log), REQ-017 (Reporting)
- Business Rules missing: BR-013 (Export Format), BR-014 (Audit Retention), BR-015 (Report Scheduling)
- Persona missing: "System Administrator" (referenced in 5 requirements)

Recommendations:
- Add Feature for Data Export and Reporting (REQ-014, REQ-016, REQ-017)
- Define business rules for export, audit, and reporting
- Create System Administrator persona
```

---

## Validation Report Format

### Artifact Validation Report

```markdown
Business Artifact Validation Report
===================================
Source: {source_document_paths}
Artifacts: {artifact_list}
Validation Date: {timestamp}

## Coverage Summary

| Artifact Type | Coverage | Status |
|---------------|----------|--------|
| EPIC | 8/10 (80%) | ✓ PASSED |
| Business Rules | 12/15 (80%) | ✓ PASSED |
| Personas | 3/4 (75%) | ⚠ WARNING |
| BRD Sections | 13/16 (81%) | ✓ PASSED |

Overall Status: {PASSED/WARNING/FAILED}

## Detailed Findings

### EPIC Coverage
{detailed_epic_coverage}

### Business Rules Coverage
{detailed_rules_coverage}

### Personas Coverage
{detailed_persona_coverage}

### BRD Completeness
{detailed_brd_completeness}

## Gaps and Issues

1. {gap_description_with_source}
2. {gap_description_with_source}

## Consistency Check

✓ EPIC features align with business rules
✓ Personas referenced in requirements
✗ Conflict: {conflict_description}

## Recommendations

1. {actionable_recommendation}
2. {actionable_recommendation}
```

### Ambiguity Analysis Report

```markdown
Ambiguity Analysis Report
=========================
Documents Analyzed: {document_list}
Analysis Date: {timestamp}
Clarity Score: {score}/100

## Executive Summary

Total Ambiguities: {count}
- High Severity: {count} (requires immediate action)
- Medium Severity: {count} (should address before implementation)
- Low Severity: {count} (minor improvements)

## Issue Categories

| Category | Count | High | Medium | Low |
|----------|-------|------|--------|-----|
| Conflicts | {n} | {n} | {n} | {n} |
| Missing Definitions | {n} | {n} | {n} | {n} |
| Vague Language | {n} | {n} | {n} | {n} |
| Missing Criteria | {n} | {n} | {n} | {n} |

## High Severity Issues

### 1. {Issue Type}: {Issue Title}
- **Document:** {document_name}, {section}
- **Quote:** "{exact_quote}"
- **Issue:** {problem_description}
- **Impact:** {business_impact}
- **Recommendation:** {specific_action}

[Additional issues...]

## Medium Severity Issues
[...]

## Low Severity Issues
[...]

## Recommendations

1. **Immediate Actions (High Severity)**
   - {action_1}
   - {action_2}

2. **Before Implementation (Medium Severity)**
   - {action_1}
   - {action_2}

3. **Continuous Improvement (Low Severity)**
   - {action_1}
   - {action_2}

## Clarity Score Breakdown

- Conflict Resolution: {score}/25
- Definition Completeness: {score}/25
- Language Precision: {score}/25
- Acceptance Criteria: {score}/25

**Total: {score}/100**
```

---

## Validation Criteria

### EPIC Validation
- ✓ All source requirements mapped to features
- ✓ Success criteria defined
- ✓ Dependencies identified
- ✓ Scope clearly defined (in/out)
- ✓ No conflicting requirements

### Business Rules Validation
- ✓ All rules have formulas or logic
- ✓ Validation criteria specified
- ✓ Error handling defined
- ✓ Examples provided
- ✓ Traceability to requirements

### Personas Validation
- ✓ All personas referenced in requirements
- ✓ Demographics defined
- ✓ Goals and pain points specified
- ✓ User journeys mapped
- ✓ No contradictory persona attributes

### BRD Validation
- ✓ All 16 sections present
- ✓ No "[Not available]" placeholders
- ✓ Source documents referenced
- ✓ Traceability matrix complete
- ✓ Consistency across sections

### Requirements Quality (Ambiguity)
- ✓ No conflicting statements
- ✓ All terms defined
- ✓ No vague language
- ✓ Acceptance criteria present
- ✓ Quantifiable metrics specified

---

## Severity Scoring

### High Severity
- **Conflicts:** Contradictory requirements that block implementation
- **Missing Critical Definitions:** Undefined core business terms
- **Blocking Ambiguities:** Cannot implement without clarification

**Impact:** Implementation cannot proceed
**Action Required:** Immediate stakeholder clarification

### Medium Severity
- **Inconsistencies:** Minor conflicts that affect quality
- **Missing Optional Definitions:** Undefined secondary terms
- **Vague Requirements:** Lack of measurable criteria

**Impact:** Implementation possible but risky
**Action Required:** Address before implementation

### Low Severity
- **Style Issues:** Inconsistent terminology
- **Minor Gaps:** Nice-to-have information missing
- **Optimization Opportunities:** Clarity improvements

**Impact:** Implementation unaffected
**Action Required:** Continuous improvement

---

## Error Handling

### Artifact Not Found
```
Error: Artifact not found
Path: {artifact_path}
Suggestion: Verify the artifact exists at the specified location
Available artifacts: {list_of_found_artifacts}
```

### Source Requirements Missing
```
Error: Source requirements not found
Path: {requirements_path}
Suggestion: Provide path to source requirements documents
Expected formats: .md, .txt, .docx, .pdf
```

### No Ambiguities Detected
```
Ambiguity Analysis Report
=========================
Clarity Score: 95/100
Total Ambiguities: 2 (Low Severity)

**Result:** Requirements are clear and well-defined
**Quality:** Excellent - ready for implementation

Minor Improvements:
- Add glossary for industry terms (2 terms)
```

### Invalid Validation Request
```
Error: Cannot determine validation intent
Request: {user_request}

Did you mean:
1. Validate artifact coverage (artifacts vs requirements)
2. Detect ambiguities (requirements quality check)

Please specify what you want to validate.
```

---

## Performance Considerations

- Uses efficient file scanning (Glob/Grep)
- Processes documents in parallel
- Caches loaded documents for cross-validation
- Progressive reporting (summary first, details on demand)

### Typical Performance

| Task | Documents | Time |
|------|-----------|------|
| EPIC validation | 5-10 reqs | 10-20s |
| BRD validation | 1 BRD | 5-10s |
| Ambiguity detection | 5-10 docs | 30-60s |
| Full artifact validation | 20+ files | 60-120s |

---

## Skill Requirements

### Prerequisites
1. **Business Analyst Agent**: For artifact validation routing
2. **Ambiguity Detector Agent**: For requirements quality analysis
3. **File Access**: Read permissions for artifacts and source documents

### Document Formats
- Markdown (.md)
- Text (.txt)
- Word (.docx) - requires documents skill
- PDF (.pdf) - requires documents skill

### Optional Dependencies
- Rally API (for EPIC validation against Rally)

---

## Integration with Other Skills

### Works With
- **ba-explore**: Explore before validating
- **ba-generate**: Validate after generating
- **documents**: Read DOCX/PDF artifacts

### Coordinates With
- **ambiguity-detector**: For requirements quality analysis
- **rally-hierarchy**: For EPIC-to-Rally validation

### Not Used For
- Generation (use ba-generate)
- BRD creation (use ba-brd)
- Exploration (use ba-explore)

---

## Version and Compatibility

**Version:** 1.0.0

**Compatible with:**
- Business Analyst Agent v0.9.0+
- Ambiguity Detector Agent v1.0.0+
- Documents Skill v1.0.0+ (optional)
- Rally Hierarchy Skill v1.0.0+ (optional)

**Last Updated:** 2026-02-05
