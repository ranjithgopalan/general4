---
name: ba-brd
description: Generate ultra-concise 12-15 page BRD from document folder with dual-mode generation (interview or direct). Enforces strict page budgets, eliminates verbose prose, limits rules to 10 max. Template selection handled by brd-template skill, mandatory ambiguity detection, and template-conditional traceability. Supports --epic flag for Rally hierarchy integration. Outputs both brd.md and brd.docx with intelligent content summarization.
license: Proprietary
compatibility: Requires documents skill and brd-template skill. Optional Rally integration via rally-hierarchy skill.
metadata:
  author: ADLC Business Analyst Team
  version: "1.18.0"
  organization: AIG
  plugin: AIDLC-business-analyst
allowed-tools: Read Write Task AskUserQuestion TodoWrite
---

# Business Analyst BRD Skill

Direct BRD generation from document folders with CLI-style parameters and interactive section-by-section review.

---

## When to Use This Skill

Use this skill when:
- Generating a BRD from a known document folder
- Want flexible generation modes: interview-style review OR quick direct generation
- Want to include Rally Epic traceability (--epic flag)
- Need both markdown and DOCX output formats
- Want mandatory ambiguity detection in output

**Key Features:**
- **Direct User Interaction**: No agent relay - you interact directly with the main assistant
- **Dual Generation Modes**:
  - **Interview Mode**: Review each section with user before proceeding (recommended for quality control)
  - **Direct Mode**: Auto-generate entire BRD without user interaction (faster for trusted sources)
- **Ultra-Concise Output**: 12-15 pages (6,000-9,000 words) with strict page budgets per section, eliminates verbose prose
- **Mandatory**: Ambiguity Identification section (always included)
- **Template-Conditional**: Traceability sections (only if template includes them)
- **Aggressive Limits**: Max 10 business rules, 3 bullets per acceptance criteria, 1 paragraph per persona

**Do NOT use this skill for:**
- Exploratory requirements analysis (use ba-generate skill instead)
- Generating multiple artifact types (use ba-generate skill instead)
- Validating existing BRDs (use ba-validate skill instead)

---

## Command Syntax

```bash
ba-brd <path> [--epic <Epic ID>]
```

### Parameters

| Parameter | Required | Description | Example |
|-----------|----------|-------------|---------|
| `<path>` | Yes | Path to document folder | `./requirements/` |
| `--epic <Epic ID>` | No | Rally Epic ID for traceability (if template includes traceability tables) | `--epic E12345` |

---

## Communication is Essential

**CRITICAL: The assistant must actively communicate progress to the user throughout the entire workflow.**

Why communication matters:
- BRD generation is a multi-phase process that takes time
- Users need to understand what's happening at each step
- Transparency builds trust and allows users to follow along
- Clear progress updates help users know when to provide input (interview mode) or track progress (direct mode)

When to communicate:
- ✓ At the start of each major phase (document processing, template loading, mode selection, etc.)
- ✓ When invoking sub-agents or skills
- ✓ After completing operations (with summary statistics)
- ✓ **Interview Mode**: Before and after each section review
- ✓ **Direct Mode**: As each section is generated (e.g., "✓ Section 3 (Business Requirements) generated")
- ✓ When encountering issues or making decisions
- ✓ When writing files or performing long operations

How to communicate:
- Use clear, concise messages that explain what's happening
- Include relevant metrics (document counts, section numbers, progress indicators)
- Use checkmarks (✓) to indicate completion
- Provide context for what's happening next
- In direct mode, keep users informed of generation progress even without their input

**Never work silently. Always keep the user informed.**

---

## Workflow Overview

The skill handles the **entire BRD workflow end-to-end**:

1. **Template Loading**: Load BRD template structure (brd-template skill handles template selection)
2. **Document Processing**: Process source documents and extract content
3. **Output Path Discovery**: Determine where to save BRD files (3-tier approach)
4. **Generation Mode Selection**: Ask user to choose between interview mode (section-by-section review) or direct mode (auto-generate)
5. **BRD Content Generation**: Generate sections based on selected mode
   - **Interview Mode**: Section-by-section review with user approval (approve/modify/defer as gap)
   - **Direct Mode**: Auto-generate all sections without user interaction
6. **Ambiguity Detection**: Detect and report ambiguities (mandatory)
7. **Traceability Collection**: Collect Rally hierarchy and source citations (template-conditional)
8. **Final Output**: Generate brd.md and brd.docx
9. **Optional Resolution**: Offer to resolve gaps/conflicts detected in Section 12 (after BRD generation)

**IMPORTANT: Communicate progress to the user as you work. Tell them what you're doing at each step.**

---

## Detailed Workflow Instructions

### Phase 0: Template Loading

Load and parse BRD template before document processing:

**Instructions:**

1. Tell the user: "Loading BRD template structure..."

2. Invoke the brd-template skill:
   - Use: `Skill({skill: "AIDLC-business-analyst:brd-template"})`
   - The brd-template skill will handle all template selection (custom path, SAFE BRD, or legacy Inscore)
   - It will prompt the user if no template was specified

3. The brd-template skill will:
   - Ask user to select template (custom path, SAFE BRD, or legacy Inscore) if not already specified
   - Load the template file (supports MD, DOCX, PDF, TXT)
   - Convert to markdown if needed (DOCX → MD)
   - Detect if it's an example document or proper template
   - Convert examples to templates with placeholders if needed
   - Parse template structure (sections, subsections, placeholders)
   - Return a TemplateStructure object

4. Store the template structure received, which contains:
   - **sections**: Array of section objects with number, name, level, placeholders
   - **totalSections**: Total count of sections
   - **placeholders**: List of placeholders like {project name}, {date}, {author}
   - **idFormat**: ID pattern like "PRE_XXX" or "INV_XXX"
   - **templatePath**: Path or "default"

5. Tell the user: "✓ Template loaded with [X] sections"

6. **→ IMMEDIATELY proceed to Phase 1 (Document Processing). Do NOT stop or wait for user input.**

### Phase 1: Document Processing

Delegate document processing to the document-processor agent:

**Instructions:**

1. Tell the user: "Starting document processing phase..."
2. Tell the user: "Processing documents from: [path]"

3. **Invoke document-processor agent:**
   ```
   Task({
     subagent_type: "AIDLC-business-analyst:document-processor",
     description: "Process and extract structured content",
     prompt: "Process all documents in folder: [path]

Exclude template files (already processed in Phase 0).

For each document:
1. Convert DOCX/PDF/MSG to markdown (in memory)
2. Extract paragraph-level content
3. Map each paragraph to appropriate BRD section
4. Generate keyPoints, condensed summaries, confidence scores
5. Save JSON mapping file with timestamp

Return summary with: mappingFile path, templateFiles, totalDocuments, totalParagraphs, sectionDistribution"
   })
   ```

4. Store the returned summary object with:
   - `mappingFile`: Path to JSON mapping file
   - `totalParagraphs`: Count of extracted paragraphs
   - `sectionDistribution`: Paragraph count by BRD section

5. Tell the user: "✓ Document processing complete!"
6. Tell the user: "  - Processed [X] documents"
7. Tell the user: "  - Extracted [Y] paragraphs with section mappings"
8. Tell the user: "  - Mapping file: [mappingFile]"

8. **→ IMMEDIATELY proceed to Phase 2 (Output Path Discovery). Do NOT stop or wait for user input.**

### Phase 2: Output Path Discovery

Use 3-tier approach to find where to save BRD files:

**Tier 1: Check for Path Metadata in Documents**
1. Search the processed documents for output path metadata
2. Look for markers like "output:", "save to:", or path specifications
3. If found, use that path and skip to final step

**Tier 2: Check Existing Patterns**
1. Use Glob to search for existing BRD/documentation patterns:
   - `Glob({pattern: "output/**/*.md"})`
   - `Glob({pattern: "docs/**/*.md"})`
   - `Glob({pattern: "requirements/**/*.md"})`
2. If patterns found, extract the most common directory
3. Use that directory and skip to final step

