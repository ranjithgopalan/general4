---
name: ba-generate
description: Generate business artifacts including EPICs, business rules, personas, and BRDs from requirements documents. Routes to specialized generators based on artifact type. Use when creating new business documentation from source requirements.
license: Proprietary
compatibility: Requires business-analyst agent and sub-agents (epic-generator, business-rules-generator, brd-generator). Requires documents skill for DOCX/PDF reading.
metadata:
  author: ADLC Business Analyst Team
  version: "1.0.0"
  organization: AIG
  plugin: AIDLC-business-analyst
allowed-tools: Task Read Glob Grep Skill AskUserQuestion
---

# Business Analyst Generate Skill

Generate business artifacts (EPICs, business rules, personas, BRDs) from requirements documents. This skill acts as a router to specialized generators, handling context ADLCing and delegation.

---

## When to Use This Skill

Use this skill when:
- Generating EPIC definitions from requirements
- Extracting and formalizing business rules from documents
- Creating user personas from stakeholder information
- Generating comprehensive BRDs from multiple source documents
- Creating success metrics and KPIs from business objectives
- Need to generate multiple artifact types together

**Do NOT use this skill for:**
- Exploring existing artifacts (use ba-explore skill instead)
- Validating artifacts (use ba-validate skill instead)
- Direct BRD generation with specific parameters (use ba-brd skill instead)
- Simple document conversion (use documents skill instead)

---

## How This Skill Works

### Step 1: Analyze User Intent

The skill analyzes the request to determine:
- **Artifact Type**: EPIC | Business Rules | Personas | BRD | Success Metrics | All
- **Source Documents**: Paths to requirements, specifications, research
- **Constraints**: Output format, structure, specific sections
- **Preferences**: Naming conventions, templates, style

### Step 2: ADLC Required Context

The skill collects necessary information:
- Locate source documents (requirements, specifications, etc.)
- Check for existing business artifacts if enhancing
- Identify stakeholder information
- Read source content using appropriate tools (Read for MD/TXT, documents skill for DOCX/PDF)

### Step 3: Output Path Discovery (3-Tier Strategy)

The skill determines where to save outputs:
- **Tier 1**: Check input documents for outputPath metadata
- **Tier 2**: Check for existing output patterns (output/, docs/, deliverables/)
- **Tier 3**: Prompt user only if Tier 1 & 2 fail

### Step 4: Present Plan Before Execution

The skill creates a plan and seeks user confirmation:
- Summarize what was understood from the request
- List what will be generated and where
- Identify which specialized agent(s) will be used
- Wait for user confirmation before proceeding

### Step 5: Delegate to Appropriate Generator

Based on artifact type, delegates to:
- **epic-generator**: EPIC definitions with business context and SAFe alignment
- **business-rules-generator**: Business rules with formulas, validation logic, and error handling
- **brd-generator**: Comprehensive Business Requirements Documents with all 16 sections
- **persona-generator**: User personas with demographics, goals, pain points, and journeys

### Step 6: Include Full Context in Delegation

When delegating, includes:
- Original user request
- Source document paths and content
- Output path and naming conventions
- Any special instructions or constraints

---

## Supported Artifact Types

### EPIC Definitions

**Generated Content:**
- EPIC name and ID
- Business context and objectives
- Scope (in-scope/out-of-scope)
- Success criteria and metrics
- Features breakdown
- Dependencies and assumptions
- SAFe portfolio alignment

**Routing:** business-analyst:epic-generator

### Business Rules

**Generated Content:**
- Rule ID and name
- Condition and action
- Formula or calculation logic
- Validation rules
- Error handling
- Examples and edge cases
- Traceability to requirements

**Routing:** business-analyst:business-rules-generator

### Personas

**Generated Content:**
- Persona name and archetype
- Demographics
- Goals and objectives
- Pain points and frustrations
- User journey mapping
- Technology proficiency
- Behavior patterns

**Routing:** business-analyst:persona-generator (via business-analyst router)

### Business Requirements Document (BRD)

**Generated Content:**
- All 16 standard sections (Executive Summary through Appendices)
- Optional: Requirement Traceability Matrix (Section 15)
- Optional: Source Documents Reference (Section 16)
- Both .md and .docx formats

**Routing:** business-analyst:brd-generator

**Note:** For direct BRD generation without confirmation workflow, use ba-brd skill instead.

---

## Agent Invocation

This skill uses the Task tool with `subagent_type="business-analyst"` as a router.

### Enriched Prompt Template

