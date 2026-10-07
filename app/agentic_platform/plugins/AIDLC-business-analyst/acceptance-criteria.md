# AIDLC-business-analyst - Acceptance Criteria

## Overview
The AIDLC-business-analyst plugin extracts business knowledge from documents to generate EPICs, BRDs, business rules, and personas. Version 1.5.0 introduces template-driven architecture and skill-based auto-invocation with natural language prompts.

## Must-Haves

### 1. BRD Generation - Document Ingestion and Generation (US774989)
**As a** Business Analyst,
**I want to** request BRD generation from a folder of documents using natural language,
**So that** I can automatically generate a formatted BRD from various input files.

#### Acceptance Criteria
1. **Command accepts a path to documents (folder or files)**
   - Accept both absolute and relative paths
   - Support both individual files and folders containing multiple documents
   - Validate path exists and is accessible
   - Gracefully handle invalid paths with clear error messages

2. **Reads and extracts content from all supported file types**
   - Markdown files (*.md) - Direct read via Read tool
   - PDF files (*.pdf) - Extract via documents skill (text and tables)
   - Word documents (*.docx) - Extract via documents skill (full content)
   - Excel spreadsheets (*.xlsx) - Extract via documents skill (tables, specific sheets/ranges)
   - PowerPoint presentations (*.pptx) - Extract via documents skill (slide content)
   - Text files (*.txt) - Direct read via Read tool
   - Outlook messages (*.msg) - Extract via documents skill (content, headers, attachments)
   - Skip unsupported file types with warning message
   - Process all valid files in the specified path

3. **Generates a .docx BRD using the template format**
   - Create both BRD.md (markdown) and BRD.docx (Word) outputs
   - Follow the template structure (default or custom):
     - Default template includes: Executive Summary, Business Context, Business Outcomes, Scope Definition, Business Rules, User Personas, Functional Requirements, Non-Functional Requirements, Success Metrics, Compliance Requirements, Dependencies and Constraints, Risks and Mitigations, Ambiguities and Clarifications, Appendices
     - Custom templates can have different section names, counts, and ordering
     - Template structure is dynamically extracted, not hardcoded
   - Use documents skill for .md to .docx conversion

4. **Organizes extracted content into appropriate BRD sections**
   - Intelligently map content to correct sections using semantic analysis
   - Identify business context, problem statements, objectives
   - Extract and structure business rules, validation logic
   - Identify stakeholders and create persona profiles
   - Categorize functional vs non-functional requirements
   - Recognize compliance and regulatory mentions
   - Identify dependencies, constraints, and risks

5. **Handles missing or incomplete information gracefully**
   - Use "[Not available in source documents]" for missing sections
   - Generate BRD even with incomplete information (graceful degradation)
   - Never leave sections blank without explanation
   - Provide clear indication of which sections had sufficient vs insufficient source data
   - Include summary of coverage at the top of the BRD

#### Key Implementation Details
- **Direct generation**: No plan-confirm workflow by default
- **SAFe methodology**: Follow SAFe terminology for EPICs, capabilities, features
- **Dual format output**: Both markdown and Word document formats
- **Template-driven**: Dynamically adapts to provided or default template structure
- **Skill-based invocation**: Natural language prompts automatically trigger appropriate skills

**Test Coverage:**
- `__tests__/brd_command_document_ingestion.test`

**Related Files:**
- Skill: `skills/ba-brd/` (BRD generation skill)
- Skill: `skills/brd-template/` (template parser)
- Agent: `agents/brd-generator.md` (BRD generation logic)
- Agent: `agents/business-analyst.md` (router agent)

### 2. EPIC Definition Extraction
Given business requirements documents in a specified folder, when I request EPIC generation using natural language (e.g., "Generate an EPIC from these documents"), then the plugin must extract and structure EPIC information including business context, capabilities, NFRs, and stakeholders.

**Test Coverage:**
- `__tests__/epic_extraction.test`
- `__tests__/capabilities_and_nfrs.test`

### 3. Business Rules Extraction
Given requirements documents, when I request business rules extraction using natural language (e.g., "Extract business rules from these requirements"), then the plugin must identify and create structured business rules with formulas, validation logic, and error handling specifications.

**Test Coverage:**
- `__tests__/business_rules_extraction.test`

### 4. Persona Creation
Given stakeholder information in source documents, when the plugin analyzes requirements, then it must create detailed user personas with demographics, goals, pain points, and behaviors.

**Test Coverage:**
- `__tests__/persona_creation.test`
- `__tests__/stakeholder_analysis.test`

### 5. Comprehensive BRD Generation with Plan-Confirm
Given all extracted business artifacts (EPIC, business rules, personas), when I request BRD generation with confirmation (e.g., "Generate a BRD and let me review the plan first"), then the plugin must present a plan, obtain user confirmation, and generate a comprehensive Business Requirements Document that synthesizes all business intelligence.