**Tier 3: Ask User**
If Tiers 1 and 2 fail, use AskUserQuestion tool:
```
AskUserQuestion({
  questions: [{
    question: "Where would you like me to save the BRD files (brd.md and brd.docx)?",
    header: "Output Path",
    multiSelect: false,
    options: [
      {label: "[inputPath]", description: "Same location as input documents"},
      {label: "./output/", description: "Standard output directory"},
      {label: "./docs/business/", description: "Business documentation folder"}
    ]
  }]
})
```

**Final Step:**
- Store the determined output path
- Tell the user: "Will save BRD files to: [path]"
- **→ IMMEDIATELY proceed to Phase 2.5 (Generation Mode Selection). Do NOT stop or wait for user input.**

### Phase 2.5: Generation Mode Selection

Determine whether to use interactive interview mode or direct generation:

**Instructions:**

1. Tell the user: "BRD generation can proceed in two ways: interview mode (section-by-section review) or direct mode (auto-generate without review)."

2. **Ask user for generation mode using AskUserQuestion:**
   ```
   AskUserQuestion({
     questions: [{
       question: "How would you like to generate the BRD?",
       header: "Generation",
       multiSelect: false,
       options: [
         {
           label: "Interview mode - Review each section (Recommended)",
           description: "I'll show you each section for approval before proceeding. You can modify any section during the process."
         },
         {
           label: "Direct mode - Auto-generate entire BRD",
           description: "I'll generate all sections automatically without stopping for review. Faster but no section-by-section control."
         }
       ]
     }]
   })
   ```

3. **Store the user's choice:**
   - If "Interview mode": Set `generationMode = "interview"`
   - If "Direct mode": Set `generationMode = "direct"`

4. Tell the user: "Using [mode] for BRD generation."

5. **→ IMMEDIATELY proceed to Phase 3 (BRD Content Generation). Do NOT stop or wait for user input.**

### Phase 3: BRD Content Generation

**CRITICAL: This phase handles both interview and direct generation modes. Follow the appropriate workflow based on `generationMode` from Phase 2.5.**

#### Branching Logic

**If `generationMode = "interview"`:**
- Follow Steps 1-4 below (Interactive Interview Workflow)
- Use AskUserQuestion for every section review

**If `generationMode = "direct"`:**
- Skip to "Direct Generation Workflow" (see below)
- Auto-approve all sections without user interaction

---

### Interactive Interview Workflow (Interview Mode)

#### Step 1: Initialize Interview Tracking

1. Load the template structure (from Phase 0)
2. Tell the user: "I'll now generate the BRD section by section. There are [X] sections to review."
3. Tell the user: "For each section, I'll show you a draft and ask for your confirmation before moving to the next one."

4. **Create todo list using TodoWrite:**
   ```
   TodoWrite({
     todos: [
       {content: "Review and confirm Section 1", activeForm: "Reviewing Section 1", status: "pending"},
       {content: "Review and confirm Section 2", activeForm: "Reviewing Section 2", status: "pending"},
       ... (create one todo for EACH section in template)
     ]
   })
   ```

5. Create an empty array named `confirmedSections` to store confirmed section drafts

#### Step 2: Section Interview Loop

**For each section number from 1 to [total sections], execute this loop:**

##### 2A. Mark Section as In Progress
- Update the todo for this section to 'in_progress' status
- Tell the user: "\n--- Section [X] of [Total]: [Section Name] ---"

##### 2B. Generate Section Draft

**CRITICAL: Check page budget BEFORE generating content**

**Pre-Generation Enforcement Checklist:**
1. **Consult Page Budget table** in Content Conciseness Principles for this section
2. **Apply Compression Rules FIRST**:
   - ❌ NO detailed subsections (if in summary table, stop there)
   - ❌ NO prose except Executive Summary (3 sentences max)
   - Tables: 4 columns MAX, single-line cells only
   - Bullets: Single line only, no explanations
   - Abbreviate: usr, mgr, req, sys, calc, admin, auth, perf, impl
3. **Format Selection**: tables > bullets > minimal prose
4. **Hard Limits for Common Sections**:
   - Business Rules: 10 rules MAX (single table, no "Detailed Rules" subsection)
   - Functional Requirements: Summary table ONLY (ID | Summary | AC Count | Priority | SP | Sprint) - NO detailed acceptance criteria text
   - NFRs: Single table ONLY - NO subsection explanations
   - User Personas: 1 paragraph each MAX (3-4 sentences)
   - Ambiguities: Single summary table ONLY - NO detailed subsections by type
5. **Reject Before Generating**: If section type typically has "detailed subsections", eliminate them before writing content

1. Tell the user: "Generating draft content from source documents..."

2. **Read JSON mapping file:**
   ```
   Read({file_path: mappingFile})  // From Phase 1
   Parse JSON to get paragraph_mappings array
   ```

3. **Extract relevant content for current section:**
   - Get section name and number from template (e.g., "5. Functional Requirements")
   - Filter paragraph_mappings where `section_in_brd` matches current section
   - Collect all matching paragraphs with their metadata:
     - content (full paragraph text)
     - keyPoints (extracted facts)
     - condensed (summary)
     - document_name (source traceability)
     - confidence (quality score)
     - requires_review (flagged content)

4. **Condense and format extracted content:**
   - Use the pre-extracted keyPoints and condensed summaries from mapping
   - Identify section type and apply appropriate formatting:
     - Requirements/Rules → Table format (ID | Description | Priority)
       - Use keyPoints as table rows
       - Generate IDs if not present in content
     - Objectives/Scope → Bullet list format
       - Use condensed summaries as bullets
     - Summary/Introduction → Prose paragraph format (concise)
       - Combine condensed summaries into coherent paragraph
     - Business Rules → Table format with formulas/conditions
       - Extract from content field where formulas present
   - Apply Content Conciseness Principles to reduce verbosity
   - Remove redundant explanations, keep essential information only
   - Prioritize content with high confidence scores (>0.8)

5. Replace template variables with extracted content (e.g., {project name}, {date})

6. Track source documents used:
   - Collect unique document_name values from matched paragraphs
   - Note paragraphs flagged with requires_review for gap identification

7. **Generate draft preview for user review:**
   - **For sections ≤ 500 characters:** Show the complete content with formatting
   - **For sections > 500 characters:**
     - Show first 500 characters of the draft content
     - Add a concise summary of the remaining content:
       - For tables/lists: Count remaining items (e.g., "...plus 12 more requirements covering X, Y, Z")
       - For prose: Summarize remaining paragraphs (e.g., "...continues with details on implementation approach and success criteria")
     - Format: `[First 500 chars]\n\n**[Summary of remaining content]**`

8. Identify gaps:
   - Missing required subsections
   - Insufficient content found in source documents (no matching paragraphs)
   - Paragraphs flagged with requires_review: true
   - Low confidence scores (<0.7) in matched paragraphs

9. Calculate section confidence score:
   - Average confidence scores from matched paragraphs
   - Penalize for missing required subsections
   - Penalize for paragraphs flagged for review
   - Formula:
     - Start with average confidence from paragraphs
     - Reduce by 10% for each missing required subsection
     - Reduce by 5% for each requires_review flag
     - 0% if no relevant content found

10. Create draft object with:
   ```
   {
     sectionNumber: [number],
     sectionName: [name from template],
     content: [condensed content] or "[Not available in source documents]",
     summary: [concise summary as per step 6 guidelines],
     sources: [unique list of source document names that contributed to this section],
     gaps: [array of gap descriptions],
     confidence: [calculated score 0.0-1.0]
   }
   ```

##### 2C. Present Draft to User

**Build the confirmation question with draft preview:**
```
"Here's the draft for Section [number]: [name]:

---
[IF content length ≤ 500 chars: show complete content]
[IF content length > 500 chars: show first 500 chars + summary of rest]

Example for long sections:
"## Business Requirements
| ID | Requirement | Priority |
|-----|-------------|----------|
| BR-001 | System shall support policy creation workflow | High |
| BR-002 | System shall validate all input fields | High |
| BR-003 | System shall generate audit logs | Medium |
[...500 characters reached]

**Plus 12 more requirements covering approval workflows, search functionality, and reporting capabilities**"
---

**Sources:** [comma-separated list of sources]
[If gaps: "**Gaps:** [comma-separated list]"]
**Confidence:** [X]%

Does this look correct?"
```

