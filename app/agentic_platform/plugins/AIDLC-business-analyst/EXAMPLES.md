# AIDLC-business-analyst Usage Examples

This document provides detailed conversational examples showing the interview-style process flow of the AIDLC-business-analyst plugin.

---

## Quick Reference

| Example | Mode | Rally | Description |
|---------|------|-------|-------------|
| Example 1 | Interview | No | Full section-by-section review with modifications |
| Example 2 | Direct | No | Fast auto-generation for trusted sources |
| Example 3 | Interview | Yes | Custom template with Rally traceability |
| Example 4 | N/A | No | BRD validation and ambiguity detection |

---

## Example 1: Interview Mode - Section-by-Section Review

**Scenario:** Generate a BRD with full control over each section, including revisions and gap resolution.

```
User: Generate a BRD from ./project-requirements

Plugin: I'll help generate a BRD from your project requirements. Let me start by understanding the template structure.

Do you have a custom BRD template document you'd like to use? If so, please provide the file path. Otherwise, I can use a
predefined template.

User: Use the SAFE BRD template

Plugin: Great! I'll use the SAFE BRD template (v1.1). Let me process the documents in ./project-requirements.

[Processing documents...]
- Converting requirements.docx to markdown
- Converting specifications.pdf to markdown
- Reading user-stories.md

Found 3 documents totaling 2,450 lines. All documents normalized to markdown format.

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

User: Interview mode

Plugin: Excellent! I'll generate sections one at a time for your review. Let's start with the first section.

---

### Section 1: Title Page

**Proposed Content:**

# Business Requirements Document
## Customer Portal Enhancement Project

| Field | Value |
|-------|-------|
| Document ID | BRD-CP-2026-001 |
| Version | 1.0 |
| Date | February 9, 2026 |
| Project | Customer Portal Enhancement |
| Business Owner | Sarah Johnson |
| Technical Lead | Michael Chen |
| Status | Draft |

Is this section acceptable, or would you like me to revise it?

User: Looks good, continue

Plugin: Great! Moving to the next section.

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

User: Option 2

Plugin: Understood. I've marked the system architecture details as "[To be provided]" and will continue.

---

### Section 3: Business Requirements

**Proposed Content:**

| ID | Requirement | Priority | Source |
|----|-------------|----------|--------|
| BR-001 | User shall view active policies with coverage details | P0 | requirements.docx §2.1 |
| BR-002 | User shall submit claims with photo attachments (max 10MB) | P0 | requirements.docx §2.3 |
| BR-003 | User shall download policy documents (PDF) | P1 | specifications.pdf p.4 |
| BR-004 | System shall send email notifications on claim status changes | P1 | user-stories.md #12 |
| BR-005 | User shall update contact information in real-time | P2 | requirements.docx §3.2 |

Is this section acceptable, or would you like me to revise it?

User: Can you add more detail to BR-002 about the claim submission workflow?

Plugin: I'll enhance BR-002 with the claim submission workflow. Let me revise:

**Revised Content:**

| ID | Requirement | Priority | Source |
|----|-------------|----------|--------|
| BR-001 | User shall view active policies with coverage details | P0 | requirements.docx §2.1 |
| BR-002 | User shall submit claims via multi-step form: (1) select policy, (2) enter incident details (date, description,
amount), (3) upload photos (JPG/PNG, max 10MB total), (4) review and submit | P0 | requirements.docx §2.3 |
| BR-003 | User shall download policy documents (PDF) | P1 | specifications.pdf p.4 |
| BR-004 | System shall send email notifications on claim status changes | P1 | user-stories.md #12 |
| BR-005 | User shall update contact information in real-time | P2 | requirements.docx §3.2 |

Is this better?

User: Perfect, continue

Plugin: Excellent! I'll continue generating the remaining sections...

[Process continues through all sections]

---

### Final Step: Ambiguity Detection

I've completed all sections. Now running mandatory ambiguity detection to identify conflicts, gaps, and vague language...

**Ambiguities Detected:**

| ID | Section | Issue | Severity | Recommendation |
|----|---------|-------|----------|----------------|
| AMB-001 | Business Rules | Photo upload format acceptance unclear (JPG/PNG mentioned but not validated) | Medium | Define
explicit file type validation list |
| AMB-002 | NFRs | "Fast response time" - no quantifiable metric | High | Define specific latency threshold (e.g., <2s
p95) |
| AMB-003 | Business Rules | Claim amount limit not specified | High | Define maximum claim amount per submission |

I've added these to the "Ambiguities and Clarifications" section of the BRD.

**Generation Complete:**
- ✓ BRD.md created (12 sections, 2,340 lines)
- ✓ BRD.docx created (auto-converted from markdown)
- ✓ 3 high-severity ambiguities detected for stakeholder review
- ✓ 15 business requirements documented with source citations

The BRD is ready at ./project-requirements/BRD.md and ./project-requirements/BRD.docx.
```