**Test Coverage:**
- `__tests__/brd_generation.test`
- `__tests__/plan_confirm_workflow.test`

### 6. Ambiguity Detection and Reporting (US789392)
**As a** Business Analyst,
**I want to** detect and report ambiguities in requirements,
**So that** I can clarify gaps before finalizing the BRD.

#### Acceptance Criteria

**AC1: Agent detects conflicting statements across documents**

Goal: Identify contradictions between Epic BRD, Capability BRD, and reference documents.

Details:
- Compare statements across all source documents to find semantic conflicts
- Detect different data types for same field (e.g., "User ID is string" vs "User ID is number")
- Identify opposing requirements (must vs. must not)
- Find contradictory business rules (different values, thresholds, limits)
- Use semantic analysis to detect paraphrased conflicts
- Build conflict detection engine that scores statement pairs for contradiction likelihood
- Flag conflicts with source references showing exactly where each conflicting statement appears
- Generate conflict reports with side-by-side comparison of contradictory statements

Expected Output:
- Conflicts Detected subsection in Ambiguities section
- Each conflict includes: ID, Type, Entity, Severity, Conflicting Statements, Source References, Recommendation
- Side-by-side comparison with context (2-3 sentences before/after)

**Test Coverage:** `__tests__/ambiguity_detection_conflicts.test`

---

**AC2: Missing business rules and undefined actors/data fields are identified**

Goal: Find gaps in business logic and data definitions that could lead to implementation confusion.

Details:
- Scan for business rule patterns including if-then conditions, validation rules, and decision logic
- Identify data fields mentioned in requirements without corresponding definitions, data types, or constraints
- Flag incomplete rule sets where conditions are mentioned but actions are undefined, or vice versa
- Check for missing validation rules on input fields
- Identify actors or roles mentioned without clear definitions
- Generate a gap report listing all undefined elements with context about where they are referenced

Expected Output:
- Missing Definitions and Incomplete Rules subsection in Ambiguities section
- Each gap includes: ID, Type, Element, Severity, Mentioned In, Reason, Recommendation
- Gap types: Incomplete Rule, Undefined Field, Missing Validation, Undefined Actor

**Test Coverage:** `__tests__/ambiguity_detection_gaps.test`

---

**AC3: Unclear process steps and missing acceptance tests are flagged**

Goal: Flag vague requirements and untestable functionality that needs clarification.

Details:
- Identify vague terms in requirements such as "should", "may", "might", "approximately", "user-friendly", "fast", "secure" without specific definitions
- Find process steps without clear actors (who performs the step), actions (what is done), or outcomes (what results)
- Check for functional requirements without corresponding acceptance criteria
- Validate that acceptance criteria are testable (measurable, specific, verifiable)
- Flag requirements that use subjective language without objective metrics

Expected Output:
- Vague and Unclear Requirements subsection (subjective terms, approximate values, conditional language)
- Missing Acceptance Criteria subsection (requirements without testable criteria)
- Each vague requirement includes: ID, Type, Vague Term, Severity, Location, Issue, Recommendation
- Each missing criteria includes: ID, Requirement ID, Summary, Severity, Reason, Recommendation

**Test Coverage:** `__tests__/ambiguity_detection_vague.test`

---

**AC4: Dedicated "Ambiguities" section generated**

Goal: Create a formatted ambiguities section that matches template requirements.

Details:
- Ambiguities and Clarifications section in BRD (position determined by template)
- Format ambiguities as structured tables with columns: ID, Section Reference, Ambiguity Description, Reason, Severity (High/Medium/Low)
- Sort ambiguities by severity and section order
- Include summary count of ambiguities by category at the top of section
- Provide clarity score (0-100) calculated based on severity and count
- Include subsections: Summary, Conflicts, Missing Definitions, Vague Requirements, Missing Criteria, Clarity Score Breakdown, Recommended Actions

Expected Output:
- Ambiguities and Clarifications section with all subsections
- Summary table with counts and severity breakdown
- Clarity score with assessment (Ready for Design | Needs Clarification | Significant Gaps)
- If no ambiguities: "✓ No significant ambiguities detected. All requirements are clear, complete, and testable."

**Test Coverage:** `__tests__/ambiguity_detection_integrated.test`

---

**AC5: Each ambiguity includes section name, description, and reason for ambiguity**

Goal: Link each ambiguity back to its source location with sufficient context for easy resolution.

Details:
- Store complete section names, page numbers (if available), and document names for each ambiguity
- Provide surrounding context by including 2-3 sentences before and after the ambiguous content
- Create hyperlinks from ambiguity entries back to the relevant BRD sections
- Include source document references for conflicts (e.g., "Epic BRD Section 3.2 vs. Capability BRD Section 4.1")
- Format context in a readable way with clear attribution