**Use AskUserQuestion with this exact format:**
```
AskUserQuestion({
  questions: [{
    question: [question text from above],
    header: [truncate section name to 12 chars max],
    multiSelect: false,
    options: [
      {
        label: "Approve",
        description: "Section is complete and accurate"
      },
      {
        label: "I want to modify something / add additional context (use 'Type something' field)",
        description: "Provide changes or additions in the text input field below"
      },
      {
        label: "Note this as a gap and address later",
        description: "Section cannot be completed due to lack of clarity. Mark as gap and move to next section."
      }
    ]
  }]
})
```

##### 2D. Handle User Response

**Create a while loop: `while (!confirmed)`**

- **If user selected "Approve":**
  1. Add draft to `confirmedSections` array
  2. Set `confirmed = true` to exit the loop
  3. Update todo status to 'completed'
  4. Tell the user: "✓ Section [name] confirmed. Moving to next section..."
  5. Continue to next section in outer loop

- **If user selected "I want to modify something / add additional context":**
  1. Tell the user: "Processing your feedback..."

  2. **Read user's instructions from the response:**
     - The user will provide free-text input in the "Type something" field
     - Store this as `userInstructions`

  3. Tell the user: "Applying your changes to the section..."

  4. **Apply the user's instructions to revise the draft:**
     - Parse the instructions to understand what changes are needed
     - Modify the content based on instructions
     - **NEVER add information not present in source documents**
     - Only expand using content from the source documents
     - Preserve factual information from sources

  5. **Update the draft object:**
     - Replace `content` with revised content
     - Keep same `sectionNumber`, `sectionName`, `sources`, `gaps`

  6. Tell the user: "Revised draft ready for review."

  7. **Loop back to step 2C** - Present the revised draft to user again with AskUserQuestion

  8. Keep looping until user selects "Approve" or "Note this as a gap and address later"

- **If user selected "Note this as a gap and address later":**
  1. Tell the user: "Marking section as gap to address later..."

  2. **Create a deferred gap entry:**
     - Store the gap information for later resolution
     - Include: sectionNumber, sectionName, reason ("User marked as gap during interview"), confidence score

  3. **Add placeholder draft to confirmedSections:**
     - Mark content as "[Deferred - to be addressed during gap resolution]"
     - Include the incomplete draft content if any exists
     - Mark status as "deferred"

  4. Set `confirmed = true` to exit the loop
  5. Update todo status to 'completed'
  6. Tell the user: "✓ Section [name] marked as gap. Will address during gap resolution. Moving to next section..."
  7. Continue to next section in outer loop

---

### Direct Generation Workflow (Direct Mode)

**For users who selected "Direct mode" in Phase 2.5:**

#### Step 1: Initialize Direct Generation

1. Load the template structure (from Phase 0)
2. Tell the user: "Starting direct BRD generation with auto-approval for all [X] sections."
3. Tell the user: "I'll generate all sections automatically without stopping for review."

4. **Create todo list using TodoWrite:**
   ```
   TodoWrite({
     todos: [
       {content: "Generate Section 1", activeForm: "Generating Section 1", status: "pending"},
       {content: "Generate Section 2", activeForm: "Generating Section 2", status: "pending"},
       ... (create one todo for EACH section in template)
     ]
   })
   ```

5. Create an empty array named `confirmedSections` to store generated section drafts

#### Step 2: Auto-Generate All Sections Loop

**For each section number from 1 to [total sections], execute this loop:**

##### 2A. Mark Section as In Progress
- Update the todo for this section to 'in_progress' status
- Tell the user: "Generating Section [X] of [Total]: [Section Name]..."

##### 2B. Generate Section Content

**CRITICAL: Check page budget BEFORE generating content**

**Pre-Generation Enforcement Checklist:**
1. **Consult Page Budget table** in Content Conciseness Principles for this section
2. **Apply Compression Rules FIRST**:
   - ❌ NO detailed subsections (if in summary table, stop there)
   - ❌ NO prose except Executive Summary (3 sentences max)
   - Tables: 4 columns MAX, single-line cells only
   - Bullets: Single line only, no explanations
   - Abbreviate: usr, mgr, req, sys, calc, admin, auth, perf, impl
3. **Format Selection**: tables > bullets > minimal prose
4. **Hard Limits for Common Sections**:
   - Business Rules: 10 rules MAX (single table, no "Detailed Rules" subsection)
   - Functional Requirements: Summary table ONLY (ID | Summary | AC Count | Priority | SP | Sprint) - NO detailed acceptance criteria text
   - NFRs: Single table ONLY - NO subsection explanations
   - User Personas: 1 paragraph each MAX (3-4 sentences)
   - Ambiguities: Single summary table ONLY - NO detailed subsections by type
5. **Reject Before Generating**: If section type typically has "detailed subsections", eliminate them before writing content

1. **Read JSON mapping file:**
   ```
   Read({file_path: mappingFile})  // From Phase 1
   Parse JSON to get paragraph_mappings array
   ```

2. **Extract relevant content for current section:**
   - Get section name and number from template (e.g., "5. Functional Requirements")
   - Filter paragraph_mappings where `section_in_brd` matches current section
   - Collect all matching paragraphs with their metadata:
     - content (full paragraph text)
     - keyPoints (extracted facts)
     - condensed (summary)
     - document_name (source traceability)
     - confidence (quality score)

3. **Condense and format extracted content:**
   - Use the pre-extracted keyPoints and condensed summaries from mapping
   - Identify section type and apply appropriate formatting:
     - Requirements/Rules → Table format (ID | Description | Priority)
       - Use keyPoints as table rows
       - Generate IDs if not present in content
     - Objectives/Scope → Bullet list format
       - Use condensed summaries as bullets
     - Summary/Introduction → Prose paragraph format (concise)
       - Combine condensed summaries into coherent paragraph
     - Business Rules → Table format with formulas/conditions
       - Extract from content field where formulas present
   - Apply Content Conciseness Principles to reduce verbosity
   - Remove redundant explanations, keep essential information only
   - Prioritize content with high confidence scores (>0.8)

4. Replace template variables with extracted content (e.g., {project name}, {date})

5. Track source documents used:
   - Collect unique document_name values from matched paragraphs

6. Create section object with:
   ```
   {
     sectionNumber: [number],
     sectionName: [name from template],
     content: [condensed content] or "[Not available in source documents]",
     sources: [unique list of source document names that contributed to this section],
     paragraphCount: [number of paragraphs used from mapping]
   }
   ```

##### 2C. Auto-Approve Section

1. Add section to `confirmedSections` array
2. Update todo status to 'completed'
3. Tell the user: "✓ Section [X] ([name]) generated."
4. Continue to next section in loop

#### Step 3: Direct Generation Complete

**After ALL sections are generated (outer loop complete):**

1. Tell the user: "\n✓ All [X] sections generated automatically!"
2. Tell the user: "Proceeding to ambiguity detection..."
3. **→ Continue to Step 3: Ambiguity Detection below**

---

### Common Workflow (Both Modes)

#### Step 3: Ambiguity Detection (MANDATORY)

**After ALL sections are confirmed/generated (outer loop complete from either workflow):**

1. **Tell the user based on mode:**
   - **Interview Mode**: "\n✓ All sections reviewed! Now running ambiguity detection..."
   - **Direct Mode**: "\n✓ All sections complete! Now running ambiguity detection..."

2. **Invoke ambiguity-detector agent:**
   ```
   Task({
     subagent_type: "AIDLC-business-analyst:ambiguity-detector",
     description: "Detect ambiguities in confirmed BRD sections",
     prompt: "Analyze source documents and confirmed BRD content:

Source Documents: [list of document paths from Phase 1]

Confirmed BRD Sections:
[concatenate all confirmedSections with section headers]

Detect: conflicts, missing rules, undefined elements, vague language, missing criteria

Return JSON array with: section_name, short_description, why_ambiguous, severity, recommendation"
   })
   ```

