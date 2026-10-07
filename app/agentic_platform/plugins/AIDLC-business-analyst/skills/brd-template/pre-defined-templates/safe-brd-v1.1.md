# Business Requirements Document (BRD)

**Document Version:** {{VERSION}}
**Status:** {{STATUS}}
**Date:** {{DATE}}
**Author:** {{AUTHOR}}
**Generated From:** {{GENERATED_FROM}}

---

## Document Coverage Summary

| Section | Coverage | Source |
|---------|----------|--------|
| 1. Executive Summary | {{COVERAGE:executive_summary}} | {{SOURCE:executive_summary}} |
| 2. Business Context | {{COVERAGE:business_context}} | {{SOURCE:business_context}} |
| 3. Business Outcomes | {{COVERAGE:business_outcomes}} | {{SOURCE:business_outcomes}} |
| 4. Scope Definition | {{COVERAGE:scope_definition}} | {{SOURCE:scope_definition}} |
| 5. Business Rules | {{COVERAGE:business_rules}} | {{SOURCE:business_rules}} |
| 6. User Personas | {{COVERAGE:user_personas}} | {{SOURCE:user_personas}} |
| 7. Functional Requirements | {{COVERAGE:functional_requirements}} | {{SOURCE:functional_requirements}} |
| 8. Non-Functional Requirements | {{COVERAGE:non_functional_requirements}} | {{SOURCE:non_functional_requirements}} |
| 9. Success Metrics | {{COVERAGE:success_metrics}} | {{SOURCE:success_metrics}} |
| 10. Compliance Requirements | {{COVERAGE:compliance_requirements}} | {{SOURCE:compliance_requirements}} |
| 11. Dependencies and Constraints | {{COVERAGE:dependencies_constraints}} | {{SOURCE:dependencies_constraints}} |
| 12. Risks and Mitigations | {{COVERAGE:risks_mitigations}} | {{SOURCE:risks_mitigations}} |
| 13. Ambiguities and Clarifications | {{COVERAGE:ambiguities}} | {{SOURCE:ambiguities}} |
| 14. Appendices | {{COVERAGE:appendices}} | {{SOURCE:appendices}} |

**Note:** Sections 15-16 are optional and only included if template contains these sections and traceability data is available.

---

## Table of Contents

{{AUTO_TOC}}

---

## 1. Executive Summary

### Purpose
{{SECTION:executive_summary_purpose}}

### Background
{{SECTION:executive_summary_background}}

### Scope Overview
{{SECTION:executive_summary_scope}}

### Key Stakeholders
{{SECTION:executive_summary_stakeholders}}

---

## 2. Business Context

### Problem Statement
{{SECTION:business_context_problem}}

### Current State
{{SECTION:business_context_current_state}}

### Desired Future State
{{SECTION:business_context_future_state}}

### Market Opportunity
{{SECTION:business_context_opportunity}}

### Strategic Alignment
{{SECTION:business_context_alignment}}

---

## 3. Business Outcomes

### Business Hypothesis
{{SECTION:business_outcomes_hypothesis}}

### Primary Objectives
{{SECTION:business_outcomes_objectives}}

### Leading Indicators
{{SECTION:business_outcomes_indicators}}

### Measurable Value
{{TABLE:business_outcomes_metrics}}

---

## 4. Scope Definition

### In Scope
{{SECTION:scope_definition_in_scope}}

### Out of Scope
{{SECTION:scope_definition_out_of_scope}}

### Assumptions
{{SECTION:scope_definition_assumptions}}

### Constraints
{{SECTION:scope_definition_constraints}}

---

## 5. Business Rules

### Rule Summary
{{TABLE:business_rules_summary}}

### Detailed Business Rules
{{SECTION:business_rules_details}}

---

## 6. User Personas

{{SECTION:user_personas}}

---

## 7. Functional Requirements

{{SECTION:functional_requirements}}

---

## 8. Non-Functional Requirements

### Performance
{{SECTION:nfr_performance}}

### Scalability
{{SECTION:nfr_scalability}}

### Security
{{SECTION:nfr_security}}

### Availability
{{SECTION:nfr_availability}}

### Usability
{{SECTION:nfr_usability}}

### Accessibility
{{SECTION:nfr_accessibility}}

---

## 9. Success Metrics

### Key Performance Indicators
{{TABLE:success_metrics_kpis}}

### Success Criteria
{{SECTION:success_metrics_criteria}}

