---
name: rai
description: Validate Responsible AI principles in requirements and implementations Use when the user requests this functionality.
---

## Agent Invocation

Use Task tool with `subagent_type="requirements-validator-router"` with the following enriched prompt:

Prompt: """
## User Request
$ARGUMENTS

## Intent: RAI (Responsible AI Validation)

### 1. Analyze RAI Scope
Determine validation areas:
- Bias and Fairness: Check for discriminatory patterns
- Transparency: Model explainability requirements
- Privacy: Data handling and consent
- Accountability: Audit trail and governance
- Safety: Harm prevention measures

### 2. Identify Artifacts to Validate
- Requirements documents (BRD, PRD, User Stories)
- Design specifications (HLD, LLD)
- Implementation code
- Test cases

### 3. RAI Checklist
For each artifact:
- Data bias considerations addressed?
- Fairness metrics defined?
- Explainability requirements present?
- Privacy controls specified?
- Human oversight mechanisms?
- Harm mitigation strategies?

### 4. Present Plan Before Execution
- RAI areas to validate
- Target artifacts
- Checklists to apply
- Wait for user confirmation

### 5. Report Findings
Generate RAI-ASSESSMENT-REPORT.md with:
- RAI principle coverage
- Gaps in responsible AI considerations
- Risk assessment
- Recommendations for improvement
"""

Request to process: $ARGUMENTS