3. Store ambiguity array from agent response

4. Tell the user: "Ambiguity detection complete. Found [X] ambiguities."

5. **→ Immediately proceed to Step 3a (Format Ambiguities for BRD)**

#### Step 3a: Format Ambiguities for BRD

**After ambiguity detection completes:**

1. **Format ambiguities into BRD Section 12 table:**

   Create the Ambiguities and Clarifications section:
   ```markdown
   ## 12. Ambiguities and Clarifications

   **Summary:** Found {total} ambiguities requiring review.

   | ID | Section | Issue | Severity | Recommendation |
   |-----|---------|-------|----------|----------------|
   | AMB-001 | Business Rules | Rule BR-005 lacks validation criteria | High | Define validation logic and thresholds |
   | AMB-002 | Requirements | Performance threshold undefined | Medium | Specify measurable performance criteria |
   | ... | ... | ... | ... | ... |

   **Clarity Score:** {calculate based on severity distribution}

   **Recommendation:** {If high priority items exist: "X high-priority issues require attention" | "All ambiguities are low priority"}
   ```

2. Store formatted section for final BRD assembly

3. If no ambiguities found:
   - Create Ambiguity Identification section with "No significant ambiguities detected" note

4. Tell the user: "✓ Ambiguities formatted for BRD Section 12"

5. **→ Immediately proceed to Step 4 (Traceability Collection)**

#### Step 4: Traceability Collection (Template-Conditional)

**CRITICAL: Only generate traceability if template includes traceability tables.**

1. Tell the user: "Checking template for traceability requirements..."

2. Check template structure for traceability sections:
   - Look for "Requirement Traceability Matrix" or similar
   - Look for "Source Documents Reference" or similar

3. If template has NO traceability sections:
   - Tell the user: "Template does not include traceability sections. Skipping traceability generation."
   - **→ IMMEDIATELY proceed to Phase 4 (Final BRD Assembly). Do NOT stop or wait for user input.**

4. If template HAS traceability sections, **invoke traceability-builder agent:**
   ```
   Task({
     subagent_type: "AIDLC-business-analyst:traceability-builder",
     description: "Build requirement traceability matrices",
     prompt: "Generate traceability sections:

Epic ID: [epicId or null if --epic not provided]

Requirements: [JSON array with requirement objects from confirmedSections]
- Extract requirements from Business Requirements and Rules sections
- Include: req_id, summary, sources

Source Documents: [JSON array with document metadata from Phase 1]
- Include: document_name, type, path, sections_referenced

Template has traceability: true

Generate:
1. Rally hierarchy table (Epic→Capability→Feature) if Epic ID provided
2. Requirement citations table (Req ID → Source Document → Section)
3. Source document reference section with metadata

Return formatted markdown sections ready for BRD inclusion."
   })
   ```

5. Store traceability markdown sections from agent response

6. Tell the user: "Traceability sections generated"

7. **→ IMMEDIATELY proceed to Phase 4 (Final BRD Assembly). Do NOT stop or wait for user input.**

### Phase 4: Final BRD Assembly

**After ambiguity detection and traceability collection:**

1. Tell the user: "\nAssembling final BRD document from all confirmed sections..."

2. **Build the complete BRD markdown:**
   - Start with metadata header (title, version, date, author)
   - Add table of contents with all section links
   - Add each section from `confirmedSections` array in order:
     - Format: `## [sectionNumber]. [sectionName]`
     - Include the full `content` (not just preview)
     - Add spacing between sections
   - **Add Ambiguity Identification section** (mandatory)
   - Add traceability sections (if template includes them)
   - Add appendix with source references:
     - List all unique source documents
     - Include document types, paths, coverage percentages
   - Add document coverage summary:
     - Total sections completed
     - Overall confidence score
     - Number of ambiguities found
     - Generation timestamp

3. **Length Validation and Compression (CRITICAL):**

   a. Count total words in assembled BRD:
      - Split content by whitespace
      - Count total words
      - Calculate estimated pages: `words / 500 = pages`

   b. If word count > 9,000 words (18 pages):
      - Tell user: "⚠️ BRD exceeds target length ([X] words, ~[Y] pages). Compressing..."
      - Apply emergency compression:
        1. Find Section 7 (Functional Requirements) - convert to summary table if not already
        2. Find Section 8 (NFRs) - remove all subsection prose, keep tables only
        3. Find Section 11 (Dependencies) - collapse to 2 tables max (External | Internal)
        4. Find Section 13 (Ambiguities) - single table only, remove subsection details
        5. Remove any remaining "Detailed" subsection headers and content
        6. Collapse any tables >4 columns by combining columns
      - Recount words after compression

   c. If still > 9,000 words after compression:
      - Tell user: "⚠️ After compression: [X] words (~[Y] pages). Still exceeds 15 page target."
      - Proceed anyway (best effort) but warn user

   d. If ≤ 9,000 words:
      - Tell user: "✓ BRD length validated: [X] words (~[Y] pages, target: 12-15 pages)"

4. Tell the user: "Writing brd.md to [output path]..."

5. **Write the markdown file:**
   ```
   Write({
     file_path: "[output_path]/brd.md",
     content: [complete BRD markdown]
   })
   ```

5a. **Write `prd.md` (pipeline deliverable — required by stage G1):**

    Extract from the confirmed sections: Executive Summary, Business Context, Business Outcomes,
    Scope Definition, Functional Requirements, Non-Functional Requirements, and Success Metrics.
    Format as a concise PRD (Product Requirements Document) — same ultra-concise rules apply.

    ```
    Write({
      file_path: "[output_path]/prd.md",
      content: [PRD markdown built from the above sections]
    })
    ```

    Tell the user: "✓ prd.md written."

5b. **Write `business-rules.md` (pipeline deliverable — required by stage G1):**

    Extract the Business Rules section (Section 5) from the confirmed sections verbatim.
    Prepend a one-line heading: `# Business Rules`.

    ```
    Write({
      file_path: "[output_path]/business-rules.md",
      content: [business rules markdown]
    })
    ```

    Tell the user: "✓ business-rules.md written."

6. Tell the user: "Converting brd.md to Word document format..."

7. **Convert to DOCX using documents skill:**
   ```
   Skill({skill: "AIDLC-business-analyst:documents"})
   ```
   Then request: "Convert [output_path]/brd.md to brd.docx"

8. Tell the user: "✓ BRD files generated!"

9. **Provide generation summary:**
   - Tell the user: "Generated files:"
   - Tell the user: "  - brd.md: [output_path]/brd.md"
   - Tell the user: "  - prd.md: [output_path]/prd.md"
   - Tell the user: "  - business-rules.md: [output_path]/business-rules.md"
   - Tell the user: "  - brd.docx: [output_path]/brd.docx"
   - Tell the user: "Summary:"
   - Tell the user: "  - Sections: [X] of [Total] completed"
   - Tell the user: "  - Confidence: [average]%"
   - Tell the user: "  - Ambiguities: [X] found"
   - Tell the user: "  - Traceability: [status]"

9. **→ IMMEDIATELY proceed to Phase 5 (Optional Gap/Conflict Resolution)**

### Phase 5: Optional Gap/Conflict Resolution

**After BRD files are generated:**

1. **Check if ambiguities exist in Section 12:**
   - Review the ambiguity_array from Step 3
   - If ambiguity_array is empty or has zero items:
     - Tell user: "✓ BRD generation complete! No ambiguities detected."
     - **→ END workflow**

2. **If ambiguities exist, parse by type:**

   Classify each ambiguity as either a gap or a conflict:
   ```
   gaps = []
   conflicts = []

   for each ambiguity in ambiguity_array:
     desc = ambiguity.short_description.toLowerCase()
     why = ambiguity.why_ambiguous.toLowerCase()

     // Classify as gap if missing/incomplete/undefined
     if (desc includes "lacks" OR desc includes "undefined" OR
         desc includes "missing" OR why includes "without defining"):
       add ambiguity to gaps
     // Classify as conflict if contradictory/conflicting/vague
     else if (desc includes "conflict" OR desc includes "contradictory" OR
              desc includes "vague" OR why includes "doc a says" OR why includes "doc b says"):
       add ambiguity to conflicts
     // Default classification: treat as conflict
     else:
       add ambiguity to conflicts
   ```