```
## User Request
{user_request}

## Intent: GENERATE (Create Business Artifacts)

### 1. Analyze User Intent
- Identify artifact type: EPIC | Business Rules | Personas | BRD | All
- Extract document paths mentioned in request
- Identify any constraints or preferences
- Note any specific sections or focus areas

### 2. ADLC Required Context
- Locate source documents (requirements, specifications, etc.)
- Use Glob to discover files at specified paths
- Use documents skill for DOCX/PDF extraction
- Use Read for MD/TXT files
- Check for existing business artifacts if enhancing
- Identify stakeholder information

### 3. Output Path Discovery
- Tier 1: Check input documents for outputPath metadata
- Tier 2: Check for existing output patterns (output/, docs/)
- Tier 3: Prompt user only if Tier 1 & 2 fail

### 4. Present Plan Before Execution
- Summarize what you understood from the request
- List source documents found
- Show what you will generate
- Specify output location
- Identify which agent(s) to delegate to
- Wait for user confirmation with AskUserQuestion

### 5. Delegate to Appropriate Agent
Based on confirmed plan:
- epic-generator → EPIC definitions with business context
- business-rules-generator → Business rules with formulas and validation
- brd-generator → Comprehensive Business Requirements Document
- Multiple agents if generating multiple artifact types

### 6. Include Full Context in Delegation
Pass to specialized agent:
- Original user request
- Source documents (paths and content)
- Output path and naming conventions
- Confirmed plan details
- Any special instructions
```

---

## Usage Examples

### Example 1: Generate EPIC from Requirements

**Input:**
```
Generate an EPIC from the requirements in ./docs/authentication-requirements.md
```

**Process:**
1. Read authentication-requirements.md
2. Identify EPIC scope and features
3. Detect output path (Tier 2: docs/)
4. Present plan: "Generate EPIC definition for Customer Authentication, save to docs/EPIC-E12345.md"
5. User confirms
6. Delegate to epic-generator with context
7. epic-generator creates EPIC definition

**Output:**
```
docs/EPIC-E12345.md created with:
- EPIC Name: Customer Authentication System
- Features: OAuth2, Session Management, Password Reset
- Success Criteria: Defined
- Dependencies: Listed
```

### Example 2: Generate BRD from Multiple Documents

**Input:**
```
Generate a BRD from all documents in ./requirements/ folder
```

**Process:**
1. Use Glob to find all files in ./requirements/
2. Read supported formats (MD, TXT)
3. Extract DOCX/PDF via documents skill
4. Detect output path (Tier 2: requirements/ → output/)
5. Present plan: "Generate BRD with 16 sections, save to output/BRD.md and output/BRD.docx"
6. User confirms
7. Delegate to brd-generator with all content
8. brd-generator creates BRD with both formats

**Output:**
```
output/BRD.md and output/BRD.docx created with:
- All 16 sections populated
- Content from 8 source documents
- Coverage summary: 85%
- Source document references in Appendices
```

### Example 3: Generate Multiple Artifacts

**Input:**
```
Generate EPICs, business rules, and personas from ./business-case/
```

**Process:**
1. Read all documents in ./business-case/
2. Extract content
3. Detect output path (Tier 2: business-case/ → output/)
4. Present plan: "Generate 3 artifact types, save to output/"
5. User confirms
6. Delegate to epic-generator for EPICs
7. Delegate to business-rules-generator for rules
8. Delegate to business-analyst for personas (routes internally)

**Output:**
```
output/
├── EPICs/
│   ├── EPIC-E12345-Authentication.md
│   └── EPIC-E12346-UserProfile.md
├── BusinessRules/
│   └── business-rules.md (15 rules)
└── Personas/
    ├── persona-busy-professional.md
    └── persona-first-time-buyer.md
```

---

## Output Path Discovery Logic

### Tier 1: Document Metadata

Check source documents for outputPath in frontmatter:
```yaml
---
title: Requirements Document
outputPath: ./deliverables/
---
```

### Tier 2: Pattern Detection

Look for existing output patterns:
- `output/` directory
- `docs/` directory
- `deliverables/` directory
- `generated/` directory

### Tier 3: User Prompt

If Tier 1 & 2 fail, ask user:
```
Where would you like to save the generated artifacts?
Options:
1. ./output/ (create new directory)
2. ./docs/ (use existing docs directory)
3. Custom path (you specify)
```

---

## Plan Confirmation Workflow

Before generating, present plan for approval:

```
## Generation Plan

**Source Documents:**
- ./requirements/functional-spec.md
- ./requirements/business-case.docx
- ./requirements/user-research.pdf

**Artifacts to Generate:**
1. EPIC Definition (EPIC-E12345-Authentication.md)
2. Business Rules (business-rules.md)
3. User Personas (3 personas)

**Output Location:**
- ./output/

**Agents:**
- epic-generator (for EPIC)
- business-rules-generator (for rules)
- business-analyst → persona-generator (for personas)

Proceed with generation? (yes/no)
```

---

## Error Handling

### Source Documents Not Found

If specified paths don't exist:
- List what was searched
- Suggest alternative locations
- Ask user to verify paths
- Don't proceed without valid sources

