# AIDLC-business-analyst User Guide

**Version**: 1.5.1
**Plugin**: AIDLC-business-analyst
**Purpose**: Generate Business Requirements Documents (BRDs), EPICs, business rules, and personas from source documents with AI-powered analysis

---

## Table of Contents

1. [Quick Start](#quick-start)
2. [Recommended Workflow](#recommended-workflow) (Interview Mode)
3. [Alternative: Direct Mode](#alternative-direct-mode) (Fast Auto-Generation)
4. [Validation and Exploration](#validation-and-exploration)
5. [Common Use Cases](#common-use-cases)
6. [Skill Reference](#skill-reference)
7. [Template System](#template-system)
8. [Ambiguity Detection](#ambiguity-detection)
9. [Troubleshooting](#troubleshooting)
10. [Best Practices](#best-practices)

---

## Quick Start

### How to Use This Guide

AIDLC-business-analyst uses **skill-based auto-invocation** - you describe what you need in natural language, and the appropriate skills are automatically triggered.

**Natural language examples:**
- "Generate a BRD from the documents in ./business-requirements"
- "Extract business rules from ./specifications/calculation-logic.pdf"
- "Create an EPIC from the strategic documents in ./vision-docs"
- "Validate the BRD at ./output/Customer-Portal-BRD.md for ambiguities"

This guide shows practical workflows and examples to help you get started quickly.

### Prerequisites

Before using AIDLC-business-analyst, ensure you have:

1. **Source Documents**: Business requirements in any of these formats:
   - Word Documents (*.docx)
   - PDF Files (*.pdf)
   - Markdown (*.md)
   - Text Files (*.txt)
   - Outlook Messages (*.msg)
   - Excel Spreadsheets (*.xlsx)
   - PowerPoint (*.pptx)

2. **Organized Folder**: Place all related documents in a single directory (e.g., `./requirements`)

3. **Optional - Custom Template**: A BRD template document if your organization has specific format requirements

4. **Optional - Rally Access**: Configured Rally API credentials for traceability (via `/fe:rally-install`)

### Installation Check

```bash
# Verify plugin is installed
/plugin list

# Should show: AIDLC-business-analyst v1.5.1
```

---

## Recommended Workflow

### Scenario: Generate BRD with Full Control (Interview Mode)

You have business requirements documents and want to generate a comprehensive BRD with section-by-section review and revision capability.

This workflow gives you **maximum control** and **quality assurance** through interactive review of each section.

#### Step 1: Prepare Your Files

```bash
# Project structure
your-project/
├── business-requirements/           # Your source documents
│   ├── stakeholder-requirements.docx
│   ├── functional-specifications.pdf
│   ├── user-interviews.msg
│   └── business-rules.xlsx
└── output/                          # Output directory (created automatically)
```

#### Step 2: Start BRD Generation

**Natural language (recommended):**
```
Generate a BRD from the documents in ./business-requirements
```

Or more explicitly:
```
Create a comprehensive BRD from all documents in ./business-requirements
```

**What happens:**
The plugin will process your documents and guide you through template selection and mode selection.

#### Step 3: Template Selection

**Prompt:**
```
Do you have a custom BRD template document you'd like to use? If so, please provide the file path.
Otherwise, I can use a predefined template.
```

**Options:**

**Option A: Use predefined template**
```
Use the SAFE BRD template
```
or
```
Use Legacy Inscore template
```

**Option B: Use custom template**
```
Use ./templates/company-brd-template-v2.docx
```

**Progress output:**
```
Great! I'll use the SAFE BRD template (v1.1).

Processing documents in ./business-requirements...
- Converting stakeholder-requirements.docx to markdown
- Converting functional-specifications.pdf to markdown
- Converting user-interviews.msg to markdown
- Reading business-rules.xlsx

Found 4 documents totaling 3,250 lines. All documents normalized to markdown format.
```

#### Step 4: Mode Selection

**Prompt:**
```
I can generate the BRD in two modes:

**Interview Mode** (Recommended):
- Review each section before proceeding
- Request revisions for any section
- Gaps and conflicts identified during generation
- Full control over content

**Direct Mode**:
- Auto-generate all sections
- Faster but no section-by-section review
- Best for trusted sources

Which mode would you prefer?
```

**Choose Interview Mode:**
```
Interview mode
```

**Duration:** 15-25 minutes (varies by document complexity and revision requests)

#### Step 5: Section-by-Section Review

**Section 1: Title Page**

```
---

### Section 1: Title Page

**Proposed Content:**

# Business Requirements Document
## Customer Portal Enhancement Project

| Field          | Value                       |
| -------------- | --------------------------- |
| Document ID    | BRD-CP-2026-001             |
| Version        | 1.0                         |
| Date           | February 10, 2026           |
| Project        | Customer Portal Enhancement |
| Business Owner | Sarah Johnson               |
| Technical Lead | Michael Chen                |
| Status         | Draft                       |

Is this section acceptable, or would you like me to revise it?
```

**Options:**

**Option A: Accept and continue**
```
Looks good, continue
```

**Option B: Request revision**
```
Change the document ID to BRD-2026-PORTAL-001 and add a subtitle
```

**Revision result:**
```
**Revised Content:**

# Business Requirements Document
## Customer Portal Enhancement Project
### Self-Service Platform Initiative

| Field          | Value                       |
| -------------- | --------------------------- |
| Document ID    | BRD-2026-PORTAL-001         |
| Version        | 1.0                         |
| Date           | February 10, 2026           |
| Project        | Customer Portal Enhancement |
| Business Owner | Sarah Johnson               |
| Technical Lead | Michael Chen                |
| Status         | Draft                       |

Is this better?
```

#### Step 6: Handle Gaps

**Section with gap:**

```
---

### Section 2: Introduction

**Proposed Content:**

Current customer portal lacks self-service capabilities, causing high support ticket volume (500+/month) and customer
dissatisfaction (CSAT: 2.8/5). Enhancement enables policy viewing, claims submission, document uploads, reducing support
costs by 40% and improving CSAT to 4.0+/5 within 6 months.

**Gap Identified:** Missing information about existing system architecture. Would you like to:
1. Add this information now
2. Mark as "[To be provided]" and continue
3. Skip this section
```

**Option 1: Add information**
```
Option 1: The current system is built on AngularJS 1.5 with a legacy Java backend
```

**Option 2: Defer**
```
Option 2
```

**Result:**
```
Understood. I've marked the system architecture details as "[To be provided]" and will continue.
```

#### Step 7: Mandatory Ambiguity Detection

After all sections are generated, the plugin automatically runs ambiguity detection:

```
---

### Final Step: Ambiguity Detection

I've completed all sections. Now running mandatory ambiguity detection to identify conflicts, gaps, and vague language...

**Ambiguities Detected:**

| ID      | Section        | Issue                                         | Severity | Recommendation                       |
| ------- | -------------- | --------------------------------------------- | -------- | ------------------------------------ |
| AMB-001 | Business Rules | Photo upload format acceptance unclear        | Medium   | Define explicit file type validation |
| AMB-002 | NFRs           | "Fast response time" - no quantifiable metric | High     | Define specific latency threshold    |
| AMB-003 | Business Rules | Claim amount limit not specified              | High     | Define maximum claim amount          |

I've added these to the "Ambiguities and Clarifications" section of the BRD.

**Generation Complete:**
- ✓ BRD.md created (12 sections, 2,340 lines)
- ✓ BRD.docx created (auto-converted from markdown)
- ✓ 3 high-severity ambiguities detected for stakeholder review
- ✓ 15 business requirements documented with source citations

The BRD is ready at ./business-requirements/BRD.md and ./business-requirements/BRD.docx.
```

#### Step 8: Review Output

**Generated files:**
```
business-requirements/
├── BRD.md                           # Markdown format
├── BRD.docx                         # Word format
└── .claude-processed/               # Normalized markdown (intermediate files)
    ├── stakeholder-requirements.md
    ├── functional-specifications.md
    ├── user-interviews.md
    └── business-rules.md
```

**Review the BRD:**
```bash
# View markdown version
cat business-requirements/BRD.md

# Open Word version
open business-requirements/BRD.docx
```

#### Summary: Interview Mode Workflow

```
Total time: 15-25 minutes
User interaction: High (review each section, request revisions)
Control level: Maximum
Quality assurance: Section-by-section review + mandatory ambiguity detection

Steps:
1. Prepare documents in a folder
2. Request BRD generation
3. Select template (custom or predefined)
4. Choose Interview Mode
5. Review and approve/revise each section
6. Handle gaps interactively
7. Review ambiguity detection results
8. Review final BRD.md and BRD.docx
```

**Benefits:**
- ✅ Full control over each section
- ✅ Identify and fix issues early
- ✅ Request revisions before moving forward
- ✅ Handle gaps interactively
- ✅ Automatic ambiguity detection

---

## Alternative: Direct Mode

### Scenario: Fast Auto-Generation from Trusted Sources

You have well-structured, complete source documents and need a BRD quickly without section-by-section review.

This workflow is **faster** but provides **no interactive review** during generation.

#### Step 1: Prepare Your Files

Same as Interview Mode - organize documents in a single folder.

#### Step 2: Start BRD Generation with Direct Mode Flag

**Natural language:**
```
Generate BRD from ./specifications --direct
```

Or:
```
Generate BRD from ./requirements using direct mode
```

**What happens:**
The plugin will still prompt for template selection, but will auto-generate all sections without stopping for review.

#### Step 3: Template Selection

Same as Interview Mode - choose custom or predefined template.

#### Step 4: Automatic Generation

**Progress output:**
```
Using Legacy Inscore template (v1.0). Processing documents in direct mode...

[Processing documents...]
- Converting product-spec.docx to markdown
- Reading technical-requirements.md
- Converting business-rules.xlsx to markdown

Found 3 documents, normalized to markdown.

Generating all sections automatically...

[Section 1/10] Title Page - Generated
[Section 2/10] Table of Contents - Generated
[Section 3/10] Introduction - Generated
[Section 4/10] Purpose - Generated
[Section 5/10] Scope - Generated
[Section 6/10] Business Requirements - Generated (18 requirements)
[Section 7/10] General Parameters - Generated (POM field mappings included)
[Section 8/10] Rules - Generated (12 business rules)
[Section 9/10] Non-Functional Requirements - Generated
[Section 10/10] Appendix - Generated

Running mandatory ambiguity detection...

**Ambiguities Detected:**

| ID      | Section               | Issue                                 | Severity | Recommendation           |
| ------- | --------------------- | ------------------------------------- | -------- | ------------------------ |
| AMB-001 | Business Rules        | Rule R-003 uses "approximately"       | Medium   | Define explicit range    |
| AMB-002 | Business Requirements | BR-012 "user-friendly" lacks criteria | High     | Define usability metrics |

**Generation Complete:**
- ✓ BRD.md created (1,890 lines, 40% more concise)
- ✓ BRD.docx created
- ✓ 2 ambiguities detected (1 high, 1 medium severity)
- ✓ 18 requirements, 12 business rules documented

BRD available at ./specifications/BRD.md and ./specifications/BRD.docx.
```

**Duration:** 8-12 minutes

#### Summary: Direct Mode Workflow

```
Total time: 8-12 minutes
User interaction: Low (template selection only)
Control level: Minimum
Quality assurance: Mandatory ambiguity detection only

Steps:
1. Prepare documents in a folder
2. Request BRD generation with "--direct" flag
3. Select template
4. Wait for automatic generation
5. Review ambiguity detection results
6. Review final BRD.md and BRD.docx
```

**Benefits:**
- ✅ Fast generation without interruption
- ✅ Still includes mandatory ambiguity detection
- ✅ Good for well-structured sources
- ✅ Less time investment

**Trade-offs:**
- ❌ No section-by-section review
- ❌ No revision capability during generation
- ❌ Must review entire BRD after generation

**When to use Direct Mode:**
- Source documents are complete and well-structured
- Template structure is familiar
- Fast turnaround needed
- Trusted, validated source material

---

## Validation and Exploration

### Use Case 1: Validate Existing BRD for Ambiguities

**Scenario:** You have an existing BRD and want to check it for ambiguities, conflicts, gaps, and vague language.

**Natural language:**
```
Check the BRD at ./output/Customer-Portal-BRD.md for ambiguities
```

Or:
```
Validate the BRD at ./docs/requirements for ambiguities and conflicts
```

**Progress output:**
```
I'll analyze the BRD at ./output/Customer-Portal-BRD.md for ambiguities, conflicts, gaps, and vague language.

Reading BRD...

Running comprehensive ambiguity detection across all sections:
- Conflicting statements
- Missing definitions and incomplete rules
- Vague and unclear requirements
- Missing acceptance criteria
- Undefined terms

**Analysis Complete**

### Ambiguity Report

**Summary Statistics:**

| Severity  | Count  | Percentage |
| --------- | ------ | ---------- |
| High      | 6      | 35%        |
| Medium    | 8      | 47%        |
| Low       | 3      | 18%        |
| **Total** | **17** | **100%**   |

---

### High Severity Ambiguities (P0 - Must Resolve Before Design)

| ID      | Section               | Issue                                                         | Location        | Recommendation                |
| ------- | --------------------- | ------------------------------------------------------------- | --------------- | ----------------------------- |
| AMB-001 | Business Rules        | Premium calculation formula references undefined risk factors | Rules §3.4      | Define risk factor categories |
| AMB-002 | Business Rules        | Refund calculation logic incomplete                           | Rules §5.2      | Add explicit refund formula   |
| AMB-003 | Business Requirements | Conflicting file size limits: "10MB max" vs "5MB max"         | Requirements §2 | Standardize limits            |

[Full report continues...]

---

**Would you like me to:**
1. Generate a clarification question list for stakeholders
2. Create a revised BRD draft with "[TO BE CLARIFIED]" markers
3. Export this ambiguity report to a separate document
```

**Duration:** 5-10 minutes

### Use Case 2: Explore Existing EPIC

**Scenario:** You want to understand capabilities defined in an existing EPIC document.

**Natural language:**
```
What capabilities are defined in the EPIC at ./epic.md?
```

Or:
```
Analyze the EPIC document at ./strategic-initiatives/digital-platform-EPIC.md
```

**Output:**
```
Analyzing EPIC: Digital Customer Engagement Platform

## Capabilities Identified

### CAP-001: Self-Service Portal
- Policy viewing, claims submission, document access
- Reduces support ticket volume by 45%
- Target: 80% of customers use self-service within 6 months

### CAP-002: Mobile-First Design
- Responsive UI, native-like experience, offline support
- Improves mobile CSAT from 2.1 to 4.2+
- Target: 60% of users access via mobile

### CAP-003: Intelligent Notifications
- Real-time alerts, personalized recommendations
- Increases customer engagement by 34%
- Target: 50% notification open rate

## Success Metrics
- Increase customer retention to 80%+ within 12 months
- Reduce support ticket volume by 45%
- Achieve 4.2+/5 customer satisfaction score

## Dependencies
- API modernization initiative (dependency)
- Legacy data migration (blocker)
```

**Duration:** 2-5 minutes

### Use Case 3: Generate Artifacts from Requirements

**Scenario:** You want to generate an EPIC definition or extract business rules from source documents.

#### Generate EPIC

**Natural language:**
```
Generate an EPIC from the strategic documents in ./vision-docs
```

Or:
```
Create an EPIC from ./requirements/strategic-vision.docx
```

**Output:**
```
I'll analyze the strategic documents and generate an EPIC definition following SAFe methodology.

Reading strategic documents...
- vision-2026.md
- market-analysis.docx
- stakeholder-requirements.pdf

Extracting:
- Strategic context and business problem
- Capabilities and value streams
- Non-functional requirements
- Success metrics

**EPIC Generated:**

# EPIC: Digital Customer Engagement Platform

## Business Context
Market share declining 12% YoY due to competitors' superior digital experience. Customer acquisition cost increased 34%
while retention dropped to 68% (industry avg: 82%).

## Capabilities
| ID      | Capability                | Description                                            |
| ------- | ------------------------- | ------------------------------------------------------ |
| CAP-001 | Self-Service Portal       | Policy viewing, claims submission, document access     |
| CAP-002 | Mobile-First Design       | Responsive UI, native-like experience, offline support |
| CAP-003 | Intelligent Notifications | Real-time alerts, personalized recommendations         |

## Success Metrics
- Increase customer retention to 80%+ within 12 months
- Reduce support ticket volume by 45%
- Achieve 4.2+/5 customer satisfaction score

EPIC saved to ./vision-docs/EPIC-Digital-Customer-Engagement-Platform.md
```

**Duration:** 5-8 minutes

#### Extract Business Rules

**Natural language:**
```
Extract business rules from ./specifications/calculation-logic.pdf
```

Or:
```
Extract business rules from this document ./docs/validation-requirements.docx
```

**Output:**
```
I'll extract business rules with formulas, validation logic, and error handling from the calculation document.

Reading calculation-logic.pdf...

Extracting:
- Calculation formulas
- Validation rules
- Business constraints
- Error conditions

**Business Rules Extracted:**

## Calculation Rules

| Rule ID | Description         | Formula                                                     | Validation                      |
| ------- | ------------------- | ----------------------------------------------------------- | ------------------------------- |
| R-001   | Premium calculation | `Premium = BasePremium × RiskFactor × (1 + AdjustmentRate)` | RiskFactor ∈ [0.8, 2.5]         |
| R-002   | Refund amount       | `Refund = Premium × (DaysRemaining / TotalDays) × 0.9`      | DaysRemaining > 30              |
| R-003   | Late fee            | `LateFee = MIN(Premium × 0.05, 50)`                         | PaymentDate > DueDate + 10 days |

## Validation Rules

| Rule ID | Field         | Constraint         | Error Message                                         |
| ------- | ------------- | ------------------ | ----------------------------------------------------- |
| V-001   | PolicyAmount  | 1000 ≤ x ≤ 1000000 | "Policy amount must be between $1,000 and $1,000,000" |
| V-002   | EffectiveDate | x ≥ TODAY()        | "Effective date cannot be in the past"                |

Business rules saved to ./specifications/business-rules.md
```

**Duration:** 5-10 minutes

---

## Common Use Cases

### Use Case 1: First-Time BRD Generation

**Scenario:** You have business requirements documents and need a comprehensive BRD for the first time.

**Steps:**
1. Organize documents: Place all requirements in `./requirements/`
2. Generate BRD: `Generate a BRD from ./requirements`
3. Select template: Choose SAFE BRD or custom template
4. Choose Interview Mode for first-time generation
5. Review each section and request revisions as needed
6. Review ambiguity report
7. Share BRD.docx with stakeholders

**Time:** 15-25 minutes

---

### Use Case 2: BRD with Rally Traceability

**Scenario:** Generate a BRD with Rally Epic hierarchy integration for complete traceability.

**Prerequisites:**
- Rally API credentials configured (`/fe:rally-install`)
- Rally Epic ID known (e.g., E78901)

**Steps:**
1. Generate with Epic flag: `Generate BRD from ./business-requirements --epic E78901`
2. Select custom template with traceability sections (or SAFE BRD)
3. Choose Interview or Direct mode
4. Plugin automatically fetches Rally hierarchy
5. Review traceability matrix linking Epic→Capability→Feature→Requirement

**Output includes:**
- Requirement Traceability Matrix with Rally hierarchy
- Source document citations
- Complete audit trail

**Time:** 18-30 minutes

**Example traceability matrix:**
```markdown
## Requirement Traceability Matrix

| Epic   | Capability | Feature | Business Requirements  | Source Citation         |
| ------ | ---------- | ------- | ---------------------- | ----------------------- |
| E78901 | C12345     | F20001  | BR-001, BR-003, BR-005 | business-case.docx §2.1 |
| E78901 | C12345     | F20002  | BR-002, BR-004, BR-006 | functional-specs.md #8  |
| E78901 | C12346     | F20003  | BR-007, BR-008, BR-009 | functional-specs.md #12 |
```

---

### Use Case 3: Custom Template with Organization Standards

**Scenario:** Your organization has specific BRD format requirements that must be followed.

**Prerequisites:**
- Custom BRD template document (DOCX or MD format)

**Steps:**
1. Prepare custom template: `./templates/company-brd-v2.docx`
2. Generate BRD: `Generate a BRD from ./requirements`
3. When prompted for template: `Use ./templates/company-brd-v2.docx`
4. Plugin extracts section structure from template
5. Choose Interview or Direct mode
6. BRD output matches template structure exactly

**Template features:**
- AI-powered semantic analysis extracts sections automatically
- Any document can become a template
- Custom section names, ordering, and structure preserved
- Dynamic adaptation to template requirements

**Time:** 15-25 minutes

---

### Use Case 4: Rapid Iteration with Direct Mode

**Scenario:** You're refining requirements and need to quickly regenerate BRDs as documents change.

**Prerequisites:**
- Well-structured, complete source documents
- Familiar with template structure

**Steps:**
1. Update source documents in `./requirements/`
2. Regenerate: `Generate BRD from ./requirements --direct`
3. Use same template as before (plugin remembers)
4. Review output and ambiguity report
5. Iterate as needed

**Time per iteration:** 8-12 minutes

**Benefits:**
- Fast turnaround
- Consistent format
- Automatic ambiguity detection
- No manual intervention required

---

### Use Case 5: Quality Assurance and Ambiguity Resolution

**Scenario:** You have a BRD from another source and need to validate it for quality issues.

**Steps:**
1. Validate BRD: `Check the BRD at ./output/BRD.md for ambiguities`
2. Review ambiguity report (High/Medium/Low severity)
3. Generate clarification questions: Choose option 1 from prompt
4. Share with stakeholders for clarification
5. Update BRD with resolutions
6. Re-validate: `Validate the updated BRD at ./output/BRD.md`

**Time:** 5-15 minutes (depending on BRD size)

**Ambiguity categories detected:**
- Conflicting statements
- Missing definitions and incomplete rules
- Vague and unclear requirements
- Missing acceptance criteria
- Undefined terms

---

### Use Case 6: Multi-Format Document Ingestion

**Scenario:** Your requirements are spread across different file formats (Word, PDF, Excel, Email).

**Supported formats:**
- Word (*.docx)
- PDF (*.pdf)
- Markdown (*.md)
- Text (*.txt)
- Excel (*.xlsx)
- PowerPoint (*.pptx)
- Outlook Messages (*.msg)

**Steps:**
1. Organize all documents in one folder
2. Generate BRD: `Generate a BRD from ./mixed-format-requirements`
3. Plugin automatically converts all to normalized markdown
4. Choose mode and proceed with generation

**Example:**
```
mixed-format-requirements/
├── requirements.docx
├── technical-specs.pdf
├── stakeholder-emails.msg
├── data-mappings.xlsx
└── presentation.pptx
```

**Time:** 15-30 minutes (varies by document count and complexity)

---

## Skill Reference

### ba-brd - BRD Generation Skill

**Primary skill for direct BRD generation** with dual-mode workflow, template selection, and complete end-to-end processing.

**Natural language triggers:**
- "Generate a BRD from..."
- "Create a BRD from..."
- "Generate BRD using..."

**Features:**
- Template selection workflow (custom → predefined)
- Dual generation modes (interview vs direct)
- Optimized document processing (60-70% token reduction)
- Content Conciseness Principles (40-50% verbosity reduction)
- Mandatory ambiguity detection
- Template-conditional traceability
- Complete end-to-end workflow

**Output:**
- `BRD.md` - Markdown format
- `BRD.docx` - Word format
- Ambiguity report embedded in BRD

---

### ba-generate - Artifact Generation Router

**Routes generation requests** to specialized generators (EPIC, Business Rules, BRD).

**Natural language triggers:**
- "Generate an EPIC from..."
- "Extract business rules from..."
- "Create personas from..."

**Output:** Varies by artifact type

---

### ba-explore - Exploration Skill

**Explore and understand existing artifacts** including EPICs, business rules, personas, and relationships.

**Natural language triggers:**
- "What capabilities are defined in..."
- "Analyze the EPIC at..."
- "Show me the business rules in..."

**Output:** Analysis and structured information

---

### ba-validate - Validation Router

**Routes validation requests** to artifact validator or ambiguity detector.

**Natural language triggers:**
- "Validate the BRD at..."
- "Check for ambiguities in..."
- "Check completeness of..."

**Output:** Validation report with findings

---

### documents - Document Conversion Skill

**Converts between formats** - automatically used by BRD generation.

**Supported conversions:**
- DOCX ↔ Markdown
- PDF → Markdown
- XLSX → Markdown
- PPTX → Markdown
- MSG → Markdown
- CSV ↔ XLSX

**Features:**
- Auto-installs dependencies on first use
- Excel range extraction
- Table detection in PDFs
- Attachment extraction from MSG

---

### brd-template - Template Parser Skill

**AI-powered template conversion** using semantic analysis.

**Predefined templates:**
- **SAFE BRD** - Comprehensive SAFe methodology (15 sections)
- **Legacy Inscore** - Organization legacy format (10 sections)

**Features:**
- Converts ANY document to reusable template
- Dynamic section extraction
- Semantic analysis of structure
- Custom template support

---

### rally-hierarchy - Rally Integration Skill

**Fetches Rally portfolio hierarchy** for traceability matrices.

**Prerequisites:**
- Rally API credentials (`/fe:rally-install`)
- Epic ID

**Usage:**
```
Generate BRD from ./docs --epic E12345
```

**Output:** Epic→Capability→Feature hierarchy table

---

### gap-resolver - Interactive Gap Resolution

**Interactive gap filling** with targeted questions.

**Triggered automatically during Interview Mode** when gaps detected.

---

### conflict-resolver - Ambiguity Resolution

**Interactive conflict resolution** by presenting options and reconciling inconsistencies.

**Triggered automatically** when conflicts detected during validation.

---

## Template System

### Overview

AIDLC-business-analyst uses a **template-driven architecture** where BRD structure adapts to custom or predefined templates.

### Predefined Templates

#### SAFE BRD Template (v1.1)

**Purpose:** Comprehensive SAFe methodology compliance

**Sections (15):**
1. Title Page
2. Tracking Page (version control, review, approval)
3. Table of Contents
4. Introduction
5. Purpose
6. Scope
7. Assumptions
8. Business Requirements
9. Additional Context
10. Rules (business rules and validation)
11. Non-Functional Requirements
12. Ambiguities and Clarifications (mandatory)
13. Requirement Traceability Matrix (template-conditional)
14. Source Documents Reference (template-conditional)
15. Appendix

**Use when:**
- Following SAFe methodology
- Need comprehensive documentation
- External stakeholders require formal structure
- Audit/compliance requirements

**Selection:**
```
Use the SAFE BRD template
```

---

#### Legacy Inscore Template (v1.0)

**Purpose:** Organization-specific legacy format for backward compatibility

**Sections (10):**
1. Document Version Control
2. Review and Approval
3. Table of Contents
4. Introduction
5. Purpose and Scope
6. Assumptions
7. Business Requirements
8. General Parameters (screen mockups, POM mapping)
9. Rules
10. Appendix

**Use when:**
- Integrating with legacy systems
- Organization-specific format requirements
- POM field mapping needed
- Maintaining consistency with existing BRDs

**Selection:**
```
Use Legacy Inscore template
```

---

### Custom Templates

**Any document can become a template** using AI-powered semantic analysis.

#### Creating a Custom Template

**Step 1: Prepare template document**
- Use existing BRD as starting point
- Include all required sections
- Use clear section headings
- Add any organization-specific sections

**Step 2: Save template**
```
./templates/company-brd-v2.docx
```

**Step 3: Use during generation**
```
Generate a BRD from ./requirements
# When prompted:
Use ./templates/company-brd-v2.docx
```

#### Template Features

**Semantic Analysis:**
- Automatically detects section structure
- Identifies headings, subheadings, tables
- Preserves custom ordering
- Adapts to any section names

**Dynamic Sections:**
- Plugin generates content for each detected section
- Custom sections supported
- Optional sections handled gracefully
- Traceability sections trigger Rally integration

**Template Reuse:**
- Templates can be reused across projects
- Shared with team members
- Version controlled
- Organization-wide standardization

---

## Ambiguity Detection

### Overview

**Mandatory feature** that automatically detects ambiguities, conflicts, and gaps in all generated BRDs.

### What is Detected

#### 1. Conflicting Statements

**Examples:**
- Data type conflicts: "User ID is string" vs "User ID is number"
- Opposing requirements: "must allow" vs "must prevent"
- Contradictory limits: "max 10MB" vs "max 5MB"
- Boundary conflicts: different thresholds

**Severity:** High

---

#### 2. Missing Definitions and Incomplete Rules

**Examples:**
- Undefined data fields: "validate creditScore" (field not defined)
- Incomplete if-then: "if premium > 1000" (then what?)
- Missing validation: "email address required" (format validation?)
- Undefined actors: "Administrator approves" (who is Administrator?)

**Severity:** High or Medium

---

#### 3. Vague and Unclear Requirements

**Examples:**
- Subjective terms: "fast response time", "user-friendly interface"
- Approximate qualifiers: "approximately 90 days", "around $500"
- Conditional terms: "should validate", "may require", "might need"
- Undefined modifiers: "reasonable effort", "sufficient security"

**Severity:** Medium or High

---

#### 4. Missing Acceptance Criteria

**Examples:**
- Functional requirements without testable criteria
- Non-testable criteria: "improve user experience"
- Incomplete process steps: missing actor, action, or outcome

**Severity:** Medium

---

### Severity Levels

| Severity   | Definition                       | Action Required                       |
| ---------- | -------------------------------- | ------------------------------------- |
| **High**   | Must resolve before design phase | Immediate stakeholder clarification   |
| **Medium** | Should clarify during design     | Clarification during design iteration |
| **Low**    | Nice to have improvements        | Address during documentation review   |

### Example Ambiguity Report

```markdown
## Ambiguities and Clarifications

### Summary Statistics

| Severity  | Count  | Percentage |
| --------- | ------ | ---------- |
| High      | 6      | 35%        |
| Medium    | 8      | 47%        |
| Low       | 3      | 18%        |
| **Total** | **17** | **100%**   |

### High Severity Ambiguities (P0 - Must Resolve Before Design)

| ID     | Section               | Issue                                           | Recommendation                     |
| ------ | --------------------- | ----------------------------------------------- | ---------------------------------- |
| AMB-01 | Business Rules        | Premium calculation formula lacks specification | Define risk factor mappings        |
| AMB-02 | Business Rules        | Refund calculation logic undefined              | Define eligibility and calculation |
| AMB-03 | Business Requirements | Conflicting file size: "10MB" vs "5MB"          | Standardize limit                  |
| AMB-04 | NFRs                  | "Fast response time" without metric             | Define latency (e.g., <2s p95)     |

### Medium Severity Ambiguities (P1 - Should Clarify During Design)

| ID     | Section               | Issue                                    | Recommendation              |
| ------ | --------------------- | ---------------------------------------- | --------------------------- |
| AMB-05 | Business Requirements | "user-friendly interface" lacks criteria | Define usability metrics    |
| AMB-06 | Business Rules        | Conditional "should" instead of "shall"  | Replace with mandatory term |
| AMB-07 | Business Requirements | Undefined actor role "Administrator"     | Add role definition         |

### Low Severity Ambiguities (P2 - Nice to Have)

| ID     | Section      | Issue                                | Recommendation            |
| ------ | ------------ | ------------------------------------ | ------------------------- |
| AMB-08 | Introduction | Undefined acronym "POM" on first use | Define or add to glossary |
```

### Benefits

- **Early Issue Detection** - Identify problems before costly design/implementation rework
- **Reduced Rework** - Clarify requirements upfront
- **Quality Assurance** - Ensure requirements are clear, complete, testable
- **Stakeholder Alignment** - Provide specific questions for clarification
- **Risk Mitigation** - Surface conflicting assumptions early

---

## Troubleshooting

### Issue: "No documents found in directory"

**Error:**
```
❌ No documents found in ./requirements
```

**Solutions:**
1. Verify directory exists: `ls ./requirements`
2. Check file extensions (MD, PDF, DOCX, TXT, MSG, XLSX, PPTX)
3. Use absolute path: `Generate BRD from /Users/me/project/requirements`
4. Provide specific file: `Generate BRD from ./docs/requirements.docx`

---

### Issue: "Template not found"

**Error:**
```
❌ Custom template not found: ./templates/company-brd.docx
```

**Solutions:**
1. Verify file exists: `ls -la ./templates/company-brd.docx`
2. Use absolute path: `/Users/me/templates/company-brd.docx`
3. Fall back to predefined: `Use the SAFE BRD template`

---

### Issue: "Document conversion failed"

**Error:**
```
❌ Failed to convert requirements.docx to markdown
```

**Solutions:**
1. Check file is not corrupted: Open in Word/Excel
2. Verify file is not password-protected
3. Ensure `documents` skill dependencies installed (auto-installs on first use)
4. Try converting manually: Convert to PDF first, then use PDF

---

### Issue: "Rally Epic not found"

**Error:**
```
❌ Rally Epic E78901 not found or inaccessible
```

**Solutions:**
1. Verify Epic ID format: `E78901` (uppercase E + digits)
2. Check Rally credentials: `/fe:rally-install`
3. Verify Epic exists in Rally
4. Check workspace/project selection: `/fe:rally-set-project`
5. Generate without Rally: Omit `--epic` flag

---

### Issue: "Too many ambiguities detected"

**Output:**
```
⚠️ 45 ambiguities detected (32 high, 13 medium)
```

**Interpretation:**
This is not an error - it indicates source documents need refinement before proceeding.

**Solutions:**
1. Review high-severity ambiguities first
2. Clarify with stakeholders
3. Update source documents
4. Regenerate BRD: `Generate BRD from ./requirements`

**Note:** High ambiguity count is valuable feedback - address issues early rather than discovering them during implementation.

---

### Issue: "BRD generation interrupted"

**Scenario:** Generation stopped mid-process (Interview Mode section review, or unexpected error)

**Solutions:**
1. **If interrupted during Interview Mode:**
   - Re-run: `Generate a BRD from ./requirements`
   - Plugin will start fresh (previous partial generation not saved)
   - Consider using Direct Mode for faster completion

2. **If interrupted during Direct Mode:**
   - Re-run: `Generate BRD from ./requirements --direct`
   - Generation will complete uninterrupted

**Note:** Current version does not support resume from checkpoint (future enhancement).

---

### Issue: "Missing POM field mappings in Legacy Inscore template"

**Context:** Legacy Inscore template includes General Parameters section with POM field mappings.

**Error:**
```
⚠️ POM schema not found - General Parameters section may be incomplete
```

**Solutions:**
1. Provide POM schema: Place `pom-schema.json` in project root
2. Accept incomplete section: Generation continues with gap marked "[To be provided]"
3. Use SAFE BRD template instead: Does not require POM schema

---

### Issue: "Section revision not applied correctly"

**Scenario:** During Interview Mode, requested revision not reflected in output.

**Solutions:**
1. Be specific in revision request:
   - ❌ "Add more detail"
   - ✅ "Add validation rules for email format and date range"
2. Review proposed revision before approving
3. Request another revision if needed: "Revise again and include..."

---

## Best Practices

### 1. Organize Documents Before Generation

**Good structure:**
```
project/
├── requirements/
│   ├── business-case.docx
│   ├── functional-specs.pdf
│   ├── stakeholder-interviews.msg
│   └── data-mappings.xlsx
└── output/
    ├── BRD.md
    └── BRD.docx
```

**Benefits:**
- Faster processing
- Better source citations
- Easier to track document lineage

---

### 2. Use Interview Mode for First-Time Generation

**Rationale:**
- Catch issues early
- Request revisions before completion
- Understand what plugin extracts from documents
- Build confidence in output quality

**Switch to Direct Mode after:**
- Template structure is familiar
- Source documents are well-structured
- Fast iteration needed

---

### 3. Choose the Right Template

| Scenario                     | Template        | Rationale                                    |
| ---------------------------- | --------------- | -------------------------------------------- |
| SAFe methodology compliance  | SAFE BRD        | 15 comprehensive sections                    |
| Legacy system integration    | Legacy Inscore  | POM mapping, organization format             |
| Organization-specific format | Custom template | Match exact requirements                     |
| First time using plugin      | SAFE BRD        | Comprehensive structure, good starting point |

---

### 4. Review Ambiguity Report Immediately

**After generation:**
1. Review Summary Statistics - what's the overall quality?
2. Focus on High Severity first - these block design phase
3. Generate clarification questions for stakeholders
4. Update source documents with clarifications
5. Regenerate BRD to verify improvements

**Ambiguity Count Benchmarks:**
- **0-5 High:** Excellent - minor clarifications needed
- **6-15 High:** Good - focused stakeholder review needed
- **16-30 High:** Fair - significant clarification required
- **31+ High:** Poor - source documents need substantial refinement

---

### 5. Use Source Citations for Traceability

**BRD includes citations:**
```markdown
| BR-001 | User shall view active policies | P0 | requirements.docx §2.1 |
| BR-002 | User shall submit claims | P0 | functional-specs.pdf p.4 |
```

**Benefits:**
- Verify accuracy against source
- Track requirement lineage
- Support audit/compliance
- Enable stakeholder validation

---

### 6. Version Your BRDs

**File naming:**
```
BRD-Customer-Portal-v1.0.docx
BRD-Customer-Portal-v1.1.docx
BRD-Customer-Portal-v2.0.docx
```

**Track changes:**
- Update version in Title Page during regeneration
- Maintain CHANGELOG section in Appendix
- Archive previous versions

---

### 7. Combine Interview Mode with Gap Resolution

**During Interview Mode:**
- Choose "Option 1: Add information now" for critical gaps
- Choose "Option 2: Mark [To be provided]" for non-critical gaps
- Defer to stakeholders for business-specific decisions

**After generation:**
- Review all "[To be provided]" markers
- ADLC missing information
- Regenerate with complete documents

---

### 8. Leverage Rally Integration for Compliance

**When compliance/traceability required:**
```
Generate BRD from ./requirements --epic E12345
```

**Benefits:**
- Complete Epic→Capability→Feature→Requirement chain
- Regulatory audit support
- Stakeholder transparency
- Bi-directional traceability

---

### 9. Use Custom Templates for Team Consistency

**Create organizational template:**
1. Generate initial BRD with SAFE template
2. Customize sections for organization needs
3. Save as `company-standard-brd-v1.0.docx`
4. Share with team
5. Use consistently: `Use ./templates/company-standard-brd-v1.0.docx`

**Benefits:**
- Team-wide consistency
- Reduced review time
- Stakeholder familiarity
- Compliance with internal standards

---

### 10. Iterate Based on Feedback

**Workflow:**
1. Generate BRD → Share with stakeholders
2. Collect feedback on ambiguities and gaps
3. Update source documents with clarifications
4. Regenerate BRD with Direct Mode (faster)
5. Verify ambiguity count decreased
6. Repeat until High Severity < 5

**Each iteration:**
- Takes 8-12 minutes (Direct Mode)
- Improves quality incrementally
- Converges toward high-quality BRD

---

## Additional Resources

### Plugin Documentation
- **README**: `plugins/AIDLC-business-analyst/README.md`
- **CHANGELOG**: `plugins/AIDLC-business-analyst/CHANGELOG.md`
- **Examples**: `plugins/AIDLC-business-analyst/EXAMPLES.md`
- **Acceptance Criteria**: `plugins/AIDLC-business-analyst/acceptance-criteria.md`

### Related Plugins
- **AIDLC-design**: Technical design specifications (Feature-TDD, UI-TDD, Service-TDD)
- **AIDLC-scrum-master**: Rally API integration and SAFe workflows
- **AIDLC-tech-architect**: Architecture design and ADRs
- **fe plugin**: Rally setup commands (`/fe:rally-install`, `/fe:rally-set-project`)

### SAFe Resources
- **SAFe Framework**: https://scaledagileframework.com/
- **EPIC Definition**: https://scaledagileframework.com/epic/
- **Business Requirements**: https://scaledagileframework.com/nonfunctional-requirements/

---

## Frequently Asked Questions

### Q: Can I use Interview Mode but skip some sections?

**A:** Yes, when presented with a section:
- Choose "Skip this section" to omit it from final BRD
- Section will not appear in output
- Useful for optional sections not relevant to your project

---

### Q: Can I regenerate just one section of a BRD?

**A:** No, current version regenerates entire BRD. Workaround:
1. Generate full BRD
2. Manually edit specific section in BRD.md
3. Regenerate BRD.docx from BRD.md using `documents` skill

---

### Q: How do I update an existing BRD with new requirements?

**A:** Two approaches:

**Approach 1: Full regeneration**
1. Add new documents to source folder
2. Regenerate: `Generate BRD from ./requirements --direct`
3. Compare with previous version

**Approach 2: Manual merge**
1. Generate BRD from new documents only
2. Manually merge new requirements into existing BRD
3. Validate: `Check the BRD at ./output/BRD.md for ambiguities`

---

### Q: Can I customize the ambiguity detection rules?

**A:** No, ambiguity detection rules are built-in. However:
- You can ignore Low Severity ambiguities if acceptable
- Focus on High/Medium for critical quality
- Use ambiguity report as guidance, not strict enforcement

---

### Q: Does the plugin support languages other than English?

**A:** Currently English only. Documents in other languages may:
- Be processed but with degraded quality
- Result in more ambiguities detected
- Require manual review and refinement

---

### Q: Can I use the plugin offline?

**A:** No, the plugin requires:
- Claude API access (internet connection)
- Rally API (if using `--epic` flag)

---

### Q: How do I share templates with my team?

**A:** Store templates in version control:
```bash
# Add to git
git add templates/company-brd-v1.0.docx
git commit -m "Add company BRD template"
git push

# Team members pull
git pull

# Everyone uses same template
Generate a BRD from ./requirements
# When prompted:
Use ./templates/company-brd-v1.0.docx
```

---

**Questions or Issues?**

- Review this guide's troubleshooting section
- Check plugin README: `plugins/AIDLC-business-analyst/README.md`
- See examples: `plugins/AIDLC-business-analyst/EXAMPLES.md`
- Review test cases: `plugins/AIDLC-business-analyst/__tests__/`

---

**Version History:**
- v1.0.0 - Initial user guide release (2026-02-10)