3. **Offer resolution via AskUserQuestion:**
   ```
   AskUserQuestion({
     questions: [{
       question: `The BRD has been generated with ${gaps.length + conflicts.length} ambiguities documented in Section 12:
- ${gaps.length} gaps (missing content, incomplete definitions)
- ${conflicts.length} conflicts (contradictory statements, vague language)

Would you like to resolve these now?`,
       header: "Resolution",
       multiSelect: false,
       options: [
         {
           label: "Yes, resolve now (Recommended)",
           description: "Interactive resolution will update the BRD files"
         },
         {
           label: "No, skip",
           description: "Issues will remain documented in Section 12"
         }
       ]
     }]
   })
   ```

   - If user selects "Yes, resolve now": → Proceed to Step 5a
   - If user selects "No, skip":
     - Tell user: "✓ BRD generation complete! Review Section 12 for documented ambiguities."
     - **→ END workflow**

#### Step 5a: Gap Resolution

**Only execute if user chose "Yes, resolve now" AND gaps.length > 0:**

1. **If gaps.length == 0:**
   - Tell user: "No gaps to resolve."
   - → Skip to Step 5b

2. Tell user: "Resolving ${gaps.length} gaps..."

3. **Transform gaps to GapReport format:**

   Create gap report array suitable for gap-resolver skill:
   ```
   gap_report = gaps.map(gap => ({
     sectionNumber: extractSectionNumber(gap.section_name),  // e.g., "3" from "Section 3: Business Rules"
     sectionName: gap.section_name,
     gapDescription: gap.short_description,
     gapType: determineGapType(gap),
     priority: mapSeverityToPriority(gap.severity),
     context: gap.why_ambiguous
   }))

   function extractSectionNumber(sectionName):
     // Extract number from strings like "Section 3" or "3. Business Rules"
     match = sectionName.match(/\d+/)
     return match ? match[0] : "0"

   function determineGapType(gap):
     desc = gap.short_description.toLowerCase()
     if desc includes "metric" OR desc includes "kpi":
       return "missing_metrics"
     if desc includes "rule" OR desc includes "validation":
       return "incomplete_business_rule"
     if desc includes "persona" OR desc includes "stakeholder":
       return "missing_stakeholder_info"
     if desc includes "undefined" OR desc includes "definition":
       return "undefined_term"
     return "general_missing_content"

   function mapSeverityToPriority(severity):
     if severity == "High": return "critical"
     if severity == "Medium": return "important"
     if severity == "Low": return "nice_to_have"
     return "important"
   ```

4. **Save gap report for skill access:**
   ```
   Write({
     file_path: ".claude/AIDLC-business-analyst/gap-reports/latest-gap-report.json",
     content: JSON.stringify(gap_report, null, 2)
   })
   ```

5. Tell user: "Invoking gap-resolver skill..."

6. **Invoke gap-resolver skill:**
   ```
   Skill({skill: "AIDLC-business-analyst:gap-resolver"})
   ```

   The gap-resolver skill will:
   - Read latest-gap-report.json
   - For each gap (in priority order):
     - Ask targeted question via AskUserQuestion
     - Validate user response
     - Update corresponding section in confirmedSections array
   - Return when complete

7. Tell user: "✓ Gap resolution complete."

8. **→ Immediately proceed to Step 5b**

#### Step 5b: Conflict Resolution

**Only execute if user chose "Yes, resolve now" AND conflicts.length > 0:**

1. **If conflicts.length == 0:**
   - Tell user: "No conflicts to resolve."
   - → Skip to Step 5c

2. Tell user: "Resolving ${conflicts.length} conflicts..."

3. **Save conflict context for skill access:**
   ```
   Write({
     file_path: ".claude/AIDLC-business-analyst/conflict-reports/latest-ambiguity-array.json",
     content: JSON.stringify(ambiguity_array, null, 2)
   })

   Write({
     file_path: ".claude/AIDLC-business-analyst/conflict-reports/latest-confirmed-sections.json",
     content: JSON.stringify(confirmedSections, null, 2)
   })
   ```

4. Tell user: "Invoking conflict-resolver skill..."

5. **Invoke conflict-resolver skill:**
   ```
   Skill({skill: "AIDLC-business-analyst:conflict-resolver"})
   ```

   The conflict-resolver skill will:
   - Read latest-ambiguity-array.json and latest-confirmed-sections.json
   - Invoke ambiguity-detector internally for comprehensive analysis
   - Categorize conflicts by type (business rules, requirements, data, process, stakeholders, vague language, missing definitions)
   - For each conflict category:
     - Ask user if they want to resolve this category
     - For each conflict in category:
       - Present conflict with context via AskUserQuestion
       - Offer resolution options (Keep A, Keep B, Merge, Custom)
       - Apply user decision to confirmedSections
   - Maintain audit log
   - Return when complete

6. **Read updated confirmed sections:**
   ```
   confirmedSections = JSON.parse(Read(".claude/AIDLC-business-analyst/conflict-reports/latest-confirmed-sections.json"))
   ```

7. Tell user: "✓ Conflict resolution complete."

8. **→ Immediately proceed to Step 5c**

#### Step 5c: Update BRD with Resolutions

**After gap and conflict resolution complete:**

1. Tell user: "Updating BRD with resolutions..."

2. **Update ambiguity status in Section 12:**

   For each ambiguity in ambiguity_array, mark as resolved if addressed:
   ```
   for each ambiguity in ambiguity_array:
     wasResolved = checkIfResolved(ambiguity, confirmedSections)
     ambiguity.status = wasResolved ? "Resolved" : "Documented only"
     ambiguity.resolution_date = wasResolved ? getCurrentDate() : null
     ambiguity.resolution_notes = wasResolved ? "User provided resolution" : null
   ```

3. **Regenerate Section 12 with resolution status:**
   ```markdown
   ## 12. Ambiguities and Clarifications

   **Summary:** Found {total} ambiguities. {resolved_count} resolved, {unresolved_count} remain.

   | ID | Section | Issue | Severity | Status | Resolution |
   |-----|---------|-------|----------|--------|------------|
   | AMB-001 | Business Rules | Rule BR-005 lacks validation criteria | High | Resolved | User provided validation logic on {date} |
   | AMB-002 | Requirements | Performance threshold undefined | Medium | Documented only | Not addressed |
   | ... | ... | ... | ... | ... | ... |

   **Clarity Score:** {calculate based on resolved vs unresolved}

   **Recommendation:** {If high priority items remain: "X high-priority issues remain" | "All critical issues resolved"}
   ```

4. **Rebuild complete BRD with updated sections:**
   - Rebuild from metadata header through all confirmedSections
   - Include updated Section 12 with resolution status
   - Include traceability sections if present
   - Include appendix

5. **Write updated files:**
   ```
   Write({
     file_path: "[output_path]/brd.md",
     content: [updated BRD markdown]
   })
   ```

6. Tell user: "Converting updated BRD to Word format..."

7. **Convert updated BRD to DOCX:**
   ```
   Skill({skill: "AIDLC-business-analyst:documents"})
   ```
   Then request: "Convert [output_path]/brd.md to brd.docx"

8. Tell user: "✓ BRD updated with resolutions!"

9. **Provide final summary:**
   - Tell the user: "Updated files:"
   - Tell the user: "  - brd.md: [output_path]/brd.md"
   - Tell the user: "  - brd.docx: [output_path]/brd.docx"
   - Tell the user: "Resolution summary:"
   - Tell the user: "  - Gaps resolved: [X] of [Y]"
   - Tell the user: "  - Conflicts resolved: [X] of [Y]"
   - Tell the user: "  - Remaining ambiguities: [X]"

---

## Content Conciseness Principles

**CRITICAL: Target 12-15 pages total (6,000-9,000 words). Each section must be ultra-condensed.**