### Measurement Frequency
{{SECTION:success_metrics_frequency}}

---

## 10. Compliance Requirements

### Regulatory Compliance
{{SECTION:compliance_regulatory}}

### Security Standards
{{SECTION:compliance_security}}

### Data Privacy
{{SECTION:compliance_privacy}}

### Audit Requirements
{{SECTION:compliance_audit}}

---

## 11. Dependencies and Constraints

### External Dependencies
{{TABLE:dependencies_external}}

### Internal Dependencies
{{TABLE:dependencies_internal}}

### Technical Constraints
{{SECTION:constraints_technical}}

### Business Constraints
{{SECTION:constraints_business}}

### Operational Constraints
{{SECTION:constraints_operational}}

---

## 12. Risks and Mitigations

{{TABLE:risks_mitigations}}

---

## 13. Ambiguities and Clarifications

**Purpose:** Document identified ambiguities, conflicts, and areas requiring stakeholder clarification before implementation.

### 13.1 Summary

{{TABLE:ambiguities_summary}}

**Clarity Score:** {{CLARITY_SCORE}}/100

**Overall Assessment:** {{CLARITY_ASSESSMENT}}

---

### 13.2 Conflicts Detected

{{TABLE:ambiguities_conflicts}}

{{SECTION:ambiguities_conflicts_details}}

---

### 13.3 Missing Definitions and Incomplete Rules

{{TABLE:ambiguities_gaps}}

{{SECTION:ambiguities_gaps_details}}

---

### 13.4 Vague and Unclear Requirements

{{TABLE:ambiguities_vague}}

{{SECTION:ambiguities_vague_details}}

---

### 13.5 Missing Acceptance Criteria

{{TABLE:ambiguities_missing_criteria}}

{{SECTION:ambiguities_missing_criteria_details}}

---

### 13.6 Clarity Score Breakdown

{{TABLE:ambiguities_clarity_breakdown}}

---

### 13.7 Recommended Actions

**Priority 1 (Before Design):**
{{SECTION:ambiguities_priority1}}

**Priority 2 (Before Implementation):**
{{SECTION:ambiguities_priority2}}

**Priority 3 (During Implementation):**
{{SECTION:ambiguities_priority3}}

---

## 14. Appendices

### Appendix A: Glossary
{{TABLE:appendix_glossary}}

### Appendix B: References
{{SECTION:appendix_references}}

### Appendix C: Source Documents
{{SECTION:appendix_source_documents}}

### Appendix D: Revision History
{{TABLE:appendix_revision_history}}

---

## 15. Requirement Traceability Matrix (Optional)

**Section Purpose:** Establish complete requirement lineage from Rally portfolio hierarchy to source documents.

**Generation Condition:** This section is only included if:
- Template explicitly includes this section, AND
- Epic ID provided via --epic parameter (for Rally hierarchy), OR
- Citation metadata tracked during document ingestion (for source references)

---

### 15.1 Rally Portfolio Hierarchy

**Epic→Capability→Feature Traceability**

{{TABLE:traceability_rally_hierarchy}}

**Hierarchy Validation Summary:**
{{SECTION:traceability_rally_summary}}

---

### 15.2 Requirement Source Citations

**Requirement-to-Source Document Mapping**

{{TABLE:traceability_source_citations}}

**Citation Coverage Summary:**
{{SECTION:traceability_citation_summary}}

---

## 16. Source Documents Reference (Optional)

**Section Purpose:** Complete catalog of source documents with contribution tracking.

**Generation Condition:** This section is only included if:
- Template explicitly includes this section, AND
- Citation metadata tracked during document ingestion

---

**Total Source Documents:** {{SOURCE_DOCUMENT_COUNT}}
**Document Types:** {{SOURCE_DOCUMENT_TYPES}}

{{SECTION:source_documents_catalog}}

---

**Traceability Report Generated:** {{GENERATION_TIMESTAMP}}
**Epic ID Used:** {{EPIC_ID}}
**Rally API Status:** {{RALLY_API_STATUS}}
**Documents Analyzed:** {{DOCUMENTS_ANALYZED_COUNT}}
**Requirements Traced:** {{REQUIREMENTS_TRACED_PERCENTAGE}}%

---

**Template Version:** safe-brd-v1.1
**Template Date:** 2026-02-09
**Compatible with:** AIDLC-business-analyst v1.5.0+
**Methodology:** SAFe (Scaled Agile Framework)