**Key Takeaways:**
- Full control over each section with revision capability
- Interactive gap detection with resolution options
- Mandatory ambiguity analysis at the end
- Both markdown and Word formats generated automatically

---

## Example 2: Direct Mode - Fast Auto-Generation

**Scenario:** Generate a BRD quickly from well-structured, trusted source documents without section-by-section review.

```
User: Generate BRD from ./specifications --direct

Plugin: I'll generate a BRD in direct mode from ./specifications. Let me check for a custom template first.

Do you have a custom BRD template? If not, I'll use a predefined template.

User: Use Legacy Inscore template

Plugin: Using Legacy Inscore template (v1.0). Processing documents in direct mode...

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

| ID | Section | Issue | Severity | Recommendation |
|----|---------|-------|----------|----------------|
| AMB-001 | Business Rules | Rule R-003 uses "approximately" without tolerance range | Medium | Define explicit range (e.g.,
±5%) |
| AMB-002 | Business Requirements | BR-012 "user-friendly interface" lacks testable criteria | High | Define specific usability
metrics |

**Generation Complete:**
- ✓ BRD.md created (1,890 lines, 40% more concise than prose-heavy approach)
- ✓ BRD.docx created
- ✓ 2 ambiguities detected (1 high, 1 medium severity)
- ✓ 18 requirements, 12 business rules documented
- ✓ POM field mappings included per Legacy Inscore template

BRD available at ./specifications/BRD.md and ./specifications/BRD.docx.
```

**Key Takeaways:**
- No manual intervention required during generation
- Significantly faster than interview mode
- Still includes mandatory ambiguity detection
- Best for well-structured, complete source documents

---

## Example 3: Interview Mode with Rally Traceability

**Scenario:** Generate a BRD with custom template and Rally Epic hierarchy integration for complete traceability.

