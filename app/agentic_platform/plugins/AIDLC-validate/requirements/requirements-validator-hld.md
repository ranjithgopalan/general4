# High-Level Design: Requirements Validator Plugin

**Feature:** Claude Code Plugin for SDLC Requirements Completeness Validation

**Status:** Implementing

**Document Type:** High-Level Design (HLD)

**Version:** 1.0

**Consumer:** Development teams validating requirements coverage

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [Component Design](#2-component-design)
3. [Plugin Structure](#3-plugin-structure)
4. [Agent Design](#4-agent-design)
5. [Commands Design](#5-commands-design)
6. [Validation Types](#6-validation-types)
7. [Data Flow](#7-data-flow)
8. [Integration Points](#8-integration-points)
9. [Error Handling Strategy](#9-error-handling-strategy)
10. [Testing Strategy](#10-testing-strategy)
11. [Revision History](#11-revision-history)

---

## 1. Architecture Overview

### 1.1 System Context

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                      Requirements Validator Plugin                           │
│                                                                              │
│  Validates completeness from:                                                │
│  - Ground Truth Documents (BRD, PRD, TDD, User Stories)                     │
│  - Multiple formats (PDF, DOCX, Excel, Markdown)                            │
│                                                                              │
│  Against Generated Artifacts:                                                │
│  - EPIC definitions                                                          │
│  - Feature documents                                                         │
│  - User Stories (Markdown, Rally, Jira exports)                             │
│  - Implementation code (traceability)                                        │
│                                                                              │
│  Produces:                                                                   │
│  - COMPLETENESS-REPORT.md                                                   │
│  - Coverage metrics                                                          │
│  - Traceability matrix                                                       │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 1.2 High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                      /validate-requirements Command                          │
│                         (Unified Entry Point)                                │
└───────────────────────────────┬─────────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                    requirements-validator-router                             │
│                 (Planning + Routing + Coordination)                          │
├─────────────────────────────────────────────────────────────────────────────┤
│  1. Analyze request (determine validation type)                              │
│  2. Identify ground truth documents                                          │
│  3. Identify target artifacts                                                │
│  4. Present validation plan for approval                                     │
│  5. Delegate to specialized validators                                       │
│  6. Aggregate results into unified report                                    │
│  7. Generate traceability matrix                                             │
└───────────────────────────────┬─────────────────────────────────────────────┘
                                │
        ┌───────────────────────┼───────────────────────┬─────────────────────┐
        ▼                       ▼                       ▼                     ▼
┌───────────────┐      ┌───────────────┐      ┌───────────────┐      ┌───────────────┐
│ epic-coverage │      │ feature-      │      │ story-        │      │ traceability- │
│ validator     │      │ coverage-     │      │ coverage-     │      │ analyzer      │
│               │      │ validator     │      │ validator     │      │               │
│ BRD → EPIC    │      │ PRD → Feature │      │ Feature →     │      │ Story → Code  │
│ coverage      │      │ coverage      │      │ Story coverage│      │ traceability  │
└───────────────┘      └───────────────┘      └───────────────┘      └───────────────┘
        │                       │                       │                     │
        └───────────────────────┼───────────────────────┴─────────────────────┘
                                ▼
                    ┌───────────────────────┐
                    │  Validation Output     │
                    │  - COMPLETENESS-REPORT │
                    │  - Coverage metrics    │
                    │  - Traceability matrix │
                    └───────────────────────┘
```

### 1.3 Multi-Agent Architecture

| Agent | Model | Purpose | Permission |
|-------|-------|---------|------------|
| requirements-validator-router | Sonnet | Request analysis, ground truth identification, routing | plan |
| epic-coverage-validator | Haiku | BRD → EPIC completeness validation | plan |
| feature-coverage-validator | Haiku | PRD/EPIC → Features coverage validation | plan |
| story-coverage-validator | Haiku | Features → User Stories coverage validation | plan |
| traceability-analyzer | Haiku | Stories → Code traceability analysis | plan |

### 1.4 Architectural Principles

| Principle | Implementation |
|-----------|----------------|
| **Ground Truth Comparison** | Always compare artifacts against explicit source documents |
| **Evidence-Based Reporting** | Every gap cites source location and confidence level |
| **Human-In-The-Loop (HITL)** | All findings require human verification |
| **Confidence Scoring** | HIGH/MEDIUM/LOW levels for all findings |
| **Read-Only Operation** | Never modifies source documents or artifacts |
| **Multi-Format Support** | Process PDF, DOCX, Excel, Markdown ground truths |

---

## 2. Component Design

### 2.0 Agent Architecture Pattern

The requirements-validator plugin follows a **three-tier agent architecture** that separates concerns and enables scalable, maintainable agent workflows:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          Agent Architecture Pattern                          │
├─────────────────────────────────────────────────────────────────────────────┤
│  TIER 1: COMMAND (Prompt Enricher)                                          │
│  ┌────────────────────────────────────────────────────────────────────────┐ │
│  │  /validate-requirements command (commands/validate-requirements.md)    │ │
│  │  - Takes $ARGUMENTS from user (ground truth, target, scope)            │ │
│  │  - Enriches with validation strategy references                        │ │
│  │  - Invokes requirements-validator-router via Task tool                 │ │
│  │  - NO routing logic (delegated to router)                              │ │
│  └────────────────────────────────────────────────────────────────────────┘ │
│                                    │                                         │
│                                    ▼                                         │
│  TIER 2: ROUTER (Context Engineering + Orchestration)                       │
│  ┌────────────────────────────────────────────────────────────────────────┐ │
│  │  requirements-validator-router.md (agents/requirements-validator-      │ │
│  │  router.md)                                                            │ │
│  │  Context Engineering:                                                  │ │
│  │  - Identify ground truth documents (BRD, PRD, Features)                │ │
│  │  - Identify target artifacts to validate                               │ │
│  │  - Read validation-strategies skill for matching patterns              │ │
│  │  - Determine validation chain scope                                    │ │
│  │                                                                        │ │
│  │  Orchestration:                                                        │ │
│  │  - Present validation plan for approval                                │ │
│  │  - Delegate to specialized validators                                  │ │
│  │  - Coordinate multi-level validation chains                            │ │
│  │  - Aggregate results into COMPLETENESS-REPORT.md                       │ │
│  └────────────────────────────────────────────────────────────────────────┘ │
│                                    │                                         │
│     ┌──────────────────┬───────────┼───────────┬──────────────────┐         │
│     ▼                  ▼           ▼           ▼                  ▼         │
│  TIER 3: SPECIALIZED AGENTS (Extended Thinking + TOON)                      │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐ ┌──────────────┐       │
│  │ epic-        │ │ feature-     │ │ story-       │ │ traceability-│       │
│  │ coverage-    │ │ coverage-    │ │ coverage-    │ │ analyzer     │       │
│  │ validator    │ │ validator    │ │ validator    │ │              │       │
│  │              │ │              │ │              │ │ Stories →    │       │
│  │ BRD → EPIC   │ │ PRD →        │ │ Features →   │ │ Code         │       │
│  │              │ │ Features     │ │ Stories      │ │              │       │
│  └──────────────┘ └──────────────┘ └──────────────┘ └──────────────┘       │
│                                                                              │
│  All agents use: Extended thinking + TOON format + Guardrails               │
└─────────────────────────────────────────────────────────────────────────────┘
```

#### Tier 1: Command as Prompt Enricher

The `/validate-requirements` command acts as a **prompt enricher**, not a router:

```markdown
---
name: validate-requirements
description: Unified command for requirements completeness validation
---

## User Request
$ARGUMENTS

## Context Instructions
1. Identify ground truth document (BRD, PRD, Features, Stories)
2. Identify target artifacts to validate
3. Determine validation scope (single level or full chain)

## Skill References
- skills/validation-strategies/content-matching.md
- skills/validation-strategies/confidence-scoring.md
- skills/validation-strategies/gap-reporting.md

## Invocation
Invoke the requirements-validator-router via Task tool with full context
```

#### Tier 2: Router with Context Engineering + Orchestration

The router has two distinct responsibilities expressed in TOON format:

```
ContextEngineering[Category,Skills]:
  GroundTruthIdentification,"Parse user request | Locate BRD/PRD/Feature files | Read document structure"
  TargetIdentification,"Identify artifacts to validate | Locate target documents | Map file locations"
  StrategySelection,"Read validation-strategies skill | Select matching approach | Determine confidence thresholds"
  ChainScopeDetection,"Determine single vs multi-level | Map validation chain | Plan execution order"

Orchestration[Category,Skills]:
  PlanPresentation,"Show ground truth | Show targets | Show validation chain | Request approval"
  ValidatorDelegation,"Route to epic-coverage-validator | Route to feature-coverage-validator | Route to story-coverage-validator | Route to traceability-analyzer"
  ChainCoordination,"Execute validators in sequence | Pass context between validators | Handle failures"
  ResultAggregation,"Collect gap reports | Calculate coverage | Generate COMPLETENESS-REPORT.md"
```

#### Tier 3: Specialized Agents with Extended Thinking + TOON

Each validator agent uses extended thinking and TOON format:

```markdown
[Extended thinking: I am an epic-coverage-validator that validates BRD to EPIC completeness.
When I receive a request from router with context, I first (1) read skill validation-strategies
for matching patterns; (2) read ground truth BRD document completely; (3) extract business
requirements with IDs; (4) read target EPIC definition; (5) match requirements to EPIC items
using content matching; (6) calculate coverage percentage; (7) generate gap report with
evidence and confidence levels.]

## Capabilities
Capabilities[Category,Skills]:
  ContentMatching,"Semantic matching | Keyword matching | ID correlation | Phrase detection"
  ConfidenceScoring,"High/Medium/Low confidence | Evidence strength | Match quality"
  GapReporting,"Missing requirements | Partial coverage | Unmapped items"

## Behavioral Traits
BehavioralTraits[Trait,Description]:
  NonDestructive,"Read-only validation - never modifies documents"
  EvidenceBased,"Each finding includes evidence and confidence level"
  BestEffort,"Provides best-effort analysis with clear limitations"

## Guardrails
INPUT_BLOCKING_CONDITIONS[Category,Triggers]:
  SCOPE_VIOLATION,"Document modification requests | Code generation requests"
  MISSING_INPUT,"No ground truth provided | No target provided"
```

### 2.1 Layer 1: Request Analysis & Routing

**Component:** `requirements-validator-router`

**Responsibilities:**
- Analyze user request to determine validation type
- Identify ground truth documents (user-specified paths)
- Identify target artifacts to validate
- Present validation plan for user approval
- Coordinate multi-level validation chains
- Aggregate results from specialized validators
- Generate unified COMPLETENESS-REPORT.md

**Validation Type Detection:**
```
Detection Rules:
1. IF user mentions "BRD", "business requirements", "EPIC"
   → Route to epic-coverage-validator
   → Ground Truth: BRD document
   → Target: EPIC definition

2. IF user mentions "PRD", "features", "product requirements"
   → Route to feature-coverage-validator
   → Ground Truth: PRD or EPIC document
   → Target: Feature documents

3. IF user mentions "stories", "user stories", "backlog"
   → Route to story-coverage-validator
   → Ground Truth: Feature documents
   → Target: User Stories

4. IF user mentions "code", "implementation", "traceability"
   → Route to traceability-analyzer
   → Ground Truth: User Stories
   → Target: Source code

5. IF user mentions "full chain", "end-to-end", "complete"
   → Run all validators in sequence
   → Chain: BRD → EPIC → Features → Stories → Code
```

### 2.2 Layer 2: Specialized Validators

#### 2.2.1 EPIC Coverage Validator

**Purpose:** Validate BRD → EPIC completeness

**Input:**
- Ground Truth: BRD document (PDF, DOCX, MD)
- Target: EPIC definition document

**Validation Checks:**
| Check | Expected | Gap Condition |
|-------|----------|---------------|
| Business capability coverage | Each BRD capability in EPIC | Capability not found |
| Stakeholder alignment | All stakeholders addressed | Stakeholder missing |
| Success metric mapping | Metrics traceable | Metric not mapped |
| Scope completeness | EPIC scope matches BRD scope | Scope mismatch |

**Output:** Coverage report with gaps, evidence, and confidence levels

#### 2.2.2 Feature Coverage Validator

**Purpose:** Validate PRD/EPIC → Features completeness

**Input:**
- Ground Truth: PRD or EPIC document
- Target: Feature documents (Markdown, Rally exports)

**Validation Checks:**
| Check | Expected | Gap Condition |
|-------|----------|---------------|
| Feature coverage | Each PRD feature has Feature doc | Feature not documented |
| Acceptance criteria | PRD AC in Feature doc | AC missing |
| User journey coverage | All journeys addressed | Journey not covered |
| Non-functional requirements | NFRs addressed | NFR missing |

#### 2.2.3 Story Coverage Validator

**Purpose:** Validate Features → User Stories completeness

**Input:**
- Ground Truth: Feature documents
- Target: User Stories (Markdown, Rally, Jira exports)

**Validation Checks:**
| Check | Expected | Gap Condition |
|-------|----------|---------------|
| Story coverage | Each feature has stories | Feature without stories |
| AC decomposition | Feature AC in story AC | AC not decomposed |
| Story completeness | All aspects covered | Partial coverage |
| Story sizing | Stories appropriately sized | Story too large |

#### 2.2.4 Traceability Analyzer

**Purpose:** Analyze Stories → Code traceability (best-effort)

**Input:**
- Ground Truth: User Stories
- Target: Source code files

**Matching Strategies (by reliability):**
1. **Explicit reference** (HIGH): Code comment contains story ID
2. **Filename convention** (MEDIUM): File named after story
3. **Content matching** (LOW): Story keywords in code

**Output:** Traceability report with confidence indicators

---

## 3. Plugin Structure

```
requirements-validator/
├── .claude-plugin/
│   └── plugin.json                          # Plugin manifest
│
├── requirements/
│   ├── requirements-validator-brd.md        # Business requirements
│   ├── requirements-validator-hld.md        # This document
│   └── requirements-validator-lld.md        # Implementation details
│
├── agents/
│   ├── requirements-validator-router.md     # Planning router (entry point)
│   ├── epic-coverage-validator.md           # BRD → EPIC validation
│   ├── feature-coverage-validator.md        # PRD → Features validation
│   ├── story-coverage-validator.md          # Features → Stories validation
│   ├── traceability-analyzer.md             # Stories → Code traceability
│   └── requirements-validator.md            # Original agent (reference)
│
├── commands/
│   ├── validate-requirements.md             # Unified validation command
│   └── check-coverage.md                    # Quick coverage check
│
├── skills/
│   └── validation-strategies/
│       ├── SKILL.md                         # Entry point
│       ├── content-matching.md              # Semantic comparison strategies
│       ├── confidence-scoring.md            # HIGH/MEDIUM/LOW definitions
│       ├── gap-reporting.md                 # Evidence-based gap formatting
│       └── traceability-rules.md            # Coverage matrix generation
│
├── __tests__/
│   ├── epic_coverage.test
│   ├── feature_coverage.test
│   ├── story_coverage.test
│   └── traceability.test
│
├── acceptance-criteria.md
└── README.md
```

---

## 4. Agent Design

### 4.1 Requirements Validator Router (Planning Router)

**File:** `agents/requirements-validator-router.md`

**Frontmatter:**
```yaml
---
name: requirements-validator-router
description: Orchestrates requirements validation workflows by identifying ground truth documents, planning validation strategy, and coordinating specialized validators. Use PROACTIVELY when users request requirements validation, coverage checks, or completeness verification.
model: sonnet
tools: Read, Glob, Grep, Task
permissionMode: plan
color: purple
---
```

**Purpose:**
The requirements-validator-router is the intelligent orchestrator for all requirements validation workflows. It identifies ground truth documents, determines validation scope, presents plans, and coordinates specialized validators.

**Workflow:**
```
Step 1: Analyze Request → Determine validation type (EPIC, Features, Stories, Code)
Step 2: Identify Ground Truth → Get user-specified source document paths
Step 3: Identify Targets → Locate artifacts to validate
Step 4: Present Plan → Show validation strategy for confirmation
Step 5: Delegate → Invoke specialized validator(s)
Step 6: Aggregate → Combine findings into unified report
Step 7: Report → Generate COMPLETENESS-REPORT.md with confidence levels
```

**Context Passing Specification:**
```yaml
contextPassing:
  userRequest: "Original validation request"
  validationType: "epic | feature | story | traceability | full-chain"
  groundTruth:
    type: "BRD | PRD | EPIC | Feature | Stories"
    path: "/path/to/source/document"
    format: "PDF | DOCX | Excel | MD"
  targets:
    type: "EPIC | Features | Stories | Code"
    paths: ["/path/to/artifacts/"]

  skillReferences:
    contentMatching: "skills/validation-strategies/content-matching.md"
    confidenceScoring: "skills/validation-strategies/confidence-scoring.md"
    gapReporting: "skills/validation-strategies/gap-reporting.md"
```

**Guardrails:**

### INPUT_BLOCKING_CONDITIONS
- **SCOPE_VIOLATION**: Requests to modify documents | requests to create artifacts
- **HARMFUL_CONTENT**: Malicious operations | destructive actions
- **QUALITY_VALIDATION**: Requests for hallucination detection (use artifact-validator)

### RESPONSE_TO_BLOCKED_INPUT
- **ACTION**: Politely refuse | explain completeness-only scope | suggest appropriate plugin
- **TONE**: Firm but respectful | educational
- **ALTERNATIVES**: Suggest artifact-validator for quality | suggest code-validator for code

### OPERATIONAL_CONSTRAINTS
- **ALWAYS**: Get ground truth path from user | present plan before validating | include confidence levels | cite evidence
- **NEVER**: Modify documents | auto-fix gaps | validate without ground truth | guarantee accuracy
- **STOP_AND_ASK**: Ground truth not specified | target artifacts not found | large document warning
- **DEFER_TO**: artifact-validator (quality/hallucinations) | code-validator (code quality) | security-reviewer (security)

**Key Distinctions:**
- **vs artifact-validator**: I check COMPLETENESS (what's missing); they check QUALITY (what's wrong)
- **vs code-validator**: I trace requirements to code; they validate code against standards
- **vs documents-maintainer**: I validate coverage; they generate documentation

---

### 4.2 EPIC Coverage Validator

**File:** `agents/epic-coverage-validator.md`

**Frontmatter:**
```yaml
---
name: epic-coverage-validator
description: Validates BRD to EPIC completeness by comparing business requirements against EPIC definitions. Identifies gaps with evidence and confidence levels. Use when validating EPIC coverage.
model: haiku
tools: Read, Glob, Grep
permissionMode: plan
color: blue
---
```

**Responsibilities:**
- Parse BRD document (ground truth)
- Extract business capabilities, stakeholders, success metrics
- Compare against EPIC definition
- Report gaps with evidence and confidence

**Output Format:**
```markdown
## EPIC Coverage Report

**Ground Truth:** {brd_path}
**Target:** {epic_path}
**Validation Date:** {timestamp}

### Coverage Summary (Estimated - Requires Human Verification)

| Category | In BRD | In EPIC | Coverage | Confidence |
|----------|--------|---------|----------|------------|
| Capabilities | 10 | 9 | ~90% | Medium |
| Stakeholders | 5 | 5 | ~100% | High |
| Success Metrics | 8 | 6 | ~75% | Medium |

### Potential Gaps

**GAP-001: Missing Capability** - Confidence: HIGH
- **Source:** BRD Page 5, Section 2.3
- **Content:** "System shall support automated calculation engine"
- **Expected In:** EPIC capabilities section
- **Status:** Not found in EPIC document
- **Recommendation:** Add to EPIC capabilities if confirmed missing

### Human Review Required
All findings require verification before action.
```

---

### 4.3 Feature Coverage Validator

**File:** `agents/feature-coverage-validator.md`

**Frontmatter:**
```yaml
---
name: feature-coverage-validator
description: Validates PRD/EPIC to Features completeness by comparing product requirements against Feature documents. Use when validating Feature coverage.
model: haiku
tools: Read, Glob, Grep
permissionMode: plan
color: green
---
```

**Responsibilities:**
- Parse PRD or EPIC document (ground truth)
- Extract features, acceptance criteria, user journeys
- Compare against Feature documents
- Report gaps with evidence and confidence

---

### 4.4 Story Coverage Validator

**File:** `agents/story-coverage-validator.md`

**Frontmatter:**
```yaml
---
name: story-coverage-validator
description: Validates Features to User Stories completeness by comparing Feature documents against User Stories. Use when validating Story coverage.
model: haiku
tools: Read, Glob, Grep
permissionMode: plan
color: orange
---
```

**Responsibilities:**
- Parse Feature documents (ground truth)
- Extract feature requirements and acceptance criteria
- Compare against User Stories (any format)
- Report gaps with evidence and confidence

---

### 4.5 Traceability Analyzer

**File:** `agents/traceability-analyzer.md`

**Frontmatter:**
```yaml
---
name: traceability-analyzer
description: Analyzes User Stories to Code traceability using multiple matching strategies. Best-effort analysis with confidence indicators. Use when checking code implementation coverage.
model: haiku
tools: Read, Glob, Grep
permissionMode: plan
color: red
---
```

**Responsibilities:**
- Parse User Stories (ground truth)
- Search code for story references
- Apply matching strategies (explicit, filename, content)
- Report traceability with confidence levels

**Limitation Acknowledgment:**
```
⚠️ IMPORTANT LIMITATION
Code-to-story matching is less reliable than document-to-document matching because:
- Code rarely contains story IDs
- Function names don't map directly to story descriptions
- One story may span multiple files

Confidence levels reflect this limitation.
```

---

## 5. Commands Design

### 5.1 Unified /validate-requirements Command

**File:** `commands/validate-requirements.md`

**Frontmatter:**
```yaml
---
argument-hint: [validation request]
description: Unified command for requirements completeness validation - validates coverage from ground truth documents to generated artifacts
---
```

**Routing Logic:**
```markdown
## Routing Logic

Analyze the user's request and determine validation type:

### 1. BRD → EPIC → epic-coverage-validator
**Triggers:** brd, business requirements, epic coverage
**Ground Truth:** BRD document
**Target:** EPIC definition

### 2. PRD → Features → feature-coverage-validator
**Triggers:** prd, product requirements, features
**Ground Truth:** PRD or EPIC document
**Target:** Feature documents

### 3. Features → Stories → story-coverage-validator
**Triggers:** stories, user stories, backlog
**Ground Truth:** Feature documents
**Target:** User Stories

### 4. Stories → Code → traceability-analyzer
**Triggers:** code, implementation, traceability
**Ground Truth:** User Stories
**Target:** Source code

### 5. Full Chain → All validators
**Triggers:** full, complete, end-to-end, entire chain
**Flow:** BRD → EPIC → Features → Stories → Code
```

**Example Routing:**

| User Request | Detected Intent | Routed To |
|--------------|-----------------|-----------|
| "Validate EPIC against BRD" | BRD → EPIC | epic-coverage-validator |
| "Check if all PRD features are covered" | PRD → Features | feature-coverage-validator |
| "Validate user story coverage" | Features → Stories | story-coverage-validator |
| "Check code traceability" | Stories → Code | traceability-analyzer |
| "Full chain validation" | Complete | All validators sequentially |

---

## 6. Validation Types

### 6.1 EPIC Coverage Validation

| Source | Target | Validation |
|--------|--------|------------|
| BRD | EPIC | Capabilities, stakeholders, metrics |

### 6.2 Feature Coverage Validation

| Source | Target | Validation |
|--------|--------|------------|
| PRD / EPIC | Features | Features, acceptance criteria, journeys |

### 6.3 Story Coverage Validation

| Source | Target | Validation |
|--------|--------|------------|
| Features | User Stories | Story decomposition, AC coverage |

### 6.4 Traceability Analysis

| Source | Target | Validation |
|--------|--------|------------|
| User Stories | Code | Implementation references |

### 6.5 Full Chain Validation

```
BRD → EPIC → Features → Stories → Code
  │      │        │         │        │
  └──────┴────────┴─────────┴────────┘
           Cascading validation
```

---

## 7. Data Flow

### 7.1 Single-Level Validation Flow

```
User Request: "Validate EPIC against BRD"
     │
     ▼
┌─────────────────────────┐
│ requirements-validator- │
│ router                  │
│ 1. Get BRD path         │
│ 2. Get EPIC path        │
│ 3. Present plan         │
└───────────┬─────────────┘
            │ (after approval)
            ▼
┌─────────────────────────┐
│ epic-coverage-validator │
│ - Parse BRD             │
│ - Extract requirements  │
│ - Compare to EPIC       │
│ - Identify gaps         │
└───────────┬─────────────┘
            │
            ▼
┌─────────────────────────┐
│ COMPLETENESS-REPORT.md  │
│ - Coverage metrics      │
│ - Gaps with evidence    │
│ - Confidence levels     │
│ - Recommendations       │
└─────────────────────────┘
```

### 7.2 Full Chain Validation Flow

```
User Request: "Full chain validation"
     │
     ▼
┌─────────────────────────┐
│ requirements-validator- │
│ router                  │
│ - Collect all paths     │
│ - Plan full chain       │
└───────────┬─────────────┘
            │
    ┌───────┴───────┐
    │               │
    ▼               ▼
┌─────────┐   ┌─────────┐
│ Level 1 │   │ Level 2 │
│BRD→EPIC │   │PRD→Feat │
└────┬────┘   └────┬────┘
     │             │
    ┌┴─────────────┴┐
    │               │
    ▼               ▼
┌─────────┐   ┌─────────┐
│ Level 3 │   │ Level 4 │
│Feat→Story│   │Story→Code│
└────┬────┘   └────┬────┘
     │             │
     └──────┬──────┘
            ▼
┌─────────────────────────┐
│ Unified Report          │
│ - All level coverage    │
│ - Traceability matrix   │
│ - Overall assessment    │
└─────────────────────────┘
```

---

## 8. Integration Points

### 8.1 Upstream Dependencies

| Plugin | Provides | Usage |
|--------|----------|-------|
| workspace-agent | EPIC/Feature templates | Reference structure |
| business-analyst | BRD documents | Ground truth |
| product-owner | PRD documents | Ground truth |
| scrum-master | User Stories | Validation target |

### 8.2 Downstream Consumers

| Plugin | Consumes | Usage |
|--------|----------|-------|
| artifact-validator | Coverage report | Quality context |
| teams | Gap reports | Create missing items |

### 8.3 Cross-Plugin Workflow

```
┌─────────────────────────────────────────────────────────────────┐
│                    Typical Workflow                              │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  1. Teams create BRD, PRD documents                             │
│           │                                                      │
│           ▼                                                      │
│  2. Agents generate EPIC, Features, Stories                     │
│           │                                                      │
│           ▼                                                      │
│  3. requirements-validator checks completeness                  │
│           │                                                      │
│           ▼                                                      │
│  4. Teams address identified gaps                               │
│           │                                                      │
│           ▼                                                      │
│  5. Re-validate until coverage acceptable                       │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

---

## 9. Error Handling Strategy

### 9.1 File Access Errors

| Error Type | Response |
|------------|----------|
| File not found | Report error with path, continue with others |
| Permission denied | Report error, suggest fix |
| Unsupported format | List supported formats |
| Corrupted file | Report parsing failure |

### 9.2 Validation Errors

| Error Type | Response |
|------------|----------|
| No ground truth | Ask user to specify path |
| No artifacts found | Report no artifacts at path |
| Document too large | Use selective reading, warn user |

### 9.3 Error Response Format

```markdown
## Validation Errors

⚠️ **2 errors encountered:**

1. **FILE_NOT_FOUND:** ./docs/PRD.pdf
   - Path does not exist
   - Action: Verify file path

2. **PARSE_FAILURE:** ./docs/BRD.pdf (Page 15)
   - Could not extract text from scanned image
   - Action: Provide OCR version

### Files Successfully Processed
- EPIC.md ✅
- Feature-Login.md ✅
```

---

## 10. Testing Strategy

### 10.1 Test Categories

| Category | Test Files | Coverage |
|----------|------------|----------|
| EPIC Coverage | epic_coverage.test | BRD → EPIC validation |
| Feature Coverage | feature_coverage.test | PRD → Features validation |
| Story Coverage | story_coverage.test | Features → Stories validation |
| Traceability | traceability.test | Stories → Code analysis |
| Routing | routing_logic.test | Request routing |

### 10.2 Test Scenarios

```
Test Scenarios:
1. Single-level validation (BRD → EPIC)
2. Multi-level chain validation
3. Missing ground truth handling
4. Large document handling
5. Various format combinations
6. Gap detection accuracy
7. Confidence level assignment
```

### 10.3 Quality Metrics

| Metric | Target |
|--------|--------|
| Matching reliability | 70-80% |
| False positive rate | <20% |
| Evidence quality | 100% (every gap has citation) |
| Human review reminder | 100% (every report) |

---

## 11. Revision History

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | Nov 27, 2025 | AI COE Team | Initial HLD following PLUGIN_DEVELOPMENT_GUIDE.md v2.0. Multi-agent architecture with planning router pattern. Four specialized validators (EPIC, Feature, Story, Traceability). Evidence-based reporting with confidence levels. HITL mandatory for all findings. |
| 1.2 | 2025-12-19 | AI COE Team | Version bump for plugin bundle release v1.2.0 |
| 1.3 | 2026-01-09 | AI COE Team | Version bump for release v1.3.0 |
| 1.4 | 2026-01-21 | AI COE Team | Version bump for unified release v1.4.0 |

---

**Document Owner:** AI COE Team

**Document Type:** High-Level Design (HLD)

**Status:** Draft

**Consumer:** requirements-validator plugin implementation