### Page Budget by Section

Strict allocation to achieve 12-15 page target:

| Section | Max Pages | Format | Max Items |
|---------|-----------|--------|-----------|
| Executive Summary | 0.5 | 3-5 bullets | 5 bullets |
| Business Context | 0.5 | 5 bullets per subsection | 25 bullets total |
| Business Outcomes | 1.0 | Table only | 8 metrics |
| Scope Definition | 1.0 | Bullets only | 40 bullets |
| Business Rules | 1.5 | Table only, NO details section | 10 rules max |
| User Personas | 0.5 | 1 short paragraph per persona | 5 personas |
| Functional Requirements | 2.0 | Table only, 3 bullets max per acceptance criteria | 20 requirements |
| Non-Functional Requirements | 1.0 | Table only | 15 items |
| Success Metrics | 0.5 | KPI table only | 8 KPIs |
| Compliance Requirements | 0.5 | Bullets only | 20 bullets |
| Dependencies/Constraints | 1.0 | Tables only | 10 dependencies, 10 constraints |
| Risks/Mitigations | 1.0 | Table only | 10 risks |
| Ambiguities | 1.5 | Single table only, NO subsections | All ambiguities |
| Appendices | 0.5 | Glossary table only | 30 terms |
| **TOTAL** | **13 pages** | | |

### Universal Compression Rules

1. **ELIMINATE all "Detailed" subsections** - if it's in the summary table, it's complete
2. **NO prose paragraphs** except Executive Summary (3 sentences max)
3. **Tables ≤4 columns** - combine columns aggressively
4. **Single-line bullets** - no explanations, no multi-line
5. **Acceptance criteria = 3 bullets max** (was unlimited)
6. **Business rules = 10 max** (was 15+)
7. **User personas = 1 paragraph each** (was multi-paragraph)
8. **NO examples, rationales, backgrounds, or justifications**
9. **Abbreviate aggressively** - mgr, req, auth, admin, calc, config, etc.
10. **Remove all redundancy** - information appears once only

### Enforcement During Generation

**BEFORE generating each section:**
1. Check page budget allocation for this section
2. If content exceeds budget → prioritize: tables > bullets > minimal prose
3. Cut all explanatory text, examples, background
4. Compress acceptance criteria to 3 bullets max
5. Limit business rules to 10 total (prioritize by criticality)

### Quality Targets

- **Overall document:** 12-15 pages (6,000-9,000 words max)
- **Section reduction:** 65-70% smaller than traditional BRDs
- **Information density:** Every sentence conveys essential data only
- **Zero tolerance:** Reject verbose prose, detailed subsections, examples

### Section Format Examples

**Section 7: Functional Requirements - CORRECT FORMAT**

Use summary table ONLY (no detailed AC text):

| ID | Summary | AC | Pri | SP | Sprint |
|----|---------|----|----|-----|--------|
| US-001 | Create policy guided workflow | 4 | H | 8 | 1 |
| US-002 | Auto-calc premium w/ breakdown | 3 | C | 13 | 1 |
| US-003 | Save draft, resume later | 3 | M | 5 | 2 |

**❌ WRONG - DO NOT DO THIS:**
```
US-001: Create New Policy as an Agent
As an insurance agent, I want to create...
Acceptance Criteria:
- Given I am logged in as an agent
- When I click "Create New Policy"
...
```

**Section 5: Business Rules - CORRECT FORMAT**

Single table only (no "Detailed Rules" subsection):

| Rule ID | Description | Formula/Logic | Priority |
|---------|-------------|---------------|----------|
| BR-001 | Premium Calc | (Coverage × 0.001 + Age × 0.005) × Risk | Critical |
| BR-002 | Age Constraint | 18 ≤ Age ≤ 75 | Critical |

**❌ WRONG - DO NOT DO THIS:**
```
### Detailed Business Rules

**BR-001: Premium Calculation**
- Formula: (Coverage × 0.001...)
- Inputs: Coverage amount (USD), Age...
- Validation: Coverage within BR-003...
- Output: Monthly premium...
- API Endpoint: POST /api/v1/premium/calculate
- Error Handling: Returns HTTP 400...
```

**Section 8: Non-Functional Requirements - CORRECT FORMAT**

Single table only:

| Category | Requirement | Target | Metric |
|----------|-------------|--------|--------|
| Perf | Policy creation | <5s | 95th %ile |
| Perf | Premium calc | <500ms | 99th %ile |
| Scale | Concurrent users | 500 | Max load |
| Security | Auth | MFA + RBAC | All users |

**❌ WRONG - DO NOT DO THIS:**
```
### Performance
**Response Time Targets:**
- Policy Creation: <5 seconds (95th percentile)
  Justification: User expectation...
- Premium Calculation: <500ms (99th percentile)
  Rationale: Inline calculation...
### Scalability
**Concurrent Users:** Support 500...
### Security
**Authentication:**
- MFA required for all users...
```

**Section 6: User Personas - CORRECT FORMAT**

One short paragraph per persona (3-4 sentences max):

**Agent (Sarah, 32):** 8 yrs exp, 60% field/40% office. Creates policies on iPad with offline sync. Goals: reduce creation time, minimize errors.

**Manager (Michael, 45):** Oversees 15 agents, approves 30-50 policies/week. Needs dashboard with urgency prioritization. Goals: quality standards, <24hr approval cycle.

**❌ WRONG - DO NOT DO THIS:**
```
**Persona 1: Insurance Agent (Sarah)**

Sarah is a 32-year-old insurance agent with 8 years of experience. She spends 60% of her time in the field meeting customers and 40% in the office...

**Background:** Sarah has been with the company for...
**Goals and Motivations:** Her primary goals are...
**Frustrations:** She finds the current system...
**Technology Usage:** She is comfortable with...
```

---

## Graceful Degradation Rules

1. **Never leave sections empty** - Always include section headers
2. **Mark missing information clearly** - Use "[Not available in source documents]"
3. **Provide partial information** - Include what is available, mark what's missing
4. **Coverage summary** - Always include document coverage summary at top
5. **Source attribution** - Note which source documents provided which sections

---

## Guardrails