```
User: Generate BRD from ./business-requirements --epic E78901

Plugin: I'll generate a BRD with Rally traceability for Epic E78901. Let me start by checking for a custom template.

Do you have a custom BRD template document? Please provide the file path, or I can use a predefined template.

User: Use ./templates/company-brd-template-v2.docx

Plugin: Loading custom template from ./templates/company-brd-template-v2.docx...

Template loaded successfully! Detected 14 custom sections including:
- Executive Summary
- Strategic Alignment
- Business Context
- **Requirement Traceability Matrix** (Rally integration enabled)
- Source Documents Reference

I notice your template includes traceability sections. I'll automatically fetch the Rally Epic hierarchy for E78901.

Processing documents in ./business-requirements...
- Converting business-case.docx to markdown
- Reading functional-specs.md
- Converting stakeholder-interviews.msg to markdown

Fetching Rally hierarchy for Epic E78901...

**Rally Hierarchy Retrieved:**
- Epic E78901: Customer Experience Platform
  - Capability C12345: Self-Service Portal
    - Feature F20001: Policy Management UI
    - Feature F20002: Claims Submission Workflow
  - Capability C12346: Mobile Accessibility
    - Feature F20003: Responsive Design Framework

Now, which generation mode would you prefer?
1. Interview Mode (section-by-section review)
2. Direct Mode (auto-generate)

User: Interview mode

Plugin: Starting interview mode with custom template sections...

---

### Section 1: Executive Summary

**Proposed Content:**

Customer Experience Platform initiative addresses declining digital engagement (32% YoY) and rising operational costs
($2.4M annually in manual processing). Self-service portal + mobile-first design reduces processing costs by 65% and
improves customer retention by 18% (projected 24-month ROI).

Is this section acceptable, or would you like revisions?

User: Continue

[Process continues through custom sections...]

---

### Section 9: Requirement Traceability Matrix

**Proposed Content:**

| Epic | Capability | Feature | Business Requirements | Source Citation |
|------|------------|---------|----------------------|-----------------|
| E78901 | C12345 | F20001 | BR-001, BR-003, BR-005 | business-case.docx §2.1, functional-specs.md #4 |
| E78901 | C12345 | F20002 | BR-002, BR-004, BR-006 | functional-specs.md #8, stakeholder-interviews.msg |
| E78901 | C12346 | F20003 | BR-007, BR-008, BR-009 | functional-specs.md #12 |

This section maps all business requirements to Rally work items and source documents for complete traceability.

Is this acceptable?

User: Yes, continue

[Process continues...]

---

### Final Step: Ambiguity Detection

Running mandatory ambiguity analysis...

**Ambiguities Detected:**

| ID | Section | Issue | Severity | Recommendation |
|----|---------|-------|----------|----------------|
| AMB-001 | Strategic Alignment | ROI calculation methodology not documented | High | Reference specific cost-benefit
analysis appendix |
| AMB-002 | Business Requirements | Mobile browser support scope undefined | Medium | Define minimum browser versions
(iOS Safari, Chrome) |
| AMB-003 | NFRs | "Improve customer retention by 18%" - baseline and measurement method unclear | High | Define baseline
period and KPI calculation |

**Generation Complete:**
- ✓ BRD.md created (14 custom sections, 3,120 lines)
- ✓ BRD.docx created
- ✓ Rally traceability matrix with Epic→Capability→Feature→Requirement mapping
- ✓ Source citations for all requirements
- ✓ 3 ambiguities detected (2 high, 1 medium severity)

BRD available at ./business-requirements/BRD.md and ./business-requirements/BRD.docx.
```

**Key Takeaways:**
- Custom templates fully supported with dynamic section detection
- Rally integration automatic when template includes traceability sections or --epic flag provided
- Complete audit trail: Epic → Capability → Feature → Requirement → Source Document
- Interview mode works seamlessly with any template structure

---

## Example 4: BRD Validation and Ambiguity Detection

**Scenario:** Validate an existing BRD for completeness and detect ambiguities without regenerating it.