Expected Output:
- Each ambiguity entry includes: Source document, Section name/number, Full context quote, Surrounding text, Hyperlink to section, Clear reason/issue description, Specific actionable recommendation

DocumentReference Structure:
```typescript
interface DocumentReference {
  fileName: string
  documentPath: string
  sectionName: string
  pageNumber?: number
  hyperlinkAnchor: string
}
```

**Test Coverage:** `__tests__/ambiguity_detection_integrated.test`

---

#### Implementation Details

**Agent:** `agents/ambiguity-detector.md` (~400 lines)
- Read-only analyzer with 5 detection algorithms
- Pattern matching via Grep + semantic analysis via Claude
- Severity scoring: High (10 pts), Medium (5 pts), Low (2 pts)
- Clarity score calculation per section and overall

**Integration:** `agents/brd-generator.md` Phase 1.5
- Invoked after content extraction, before generation
- Feature flag: `SKIP_AMBIGUITY_DETECTION: true` to bypass
- Graceful degradation if detection fails

**Template:** `skills/brd-template/SKILL.md` v1.5
- Ambiguities and Clarifications section (7 subsections)
- Template-driven section structure with dynamic parsing
- Default template includes ambiguities section

**Validation:** `skills/ba-validate/`
- Intent: `VALIDATE_AMBIGUITIES`
- Routes to ambiguity-detector for standalone analysis
- Supports natural language: "validate ambiguities", "check for conflicts"

**Test Files:**
- `__tests__/ambiguity_detection_conflicts.test` (conflict detection)
- `__tests__/ambiguity_detection_gaps.test` (gap detection)
- `__tests__/ambiguity_detection_vague.test` (vagueness detection)
- `__tests__/ambiguity_detection_integrated.test` (end-to-end BRD generation)

## Nice-to-Haves

### 6. Success Metrics Definition
Given business objectives in source documents, when the plugin extracts information, then it should define measurable success metrics and KPIs in structured format.

**Test Coverage:**
- `__tests__/success_metrics_definition.test`

### 7. Compliance Requirements Analysis
Given regulatory or compliance mentions in source documents, when the plugin processes requirements, then it should extract and structure compliance requirements with specific regulations and obligations.

**Test Coverage:**
- `__tests__/compliance_requirements.test`

### 8. Exploration Mode
Given existing business artifacts, when I request exploration using natural language (e.g., "Analyze the EPIC and explain the capabilities"), then the plugin should analyze and explain the artifacts.

**Test Coverage:**
- `__tests__/exploration_mode.test`

### 9. Validation Mode
Given business artifacts and source requirements, when I request validation using natural language (e.g., "Validate the BRD against the source requirements"), then the plugin should check coverage and completeness.

**Test Coverage:**
- `__tests__/validation_mode.test`

## Additional Test Coverage

The following tests cover important aspects of the Business Analyst plugin:

- **Business Hypothesis:** `__tests__/business_hypothesis.test`
- **Document Ingestion:** `__tests__/document_ingestion.test`
- **Naming Convention:** `__tests__/naming_convention.test`
- **Organizational Context:** `__tests__/organizational_context.test`
- **Problem Statement Extraction:** `__tests__/problem_statement_extraction.test`
- **SAFe Methodology:** `__tests__/safe_methodology.test`
- **Intent Detection:** `__tests__/intent_detection.test`
- **Router Coordination:** `__tests__/router_coordination.test`

## Version History

### Version 1.5.0 (Current - US793123, US793127)
- **BREAKING**: Deprecated commands in favor of skill-based auto-invocation
- **NEW**: Template-driven architecture with dynamic section parsing
- **NEW**: Natural language prompt support for all operations
- Ambiguities and Clarifications section integrated with template
- Default template at `skills/brd-template/template.md`
- Template version updated to 1.5 (dynamic structure)
- Removed hardcoded section assumptions
- Skills automatically invoked from natural language

### Version 1.4.0
- Ambiguity detection and reporting (US789392)
- Added Ambiguities and Clarifications section to BRD template
- New ambiguity-detector agent with 5 detection algorithms
- Enhanced validation with VALIDATE_AMBIGUITIES intent
- Comprehensive test coverage for ambiguity detection

### Version 1.4.0
- Version bump for unified release
- All plugin functionality maintained from 1.3.0

### Version 1.3.0
- Automated BRD generation capability
- Supports multiple document formats (MD, PDF, DOCX, TXT, MSG)
- Dual format output (markdown + Word)
- Graceful degradation for missing information
- Direct generation without plan-confirm workflow

### Version 1.2.0
- Initial release with artifact generation capabilities
- EPIC, business rules, and persona extraction
- Plan-confirm workflow for all generation tasks
- SAFe methodology alignment