```
OPERATIONAL_CONSTRAINTS[Type,Rules]:
  ALWAYS,"enforce 12-15 page target (6000-9000 words max) | CRITICAL: validate word count before writing files | if >9000 words apply emergency compression | check page budget before generating each section | apply Pre-Generation Enforcement Checklist before each section | reject verbose prose except Executive Summary (3 sentences max) | use tables and bullets exclusively | eliminate ALL 'Detailed' subsections | NEVER generate detailed acceptance criteria text for user stories | NEVER generate subsection explanations for NFRs | NEVER generate subsections for ambiguities | Section 7 Functional Requirements: summary table ONLY (ID | Summary | AC Count | Priority | SP | Sprint) | Section 8 NFRs: single table ONLY, NO subsections | Section 5 Business Rules: single table ONLY, NO 'Detailed Rules' subsection | Section 6 User Personas: 1 paragraph each (3-4 sentences) | Section 13 Ambiguities: single summary table ONLY | limit business rules to 10 max | compress all content to single-line bullets | remove examples, rationales, backgrounds, justifications | abbreviate aggressively (usr, mgr, req, sys, calc, admin, auth, perf, impl, config) | enforce 4-column max for tables | collapse tables with >4 columns by combining columns | communicate what you're doing at each step | follow the exact workflow defined in this skill | invoke brd-template skill for template loading (Phase 0) | let brd-template skill handle all template selection | invoke document-processor agent via Task tool for document processing and content extraction (Phase 1) | store mappingFile path from document-processor (Phase 1) | read JSON mapping file for section content (Phase 3) | use pre-extracted keyPoints and condensed summaries from mapping | filter paragraphs by section_in_brd field | prioritize high-confidence paragraphs (>0.8) | discover output path before generation using 3-tier approach (Phase 2) | ask user to select generation mode (Phase 2.5) using AskUserQuestion | automatically proceed after user selects generation mode | use TodoWrite to track section generation progress | IN INTERVIEW MODE: use interactive section-by-section generation with interview loop | IN INTERVIEW MODE: USE AskUserQuestion tool for EVERY section review (never just ask in plain text) | IN INTERVIEW MODE: show first 500 characters of draft with summary of rest if section exceeds 500 chars | IN INTERVIEW MODE: for sections ≤500 chars show complete content | IN INTERVIEW MODE: present draft with sources/gaps/confidence in confirmation prompt | IN INTERVIEW MODE: provide THREE options: Approve, Modify (with text input), Note as gap and address later | IN INTERVIEW MODE: when user selects defer as gap option, mark section as deferred and move to next section | IN DIRECT MODE: auto-generate all sections without user interaction | IN DIRECT MODE: tell user progress as each section is generated | apply Content Conciseness Principles throughout BRD generation in both modes | prefer tables over prose | use compact notation and abbreviations | maximize information density | eliminate redundancy | target 65-70% size reduction | collect all confirmed/generated sections before ambiguity detection | invoke ambiguity-detector agent via Task tool after all sections complete (Step 3) | format ambiguities into Section 12 without resolution (Step 3a) | proceed to traceability (Step 4) after formatting ambiguities | invoke traceability-builder agent via Task tool if template includes traceability (Step 4) | generate both .md and .docx files (Phase 4) | AFTER BRD generation: check if ambiguities exist in Section 12 (Phase 5) | AFTER BRD generation: offer interactive resolution via AskUserQuestion if ambiguities found | AFTER BRD generation: respect user's choice to resolve or skip | parse ambiguity_array into gaps vs conflicts in Phase 5 | invoke gap-resolver skill if user chooses to resolve AND gaps exist (Step 5a) | invoke conflict-resolver skill if user chooses to resolve AND conflicts exist (Step 5b) | save gap_report.json and conflict context files before invoking resolution skills | update Section 12 with resolution status after resolution (Step 5c) | regenerate brd.md and brd.docx with updates after resolution | mark resolved items with resolution date and notes | mark unresolved items as Documented only | include all template sections | ALWAYS run ambiguity detection | ALWAYS include Ambiguity Identification section in BRD | handle complete end-to-end workflow | provide status updates during long operations"
  NEVER,"work silently without communicating | prompt user for template selection (let brd-template skill handle this) | skip generation mode selection (Phase 2.5) | invent business requirements or add fabricated information | process documents inline using Glob/Grep (use document-processor agent) | read normalized markdown files directly (use JSON mapping instead) | ignore pre-extracted keyPoints and condensed summaries | detect ambiguities inline (use ambiguity-detector agent) | generate traceability inline (use traceability-builder agent) | skip agent invocation when agents should be used | skip output path discovery | leave sections blank without explanation | assume section numbers or names | skip ambiguity detection | generate traceability sections if template does not include them | skip length validation in Phase 4 | write files without checking word count | generate detailed acceptance criteria for user stories | generate 'Detailed Rules' or 'Detailed Business Rules' subsections | generate subsection prose for NFRs (Performance, Security, etc.) | generate subsections for Ambiguities section (keep single table only) | use multi-line bullets or explanations | include examples, rationales, or justifications in output | IN INTERVIEW MODE: proceed to next section without user confirmation via AskUserQuestion tool | IN INTERVIEW MODE: ask for section confirmation in plain text without using AskUserQuestion tool | IN INTERVIEW MODE: show entire draft content without truncation for sections >500 chars | IN INTERVIEW MODE: provide only two options (must include all three: approve, modify, defer) | IN DIRECT MODE: stop for user input during section generation | move to ambiguity detection before ALL sections are confirmed/generated | offer resolution BEFORE generating BRD files (resolution happens AFTER BRD generation in Phase 5) | skip optional resolution check after BRD generation (Phase 5) | force user to resolve ambiguities (must offer skip option) | lose track of which ambiguities were resolved | fail to update confirmedSections with resolutions | fail to regenerate BRD files after resolution | invoke resolution skills without saving required context files first | add information not present in source documents during revisions | include verbose explanations or examples in output | use unnecessary articles or full sentences when fragments suffice"
  CONDITIONALLY,"generate traceability sections (ONLY if template includes traceability tables) | use interactive review (ONLY if generationMode = interview) | auto-generate sections (ONLY if generationMode = direct) | offer gap/conflict resolution (ONLY if ambiguities exist in Section 12 after BRD generation)"
  STOP_AND_ASK,"cannot find or process documents | output path discovery fails all 3 tiers"
```

---

## Version and Compatibility

**Version:** 1.18.0 (Enforced ultra-concise output with validation)

**Compatible with:**
- BRD Template Skill v2.1.0+
- Documents Skill v1.0.0+
- Document Processor Agent v3.0.0+ (via Task) - **BREAKING: Now returns JSON mapping with paragraph-level content**
- Ambiguity Detector Agent v1.0.0+ (via Task)
- Traceability Builder Agent v1.0.0+ (via Task)
- Gap Resolver Skill v1.0.0+ (optional, Phase 5)
- Conflict Resolver Skill v1.0.0+ (optional, Phase 5)
- Rally Hierarchy Skill v1.0.0+ (optional, via traceability-builder)
- Rally API Skill v1.4.5+ (optional, via traceability-builder)

**Changelog:**
- v1.18.0: **CRITICAL ENFORCEMENT UPDATE** - Added Pre-Generation Enforcement Checklist with hard limits for common sections; Section 7 (Functional Requirements) now MUST be summary table only (no detailed AC text); Section 8 (NFRs) single table only with NO subsection explanations; Section 13 (Ambiguities) single table only with NO subsections by type; Added length validation step in Phase 4 (counts words, applies emergency compression if >9000 words); Added Section Format Examples showing correct vs wrong formats; Strengthened guardrails with explicit NEVER rules for verbose content; Updated both Interview and Direct workflows with enforcement checkpoints; Added emergency compression algorithm for oversized BRDs
- v1.17.0: **MAJOR UPDATE** - Enforced ultra-concise 12-15 page output target (6,000-9,000 words); Added strict page budget allocation table (Executive Summary 0.5 pages, Business Rules 1.5 pages max, etc.); Eliminated all "Detailed" subsections; Limited business rules to 10 max (was 15+); Limited acceptance criteria to 3 bullets max (was unlimited); Limited user personas to 1 paragraph each; Added page budget enforcement before each section generation; Updated guardrails to reject verbose prose, detailed subsections, examples; Target 65-70% size reduction (was 40-50%); Zero tolerance for verbosity
- v1.16.0: **BREAKING CHANGE** - Document-processor now returns JSON mapping with paragraph-level content extraction; ba-brd reads mappingFile for section population; Uses pre-extracted keyPoints and condensed summaries; Filters paragraphs by section_in_brd field; Prioritizes high-confidence content; Updated Phase 1 to store mappingFile path; Updated Phase 3 (Interview Mode) and Direct Mode to read JSON mapping; Added paragraph-level metadata tracking; Improved section generation accuracy with semantic mapping
- v1.15.0: REVERTED - Document-processor returned file paths instead of JSON mapping (reverted in v1.16.0)
- v1.14.1: Refined interview preview to show first 500 characters with summary of rest for sections >500 chars; Prevents overwhelming users with very long section previews; Updated guardrails for truncated preview behavior
- v1.14.0: Changed resolution workflow to occur AFTER BRD generation (new Phase 5); Interview mode now shows draft preview instead of summary; Added third option in interview mode to defer gaps ("Note this as a gap and address later"); Resolution now offered after brd.md and brd.docx are generated; BRD files regenerated with updates after resolution; Enhanced deferred gap tracking; Updated guardrails for post-generation resolution workflow
- v1.13.0: Removed redundant template verification (Phase -1); Template selection now handled entirely by brd-template skill; Eliminates duplicate user prompts for template choice; Simplified workflow from 9 phases to 8 phases; Updated guardrails to prevent template prompting in ba-brd skill
- v1.12.0: Added dual-mode generation with Phase 2.5 Generation Mode Selection; Users can choose between Interview Mode (section-by-section review with AskUserQuestion) or Direct Mode (auto-generate all sections without user interaction); Automatic continuation after mode selection; Updated workflow overview and guardrails to support both modes
- v1.11.0: Replaced detailed section-by-section condensing strategies with general Content Conciseness Principles; Enhanced interview preview with intelligent summaries (item counts + themes + examples for long sections, 150-word limit for prose); Updated guardrails for general conciseness approach
- v1.10.0: Enhanced condensing strategies with ultra-aggressive formatting (target: 40-50% overall reduction), mathematical notation for rules, multi-column requirement tables, remove Source Documents Reference section, 83% reduction in Ambiguities section, compact citation format
- v1.9.1: Added explicit continuity instructions between all phases (IMMEDIATELY proceed to next phase)
- v1.9.0: Outsourced document processing, ambiguity detection, and traceability to specialized agents via Task tool
- v1.8.1: Removed auto-approve functionality, simplified to two-option review (Approve/Modify)
- v1.8.0: Added template verification phase, streamlined section review options (Approve/Modify), aggressive content condensing (70-85% reduction)
- v1.7.0: Removed brd-generator agent dependency, direct execution for user interaction
- v1.6.0: Added Rally traceability, source citations, --epic flag
- v1.5.0: Added DOCX output via documents skill
- v1.0.0: Initial release