```
User: Check the BRD at ./output/Customer-Portal-BRD.md for ambiguities

Plugin: I'll analyze the BRD at ./output/Customer-Portal-BRD.md for ambiguities, conflicts, gaps, and vague language.

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

| Severity | Count | Percentage |
|----------|-------|------------|
| High | 6 | 35% |
| Medium | 8 | 47% |
| Low | 3 | 18% |
| **Total** | **17** | **100%** |

---

### High Severity Ambiguities (P0 - Must Resolve Before Design)

| ID | Section | Issue | Location | Recommendation |
|----|---------|-------|----------|----------------|
| AMB-001 | Business Rules | Premium calculation formula references undefined risk factors | Rules §3.4 | Define risk factor
categories, weights, and calculation method |
| AMB-002 | Business Rules | Refund calculation logic incomplete (condition specified but calculation method missing) | Rules
§5.2 | Add explicit refund formula and eligibility criteria |
| AMB-003 | Business Requirements | Conflicting file size limits: "10MB max" (BR-004) vs "5MB max" (BR-009) | Requirements §2
| Standardize file size limit across all requirements |
| AMB-004 | NFRs | "Fast response time" without quantifiable metric | NFRs §1.2 | Define latency threshold (e.g., <2s
for p95, <500ms for p50) |
| AMB-005 | Business Rules | Date range validation uses "approximately 90 days" | Rules §7.1 | Replace with exact range
(e.g., 90 ±3 days) or fixed 90 days |
| AMB-006 | Scope | Conflicting statements: "Mobile support in scope" vs "Mobile apps excluded" | Scope §2 | Clarify
whether mobile web or native apps included |

---

### Medium Severity Ambiguities (P1 - Should Clarify During Design)

| ID | Section | Issue | Location | Recommendation |
|----|---------|-------|----------|----------------|
| AMB-007 | Business Requirements | BR-007 "user-friendly interface" lacks testable criteria | Requirements §2 | Define
usability metrics (task completion rate, time-on-task) |
| AMB-008 | Business Rules | Rule R-008 uses conditional "should" instead of mandatory "shall" | Rules §4.3 | Replace "should"
with "shall" or "must" for enforceable rule |
| AMB-009 | Business Requirements | Actor role "Administrator" used but not defined in stakeholders | Requirements §3 | Add
Administrator persona/role definition |
| AMB-010 | NFRs | "Secure authentication" without specific standard reference | NFRs §2.1 | Specify OAuth 2.0, SAML,
or other protocol |
| AMB-011 | Business Rules | Incomplete if-then: condition stated but action undefined | Rules §6.2 | Add explicit action
or consequence |
| AMB-012 | Assumptions | Assumption A-003 references undefined external system "CRM v2" | Assumptions §1 | Define CRM
system or add to glossary |
| AMB-013 | Business Requirements | Missing acceptance criteria for BR-015 | Requirements §4 | Add testable acceptance
criteria |
| AMB-014 | NFRs | Performance target "support 1000 users" lacks concurrent specification | NFRs §1.3 | Clarify
concurrent vs total users |

---

### Low Severity Ambiguities (P2 - Nice to Have)

| ID | Section | Issue | Location | Recommendation |
|----|---------|-------|----------|----------------|
| AMB-015 | Introduction | Undefined acronym "POM" on first use | Introduction §1 | Add to glossary or define on first use |
| AMB-016 | Appendix | Glossary term "Policy Holder" inconsistent with "Policyholder" used elsewhere | Appendix §3 |
Standardize terminology |
| AMB-017 | Business Requirements | BR-018 uses relative term "reasonable time" | Requirements §5 | Define specific time
threshold |

---

### Recommendations

**Immediate Actions (High Severity):**
1. Resolve conflicting file size limits (AMB-003)
2. Clarify mobile support scope (AMB-006)
3. Define premium calculation formula completely (AMB-001)
4. Add quantifiable NFR metrics for performance (AMB-004)
5. Complete refund calculation logic (AMB-002)
6. Replace approximate date range with exact specification (AMB-005)

**Follow-Up Actions (Medium Severity):**
7. Define all actor roles and personas explicitly
8. Add testable acceptance criteria to all functional requirements
9. Replace conditional language ("should", "may") with mandatory terms
10. Reference specific security/authentication standards

**Enhancement Actions (Low Severity):**
11. Standardize terminology across document
12. Define all acronyms on first use
13. Replace relative terms with specific thresholds

**Risk Assessment:**
- **Design Risk:** HIGH - Conflicting requirements and incomplete rules will cause design rework
- **Implementation Risk:** HIGH - Missing formulas and undefined logic will delay development
- **Testing Risk:** MEDIUM - Vague acceptance criteria will complicate test planning

The BRD requires stakeholder clarification on 6 high-severity items before proceeding to design phase.

---

**Would you like me to:**
1. Generate a clarification question list for stakeholders
2. Create a revised BRD draft with "[TO BE CLARIFIED]" markers
3. Export this ambiguity report to a separate document
```

**Key Takeaways:**
- Comprehensive validation without regenerating the entire BRD
- Detailed ambiguity report with severity levels and recommendations
- Identifies conflicts, gaps, vague language, and missing definitions
- Provides actionable next steps for stakeholder clarification
- Risk assessment helps prioritize resolution efforts

---

## Additional Examples

### Generate EPIC Definition