### Output Path Conflicts

If output files already exist:
- Warn about overwrite
- Offer to append timestamp
- Ask for user confirmation
- Suggest alternative names

### Insufficient Content

If source documents lack needed information:
- Proceed with available content
- Mark missing sections clearly
- Include coverage summary
- Suggest additional sources

### User Cancels Plan

If user rejects the plan:
- Ask what needs to change
- Revise plan based on feedback
- Present updated plan
- Don't proceed until approved

---

## Performance Considerations

- Processes documents in parallel when possible
- Caches extracted content to avoid re-reading
- Delegates to specialized agents for efficient generation
- Uses progressive disclosure (plan before heavy work)

---

## Skill Requirements

### Prerequisites
1. **Business Analyst Agent**: Main router agent
2. **Specialized Generators**: epic-generator, business-rules-generator, brd-generator
3. **Documents Skill**: For DOCX/PDF reading
4. **File Access**: Read permissions for source documents, write for output

### Source Document Formats
- Markdown (.md)
- Text (.txt)
- Word (.docx) - via documents skill
- PDF (.pdf) - via documents skill
- Outlook MSG (.msg) - via documents skill

---

## Integration with Other Skills

This skill orchestrates:
- **documents**: For reading DOCX/PDF/MSG files
- **brd-template**: For BRD structure
- **rally-hierarchy**: For EPIC traceability (optional)

This skill routes to:
- **epic-generator**: For EPIC creation
- **business-rules-generator**: For rules extraction
- **brd-generator**: For BRD generation

---

## Differences from ba-brd Skill

| Feature | ba-generate | ba-brd |
|---------|-------------|--------|
| **Confirmation** | Requires user approval | Direct execution |
| **Scope** | All artifact types | BRD only |
| **Routing** | Router to multiple agents | Direct to brd-generator |
| **Speed** | Slower (plan + confirm) | Faster (direct) |
| **Use Case** | Exploratory, multiple artifacts | Known BRD generation |

**Recommendation:**
- Use **ba-generate** when exploring requirements or generating multiple artifact types
- Use **ba-brd** when you specifically need a BRD with known parameters

---

## Version and Compatibility

**Version:** 1.0.0
**Compatible with:**
- Business Analyst Agent v0.9.0+
- EPIC Generator v0.9.0+
- Business Rules Generator v0.9.0+
- BRD Generator v1.6.0+
- Documents Skill v1.0.0+
- Rally Hierarchy Skill v1.0.0+ (optional)

**Last Updated:** 2026-02-05

---

## Reporting to the AIDLC Platform (ECS UI tracking)

When invoked for the EPIC-SET stage (G3 of `uw-cr-global.yaml`), execute these
Bash commands to register the run and upload the deliverable. Requires
`aidlc.config.json` in the project root. The EPIC-SET stage produces `epic.md`
(the epic delegate must write output to a file named exactly `epic.md`).

**Step 0 — Read config**
```bash
CONFIG=$(cat aidlc.config.json 2>/dev/null || echo '{}')
BASE=$(echo "$CONFIG" | python3 -c "import sys,json; print(json.load(sys.stdin).get('central_base_url','http://localhost:8080'))")
TOKEN=$(python3 -c "import json,os; d=json.load(open('aidlc.config.json')); print(os.environ.get(d.get('auth',{}).get('bearer_env','AIDLC_TOKEN'),''))")
WORKSPACE=$(echo "$CONFIG" | python3 -c "import sys,json; print(json.load(sys.stdin).get('workspace_id',''))")
```

**Step 1 — Register run (`execute=false` — ECS records it but does not run it)**
```bash
RESP=$(curl -s -X POST "$BASE/agentic_platform/api/v1/pipeline-runs" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d "{\"stage\":\"epic-set\",\"workspace_id\":\"$WORKSPACE\",\"execute\":false}")
RUN_ID=$(echo "$RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['run']['run_id'])")
echo "Registered run: $RUN_ID"
```

**Step 2 — Mark as running (ECS UI shows stage as in-progress)**
```bash
curl -s -X PATCH "$BASE/agentic_platform/api/v1/pipeline-runs/$RUN_ID/status" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"state":"running"}'
```

→ **Now execute the skill workflow above.** Instruct the epic-generator to write its
output to `epic.md` (lowercase). When `epic.md` exists, continue below.

**Step 3 — Upload deliverable (ECS validates and moves stage to `waiting_for_approval`)**
```bash
curl -s -X POST "$BASE/agentic_platform/api/v1/pipeline-runs/$RUN_ID/deliverables" \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@epic.md"
```

**Step 4 — Review and approve in ECS UI**
`https://poc-ecs-build-ui.npsdlchgapp.us-east-1.aws.aig.net/aidlc-platform-management/pipeline`