**Last Updated:** 2026-02-09 (v1.17.0 - Ultra-concise output enforcement)

---

## JSON Mapping Structure Reference

The document-processor agent generates a JSON mapping file with this structure:

```json
[
  {
    "document_name": "technical_specifications.md",
    "paragraph_index": 0,
    "section_in_brd": "2. Business Context",
    "reason_for_mapping": "Contains system architecture overview and technology justification",
    "content": "[Full paragraph text from source document]",
    "keyPoints": [
      "Three-tier architecture: Presentation, Application, Data layers",
      "Angular 18 for frontend, Node.js Lambda for backend",
      "PostgreSQL with Redis caching for data persistence"
    ],
    "condensed": "System uses three-tier architecture with Angular frontend, Node.js Lambda backend, and PostgreSQL database with Redis cache.",
    "confidence": 0.95,
    "requires_review": false,
    "ambiguity_flag": null
  }
]
```

**Usage in BRD Generation:**
- Filter by `section_in_brd` to get paragraphs for specific sections
- Use `keyPoints` for table rows and bullet lists
- Use `condensed` for concise summaries
- Check `confidence` to prioritize high-quality content (>0.8)
- Flag `requires_review` paragraphs as gaps
- Track `document_name` for source traceability

---

## Reporting to the AIDLC Platform (ECS UI tracking)

> **SERVER CLI GUARD:** If `aidlc.config.json` does **not** exist in your current
> working directory, skip this entire ECS section and run only the main workflow
> above. These steps are for the **local Claude Code session only**.

Run Step 0 and Step 0.5 **before** starting the main workflow above so ECS
captures the full local execution time. Run Steps 1–3 after `prd.md`, `brd.md`,
and `business-rules.md` are written (Phase 4, Steps 5–5b).
Requires `aidlc.config.json` in the project root and `$env:AIDLC_TOKEN` set.

Note: `PATCH /pipeline-runs/{id}/status` and `POST /pipeline-runs/{id}/deliverables`
are not supported by this server. The `execute: true` + worktree pre-population
pattern is the only supported completion path.

**Step 0 — Read config and resolve output directory**
```powershell
$cfg     = Get-Content "aidlc.config.json" | ConvertFrom-Json
$BASE    = $cfg.central_base_url
$TOKEN   = [System.Environment]::GetEnvironmentVariable($cfg.auth.bearer_env)
$WS      = $cfg.workspace_id
$HDR     = @{ Authorization = "Bearer $TOKEN"; "Content-Type" = "application/json" }
$OUT_DIR = "workspaces\$WS\design"
New-Item -ItemType Directory -Force $OUT_DIR | Out-Null
Write-Host "Output directory: $OUT_DIR"
```

**Step 0.5 — Create ECS run BEFORE local work begins (captures true start time)**
```powershell
# Cancel stale queued runs for prd stage (worktree=null means they never started)
$allRuns = (Invoke-RestMethod -Uri "$BASE/pipeline-runs?workspace_id=$WS" -Headers $HDR).runs
$allRuns | Where-Object { $_.stage_key -eq "prd" -and $_.state -eq "queued" -and (-not $_.worktree) } | ForEach-Object {
    Invoke-RestMethod -Uri "$BASE/pipeline-runs/$($_.run_id)/cancel" -Headers $HDR -Method Post | Out-Null
}
$body0   = @{ stage = "prd"; workspace_id = $WS; execute = $true; force = $true } | ConvertTo-Json
$init    = (Invoke-RestMethod -Uri "$BASE/pipeline-runs" -Headers $HDR -Method Post -Body $body0).run
$RUN_ID  = $init.run_id
$WT_PATH = $init.worktree
if ($WT_PATH) { New-Item -ItemType Directory -Force -Path $WT_PATH | Out-Null }
Write-Host "ECS run created: $RUN_ID  (queued_at = true start time)"
Write-Host "Worktree: $WT_PATH"
```

→ **Now execute the skill workflow above.** When `prd.md`, `brd.md`, and
`business-rules.md` are written (Phase 4, Steps 5–5b), continue below.

**Step 1 — Pre-populate worktree with deliverables (recover if CLI timed out)**
```powershell
# If initial run failed (CLI hit max_turns during generation), create a fresh run
$status = (Invoke-RestMethod -Uri "$BASE/pipeline-runs/$RUN_ID" -Headers $HDR).run
if ($status.state -in @("failed","cancelled")) {
    Write-Host "Run $RUN_ID ended as '$($status.state)'. Creating fresh run..."
    $bodyF   = @{ stage = "prd"; workspace_id = $WS; execute = $true; force = $true } | ConvertTo-Json
    $fresh   = (Invoke-RestMethod -Uri "$BASE/pipeline-runs" -Headers $HDR -Method Post -Body $bodyF).run
    $RUN_ID  = $fresh.run_id
    $WT_PATH = $fresh.worktree
    Write-Host "Fresh run: $RUN_ID"
}
if ($WT_PATH) {
    $destDir = "$WT_PATH\workspaces\$WS\design"
    New-Item -ItemType Directory -Force -Path $destDir | Out-Null
    Copy-Item "$OUT_DIR\prd.md"             "$destDir\" -Force
    Copy-Item "$OUT_DIR\brd.md"             "$destDir\" -Force -ErrorAction SilentlyContinue
    Copy-Item "$OUT_DIR\business-rules.md"  "$destDir\" -Force -ErrorAction SilentlyContinue
    Write-Host "Deliverables pre-populated in worktree — server CLI will finalize promptly."
}
```

**Step 2 — Poll until `waiting_for_approval`**
```powershell
$maxWait = 600   # 10 minutes
$elapsed = 0
do {
    Start-Sleep -Seconds 15
    $elapsed += 15
    $check = (Invoke-RestMethod -Uri "$BASE/pipeline-runs/$RUN_ID" -Headers $HDR).run
    Write-Host "[$elapsed s] state = $($check.state)"
} while ($check.state -notin @("waiting_for_approval","completed","failed","cancelled") `
         -and $elapsed -lt $maxWait)

switch ($check.state) {
    "waiting_for_approval" {
        Write-Host "✓ PRD/BRD stage is waiting for Business Analyst approval."
        Write-Host "  ECS: https://poc-ecs-build-ui.npsdlchgapp.us-east-1.aws.aig.net/aidlc-platform-management/pipeline"
    }
    "failed" {
        Write-Host "✗ Run failed: $($check.error)"
        Write-Host "  Check the ECS dashboard and retry via /launch $WS"
    }
    default {
        Write-Host "Run ended with state=$($check.state)"
    }
}
```

**Step 3 — Review and approve in ECS UI**
`https://poc-ecs-build-ui.npsdlchgapp.us-east-1.aws.aig.net/aidlc-platform-management/pipeline`

Approval persona: **Business Analyst**