```
User: Generate an EPIC from the strategic documents in ./vision-docs

Plugin: I'll analyze the strategic documents and generate an EPIC definition following SAFe methodology.

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
| ID | Capability | Description |
|----|------------|-------------|
| CAP-001 | Self-Service Portal | Policy viewing, claims submission, document access |
| CAP-002 | Mobile-First Design | Responsive UI, native-like experience, offline support |
| CAP-003 | Intelligent Notifications | Real-time alerts, personalized recommendations |

## Success Metrics
- Increase customer retention to 80%+ within 12 months
- Reduce support ticket volume by 45%
- Achieve 4.2+/5 customer satisfaction score

EPIC saved to ./vision-docs/EPIC-Digital-Customer-Engagement-Platform.md
```

---

### Extract Business Rules

```
User: Extract business rules from ./specifications/calculation-logic.pdf

Plugin: I'll extract business rules with formulas, validation logic, and error handling from the calculation document.

Reading calculation-logic.pdf...

Extracting:
- Calculation formulas
- Validation rules
- Business constraints
- Error conditions

**Business Rules Extracted:**

## Calculation Rules

| Rule ID | Description | Formula | Validation |
|---------|-------------|---------|------------|
| R-001 | Premium calculation | `Premium = BasePremium × RiskFactor × (1 + AdjustmentRate)` | RiskFactor ∈ [0.8, 2.5] |
| R-002 | Refund amount | `Refund = Premium × (DaysRemaining / TotalDays) × 0.9` | DaysRemaining > 30 |
| R-003 | Late fee | `LateFee = MIN(Premium × 0.05, 50)` | PaymentDate > DueDate + 10 days |

## Validation Rules

| Rule ID | Field | Constraint | Error Message |
|---------|-------|------------|---------------|
| V-001 | PolicyAmount | 1000 ≤ x ≤ 1000000 | "Policy amount must be between $1,000 and $1,000,000" |
| V-002 | EffectiveDate | x ≥ TODAY() | "Effective date cannot be in the past" |

Business rules saved to ./specifications/business-rules.md
```

---

## Tips for Effective Usage

### Template Selection
- **Custom Template First**: Always check if your organization has a standard template
- **Predefined Fallback**: Use SAFE BRD for comprehensive SAFe compliance or Legacy Inscore for legacy system compatibility
- **Template Structure**: Any document can become a template - the plugin uses semantic analysis to extract sections

### Mode Selection
- **Use Interview Mode When:**
  - Source documents are incomplete or ambiguous
  - You need fine-grained control over content
  - Stakeholder review required during generation
  - First time using a custom template

- **Use Direct Mode When:**
  - Source documents are complete and well-structured
  - Template structure is familiar
  - Fast turnaround needed
  - Trusted, validated source material

### Document Organization
- **Group Related Documents**: Place all requirements in a single folder for easier processing
- **Use Descriptive Names**: File names help with source citations (e.g., `stakeholder-interviews-jan2026.docx`)
- **Include Metadata**: Word documents with proper metadata (author, version) improve traceability

### Ambiguity Resolution
- **Review High-Severity First**: Focus on conflicts and missing definitions before design phase
- **Engage Stakeholders Early**: Use the ambiguity report as a structured clarification request
- **Iterate**: Run ambiguity detection multiple times as requirements evolve

### Rally Integration
- **Use --epic Flag**: Automatically fetch hierarchy when Epic ID is known
- **Template Traceability Sections**: Include "Requirement Traceability Matrix" in custom templates for automatic activation
- **Verify Epic ID**: Ensure Epic exists in Rally before generation to avoid errors

---

## Troubleshooting

### "Template not found"
**Solution**: Provide absolute or relative path to template file, or select a predefined template when prompted.

### "No documents found in directory"
**Solution**: Verify directory path contains supported formats (MD, PDF, DOCX, TXT, MSG, XLSX, PPTX).

### "Rally Epic not found"
**Solution**: Verify Epic ID format (e.g., E78901) and ensure Rally credentials are configured via `/fe:rally-install`.

### "Document conversion failed"
**Solution**: Check document is not corrupted or password-protected. The `documents` skill auto-installs Python dependencies
on first use.

### "Too many ambiguities detected"
**Solution**: This indicates source documents need refinement. Review high-severity ambiguities and clarify with stakeholders
before proceeding.
