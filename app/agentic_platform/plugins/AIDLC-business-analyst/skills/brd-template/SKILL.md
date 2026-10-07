---
name: brd-template
description: AI-powered BRD template parser that converts ANY document into a reusable template using semantic analysis. Supports DOCX, MD formats. Automatically detects and replaces instance-specific values with context-aware placeholders.
allowed-tools: Read, Glob, Grep, Skill, Write
---

# BRD Template - AI-Powered Template-Driven Architecture

**Executable skill that loads, converts, parses, and validates BRD templates using AI-powered semantic analysis.**

This skill handles the complete template lifecycle:
1. Load template from path (user-provided or default)
2. Convert to markdown if needed (DOCX → MD)
3. Detect if it's an example document or proper template
4. **AI-powered conversion**: Convert examples to templates with semantic placeholders
5. Parse template structure (sections, subsections, tables)
6. Return structured template object for BRD generation

**Key Innovation:** Unlike traditional regex-based approaches, this skill uses **AI semantic analysis** to identify instance-specific values in ANY format, creating context-aware placeholders that work universally.

## When to Invoke This Skill

Invoke this skill when:
- Starting BRD generation (to load and parse template)
- User provides a custom template document
- Need to validate template completeness
- Need to extract BRD section structure

**Invocation:**
```
Skill(skill: "business-analyst:brd-template")
```

Then provide either:
- Path to custom template file
- "Use default template"

## Template-Driven Approach

**Key Principle:** Template structure is NOT hardcoded. The BRD structure is dynamically extracted from the template provided.

### Template Sources

1. **User-provided template** (highest priority):
   - Custom DOCX or MD file provided by user
   - Defines organization-specific BRD format
   - Structure extracted dynamically

2. **Pre-defined templates** (fallback):
   - **SAFE BRD template**: `skills/brd-template/pre-defined-templates/safe-brd-v1.1.md`
     - Follows SAFe methodology
     - Comprehensive structure with all standard sections
   - **Legacy Inscore template**: `skills/brd-template/pre-defined-templates/legacy-inscore-v1.0.md`
     - Organization-specific legacy format
     - Backward compatibility for existing projects

### Template Structure Extraction

When loading a template, the parser extracts:
- **Section names and hierarchy** (H1/H2/H3 headings)
- **Subsections and nesting** (multi-level structure)
- **Table structures** (columns, headers, row patterns)
- **Content placeholders** (e.g., `{project name}`, `{date}`)
- **Styling markers** (bold, italics, lists, formatting)
- **Section ordering** (1, 2, 3... or without numbers)

## Execution Workflow

When this skill is invoked, execute the following steps in order:

### Step 1: Determine Template Path

1. Check the user's request or previous context for a template path

2. **If no template path was provided in the user's input:**
   - Use the AskUserQuestion tool to present 3 options:
     ```
     Question: "Which BRD template would you like to use?"
     Options:
     1. "Upload your own template (Recommended)"
        - Description: "Provide a path to your organization's custom BRD template (DOCX or MD format). This ensures the output matches your specific requirements and formatting standards."
     2. "Use SAFE BRD template"
        - Description: "Comprehensive template following SAFe methodology with all standard sections for business analysis. Best for new projects or when no custom template is available."
     3. "Use legacy Inscore template"
        - Description: "Organization-specific legacy format for backward compatibility with existing projects."
     ```
   - Process user's choice:
     - **If option 1 (custom template):**
       - Ask user: "Please provide the path to your custom template file"
       - Set `templatePath` = user-provided path
       - Tell user: "Loading custom template from [path]"
     - **If option 2 (SAFE BRD):**
       - Set `templatePath` = `plugins/AIDLC-business-analyst/skills/brd-template/pre-defined-templates/safe-brd-v1.1.md`
       - Tell user: "Using SAFE BRD template"
     - **If option 3 (Legacy Inscore):**
       - Set `templatePath` = `plugins/AIDLC-business-analyst/skills/brd-template/pre-defined-templates/legacy-inscore-v1.0.md`
       - Tell user: "Using legacy Inscore template"

3. **If user explicitly provided a custom template path** (e.g., `./docs/custom-template.docx`):
   - Set `templatePath` = the provided path
   - Tell user: "Loading custom template from [path]"

4. **If user said "use SAFE template" or "use default template":**
   - Set `templatePath` = `plugins/AIDLC-business-analyst/skills/brd-template/pre-defined-templates/safe-brd-v1.1.md`
   - Tell user: "Using SAFE BRD template"

5. **If user said "use legacy template" or "use Inscore template":**
   - Set `templatePath` = `plugins/AIDLC-business-analyst/skills/brd-template/pre-defined-templates/legacy-inscore-v1.0.md`
   - Tell user: "Using legacy Inscore template"

### Step 2: Load and Convert Template to Markdown

1. Detect the file format from the template path extension:
   - `.md` or `.txt` → markdown format
   - `.docx` or `.pdf` → needs conversion

2. **If format is markdown (.md or .txt):**
   - Use `Read({file_path: templatePath})` to load the file
   - Store content as `templateContent`
   - Tell user: "✓ Template loaded ([X] characters)"

3. **If format is DOCX or PDF:**
   - Tell user: "Converting [format] to markdown..."
   - Invoke documents skill: `Skill({skill: "AIDLC-business-analyst:documents"})`
   - Request: "Extract markdown content from [templatePath]"
   - Store the returned markdown as `templateContent`
   - Tell user: "✓ Template converted to markdown"

### Step 3: Detect Template Type

Determine if the loaded document is a proper template or an example document:

1. Count placeholders in curly braces using pattern `\{[^}]+\}`
   - Extract all matches from `templateContent`
   - Store count as `placeholderCount`

2. Check for template marker keywords:
   - Does content include `{project name}`?
   - Does content include `{date}`?
   - Does content include `{author}`?
   - Does content include `[Insert `?
   - Store result as `hasMarkers` (true/false)

3. Determine template type:
   - If `placeholderCount >= 10` OR `hasMarkers == true`:
     - Set `templateType` = "template"
     - Tell user: "✓ Detected proper template with placeholders"
   - Otherwise:
     - Set `templateType` = "example"
     - Tell user: "⚠ Detected example document (will convert to template)"

### Step 4: Convert Example to Template (If Needed)

**Only execute this step if `templateType == "example"`**

This step uses **AI-powered semantic analysis** to identify and replace instance-specific values with generic placeholders, ensuring the template works for any document regardless of formatting conventions.

1. Tell user: "Converting example document to template with placeholders..."

2. **AI-Powered Value Detection:**

   Analyze the document to identify all instance-specific values that should become placeholders.

   **Analysis Prompt:**
   ```
   Analyze this BRD document and identify ALL specific instance values that should be replaced with placeholders to create a reusable template.

   For each value found, provide:
   - The EXACT text to replace (including any special characters, spacing, capitalization)
   - An appropriate semantic placeholder name in {curly braces}
   - The category/type of value
   - Count of occurrences in the document

   Categories to look for:
   - Project/product/system names
   - Person names (in any format: "Dr. Smith", "Jean-Pierre", "O'Brien", etc.)
   - Dates (any format: "2026-02-09", "Q1 2026", "February 9, 2026", etc.)
   - Version numbers (any format: "1.0", "v2.3", "Alpha 1.0", etc.)
   - Email addresses
   - Company/organization names
   - Requirement IDs (any format: "REQ001", "FR-1", "US-123", etc.)
   - Metrics with units (any format: "10,000 users", "500ms", "99.99%", etc.)
   - Budget/financial values ("$250K", "€1M", etc.)
   - URLs and domains
   - Technical terms that are instance-specific (server names, database names, etc.)
   - Job titles/roles
   - Department names
   - Location/geography references

   IMPORTANT RULES:
   1. Only flag values that are SPECIFIC to this instance (not generic terms)
   2. Do NOT replace: generic section headers, common technical terms (like "API", "database"), standard acronyms
   3. For protocol versions (like "TLS 1.3"), distinguish between:
      - Document/product version → Replace
      - Protocol/standard version → Keep literal
   4. Create SEMANTIC placeholder names that describe the value's purpose
      - Good: {user capacity}, {response time target}, {product owner}
      - Bad: {value}, {name}, {number}
   5. Sort by text length (longest first) to avoid partial replacements

   Document content:
   ---
   [templateContent]
   ---

   Return ONLY a JSON array with this exact structure:
   [
     {
       "text": "exact text to find",
       "placeholder": "{semantic placeholder name}",
       "category": "category_name",
       "occurrences": count
     }
   ]
   ```

3. **Parse AI Response:**

   a. Extract the JSON array from the AI response

   b. Validate each replacement entry:
      - Must have: `text`, `placeholder`, `category`, `occurrences`
      - `placeholder` must be in `{curly braces}` format
      - `text` must not be empty

   c. Sort replacements by text length (longest first)
      - Prevents partial replacements (e.g., replace "Invoice Processing System" before "Invoice")

   d. Deduplicate by `text` (keep first occurrence)

4. **Apply Replacements:**

   a. Create a copy: `convertedTemplate` = `templateContent`

   b. Initialize tracking:
      - `replacementsMade` = []
      - `categoriesFound` = new Set()

   c. For each replacement in sorted order:
      - Check if `text` exists in `convertedTemplate`
      - If found:
        - Replace ALL occurrences of `text` with `placeholder` (exact match)
        - Add to `replacementsMade`: {text, placeholder, category, count}
        - Add `category` to `categoriesFound`
      - If not found:
        - Log warning: "Value not found: [text]"

   d. Update `templateContent` = `convertedTemplate`

5. **Validate Conversion Quality:**

   a. Count total placeholders created: `placeholderCount` = count of `{...}` in converted template

   b. Check minimum threshold:
      - If `placeholderCount < 5`:
        - Warn user: "⚠️ Only created {count} placeholders. Template may still contain instance-specific data."
        - Suggest: "Consider manually reviewing the processed template."
      - If `placeholderCount >= 5`:
        - Tell user: "✓ Successfully created {count} placeholders"

   c. Report categories found:
      - Tell user: "✓ Detected categories: {list of categories}"

6. **Add BRD Metadata Header (if missing):**

   a. Check if document starts with standard BRD metadata:
      - Look for: "# Business Requirements Document", "**Document Version:**", etc.

   b. If missing or incomplete, prepend:
      ```markdown
      # Business Requirements Document (BRD)
      ## {project name}

      **Document Version:** {version}
      **Date:** {date}
      **Author:** {author}
      **Business Owner:** {business owner}
      **Status:** Draft

      ---

      ```

   c. Tell user: "✓ Added BRD metadata header"

7. **Final Reporting:**

   a. Create summary:
      - Total replacements made: {count}
      - Categories detected: {list}
      - Most common category: {category with most replacements}

   b. Tell user: "✓ Converted to template format with {count} placeholders across {n} categories"

   c. List top 5 most frequent replacements:
      - "{placeholder}" replaced "{text}" ({count} times)

**Example AI Output:**
```json
[
  {
    "text": "Customer Feedback Management System",
    "placeholder": "{project name}",
    "category": "project_name",
    "occurrences": 8
  },
  {
    "text": "Dr. Sarah Smith",
    "placeholder": "{product owner}",
    "category": "person_name",
    "occurrences": 3
  },
  {
    "text": "Q1 2026",
    "placeholder": "{release date}",
    "category": "date",
    "occurrences": 2
  },
  {
    "text": "10,000 concurrent users",
    "placeholder": "{user capacity}",
    "category": "metric",
    "occurrences": 1
  }
]
```

**Benefits of AI-Powered Approach:**
- ✅ Handles ANY format (not limited to predefined regex patterns)
- ✅ Context-aware (distinguishes protocol versions from document versions)
- ✅ Semantic placeholders (meaningful names, not generic {value})
- ✅ Flexible (adapts to unconventional naming, dates, formats)
- ✅ Comprehensive (finds values regex would miss)

### Step 5: Parse Template Structure

Extract section hierarchy and metadata from the template:

1. Initialize empty arrays:
   - `sections` = []
   - `allPlaceholders` = new Set()

2. Split `templateContent` into lines

3. Initialize tracking variables:
   - `currentSection` = null
   - `currentContent` = []

4. **For each line in the template:**

   a. Check if line is a heading using pattern: `^(#{1,4})\s+(\d+\.?)?\s*(.+)$`

   b. **If line is a heading:**
      - If `currentSection` exists:
        - Set `currentSection.content` = join `currentContent` with newlines
        - Add `currentSection` to `sections` array
      - Extract from heading:
        - `level` = count of `#` symbols (1-4)
        - `number` = the numeric part (if present, e.g., "1", "2.1")
        - `name` = the section name text
      - Create new `currentSection` object:
        ```
        {
          number: [number or null],
          name: [name],
          level: [level],
          subsections: [],
          placeholders: [],
          tableStructures: [],
          required: ![name includes "optional" case-insensitive],
          content: ""
        }
        ```
      - Reset `currentContent` = []

   c. **If line is not a heading:**
      - Add line to `currentContent` array

   d. Check for placeholders in line using pattern: `\{[^}]+\}`
      - If found, add each to `allPlaceholders` set
      - If `currentSection` exists, add to its `placeholders` array

5. After processing all lines:
   - If `currentSection` exists:
     - Set `currentSection.content` = join `currentContent`
     - Add to `sections` array

6. **Build section hierarchy:**
   - Create `root` = []
   - Create `stack` = []
   - For each section in `sections`:
     - While stack is not empty AND top of stack has level >= current section level:
       - Pop from stack
     - If stack is empty:
       - Add section to `root`
     - Else:
       - Add section to top of stack's `subsections` array
     - Push section onto stack
   - Set `hierarchicalSections` = `root`

7. **Detect ID format from template:**
   - Try these patterns in order:
     - `\b([A-Z]{2,4})[-_]([A-Z]{1,3})(\d{2,4})\b` (e.g., SEC_BR01)
     - `\b([A-Z]{2,4})-(\d{3,5})\b` (e.g., REQ-001)
     - `\b([A-Z]+)(\d{3,5})\b` (e.g., BR001)
   - If match found:
     - Set `idFormat` = matched string with digits replaced by 'X'
   - Else:
     - Set `idFormat` = "PRE_XXX"

8. Create final `templateStructure` object:
   ```
   {
     sections: hierarchicalSections,
     totalSections: sections.length,
     placeholders: Array.from(allPlaceholders),
     idFormat: idFormat,
     templatePath: templatePath
   }
   ```

9. Tell user: "✓ Template structure parsed: [X] sections, [Y] placeholders"

### Step 6: Save Processed Template (Optional)

1. Extract filename from `templatePath` (remove extension)
2. Create output path: `plugins/AIDLC-business-analyst/skills/brd-template/processed-[filename].md`
3. Use `Write({file_path: outputPath, content: templateContent})` to save
4. Tell user: "✓ Processed template saved for reference"

### Step 7: Return Template Structure

1. Tell user: "Template processing complete. Ready for BRD generation."
2. Communicate back to calling context the `templateStructure` object with:
   - List of sections (with names, numbers, levels, hierarchy)
   - Total section count
   - Placeholder list
   - ID format pattern
   - Template path used

## Pre-defined Template Structures

### SAFE BRD Template

The SAFE BRD template (`skills/brd-template/pre-defined-templates/safe-brd-v1.1.md`) includes:

- **Title Page** - Document title, project information, stakeholders
- **Tracking Page** - Version control, review, and approval tables
- **Table of Contents** - Linked navigation
- **Introduction** - Document overview and context
- **Purpose** - Problem statement and objectives
- **Scope** - In-scope, out-of-scope, boundaries
- **Assumptions** - Project assumptions with IDs
- **Business Requirements** - Functional requirements with IDs
- **Additional Context** - Supporting information
- **Rules** - Business rules and validation logic with IDs
- **Non-Functional Requirements** - Performance, security, scalability
- **Ambiguities and Clarifications** - Detected gaps, conflicts, vague language
- **Requirement Traceability Matrix** (Optional) - Links to Epic/Feature IDs
- **Source Documents Reference** (Optional) - Citation tracking
- **Appendix** - Glossary, references, acronyms, change history

### Legacy Inscore Template

The legacy Inscore template (`skills/brd-template/pre-defined-templates/legacy-inscore-v1.0.md`) includes organization-specific sections for backward compatibility with existing projects.

**Note:** Custom templates can have different section names, counts, and ordering. The generator adapts to the template structure.

## ID Format Convention

The default template uses **PRE_XXX** format where:
- **PRE** = Project/domain prefix code (e.g., SEC for security, FIN for financial)
- **XXX** = Sequential number or alphanumeric identifier

**Examples:**
- Assumptions: `SEC_A01`, `SEC_A02`, `FIN_A01`
- Business Requirements: `SEC_BR01`, `SEC_BR02`, `FIN_BR01`
- Rules: `SEC_R001`, `SEC_R002`, `FIN_R001`

**Custom templates can define their own ID format**, which will be detected during template parsing.

## Output Example

After processing, the skill communicates back a template structure. Here's an example of what's returned:

**Sections (Array):**
```
[
  {
    number: "1",
    name: "Title Page",
    level: 2,
    subsections: [],
    placeholders: ["{project name}", "{date}", "{author}"],
    required: true,
    content: "**Document Title:** {Project Name}..."
  },
  {
    number: "2",
    name: "Business Requirements",
    level: 2,
    subsections: [
      {
        name: "Functional Requirements",
        level: 3,
        placeholders: ["{requirement id}"],
        required: true
      }
    ],
    required: true
  },
  ... (additional sections)
]
```

**Summary:**
- Total Sections: 15
- Placeholders: ["{project name}", "{date}", "{author}", "{version}", ...]
- ID Format: "PRE_XXX"
- Template Path: "plugins/AIDLC-business-analyst/skills/brd-template/pre-defined-templates/safe-brd-v1.1.md" (or custom path)

## Template Files

This skill manages:
- **Pre-defined templates** in `skills/brd-template/pre-defined-templates/`:
  - **safe-brd-v1.1.md** - SAFE methodology template (recommended default)
  - **legacy-inscore-v1.0.md** - Legacy Inscore format
- **processed-*.md** - Processed templates saved for reference (auto-generated)

## Example Execution Flows

### Example 1: No Template Provided (Interactive Selection)

**User Request:** "Generate a BRD" (no template specified)

**Execution:**
1. ℹ️ No template provided - presenting options to user
2. [AskUserQuestion presents 3 options: Upload custom, SAFE BRD, Legacy Inscore]
3. User selects: "Use SAFE BRD template"
4. ✓ Using SAFE BRD template
5. ✓ Template loaded (23,456 characters)
6. ✓ Detected proper template with placeholders
7. ✓ Template structure parsed: 15 sections, 28 placeholders
8. ✓ Processed template saved for reference
9. Template processing complete. Ready for BRD generation.

**Result Communicated:**
- 15 sections extracted (Title Page, Tracking, TOC, Introduction, Purpose, Scope, Assumptions, Business Requirements, Additional Context, Rules, NFRs, Ambiguities, Traceability, Source References, Appendix)
- ID Format: PRE_XXX
- 28 placeholders found

### Example 1b: SAFE Template Explicitly Requested

**User Request:** "Use the SAFE BRD template"

**Execution:**
1. ✓ Using SAFE BRD template
2. ✓ Template loaded (23,456 characters)
3. ✓ Detected proper template with placeholders
4. ✓ Template structure parsed: 15 sections, 28 placeholders
5. ✓ Processed template saved for reference
6. Template processing complete. Ready for BRD generation.

**Result Communicated:**
- 15 sections extracted (Title Page, Tracking, TOC, Introduction, Purpose, Scope, Assumptions, Business Requirements, Additional Context, Rules, NFRs, Ambiguities, Traceability, Source References, Appendix)
- ID Format: PRE_XXX
- 28 placeholders found

### Example 2: Custom DOCX Template

**User Request:** Custom template path provided: `./docs/org-template.docx`

**Execution:**
1. ✓ Loading custom template from ./docs/org-template.docx
2. ✓ Converting DOCX to markdown...
3. ✓ Template converted to markdown
4. ✓ Detected proper template with placeholders
5. ✓ Template structure parsed: 12 sections, 19 placeholders
6. ✓ Processed template saved for reference
7. Template processing complete. Ready for BRD generation.

**Result Communicated:**
- 12 custom sections (organization-specific structure)
- ID Format: SYS-XXX (detected from template)
- 19 placeholders found

### Example 3: Example Document (AI-Powered Auto-Conversion)

**User Request:** Example BRD path provided: `./examples/sample.docx`

**Execution:**
1. ✓ Loading custom template from ./examples/sample.docx
2. ✓ Converting DOCX to markdown...
3. ✓ Template converted to markdown
4. ⚠ Detected example document (will convert to template)
5. ✓ Converting example document to template with placeholders...
6. ✓ AI identified 47 instance-specific values across 8 categories
7. ✓ Successfully created 47 placeholders
8. ✓ Detected categories: project_name, person_name, date, metric, company, requirement_id, budget, role
9. ✓ Most frequent replacements:
   - "{project name}" replaced "MyApp Mobile Client" (12 times)
   - "{product owner}" replaced "Dr. Sarah Smith" (5 times)
   - "{release date}" replaced "Q1 2026" (4 times)
   - "{user capacity}" replaced "10,000 concurrent users" (3 times)
   - "{response time target}" replaced "500ms" (2 times)
10. ✓ Converted to template format with 47 placeholders across 8 categories
11. ✓ Template structure parsed: 14 sections, 47 placeholders
12. ✓ Processed template saved for reference
13. Template processing complete. Ready for BRD generation.

**Result Communicated:**
- 14 sections extracted from example
- 47 semantic placeholders created using AI analysis
- Handles any format: "MyApp" → "{project name}", "Q1 2026" → "{release date}", "Dr. Sarah Smith" → "{product owner}"
- ID Format: REQ_XXX (detected pattern: REQ001, REQ002...)
- Categories detected: project_name, person_name, date, metric, company, requirement_id, budget, role

## Best Practices

### For BRD Generators:
1. **Always parse template first** before content extraction
2. **Never assume section names** - use template structure
3. **Never assume section count** - it varies by template
4. **Never hardcode section numbers** - use template ordering
5. **Adapt to any template structure** - semantic matching over exact names

### For Custom Templates:
1. Use clear section headings (H1, H2, H3)
2. Include placeholders in `{curly braces}` format
3. Provide example tables with headers
4. Mark optional sections explicitly
5. Use consistent formatting throughout

### For Template Selection:
1. If no template specified in source documents, present 3 options:
   - Upload custom template (recommended)
   - Use SAFE BRD template (comprehensive default)
   - Use legacy Inscore template (backward compatibility)
2. If user explicitly specifies a template, use it directly
3. Validate template completeness (minimum sections present)
4. Warn if critical sections are missing

## AI-Powered vs Regex Approach

### Why AI-Powered Semantic Analysis?

**Traditional Regex Approach (v2.0):**
- Limited to predefined patterns
- Fails on unconventional formats
- Creates generic placeholders ({value}, {name})
- Requires constant updates for new formats
- Can't distinguish context (e.g., protocol vs document version)

**AI-Powered Approach (v2.1+):**
- Works with ANY format automatically
- Understands context and semantics
- Creates meaningful placeholders ({user capacity}, {product owner})
- Self-adapting - no updates needed for new formats
- Distinguishes between instance data and standards

### Comparison Examples

| Input Value | Regex Result (v2.0) | AI Result (v2.1+) |
|-------------|---------------------|-------------------|
| "MyApp Mobile Client" | ❌ Not converted (no "System" suffix) | ✅ `{project name}` |
| "Q1 2026" | ❌ Not converted (non-standard date) | ✅ `{release date}` |
| "Dr. Sarah Smith" | ❌ Not converted (has title) | ✅ `{product owner}` |
| "10,000 concurrent users" | ❌ Partial: "10,000 concurrent {value}" | ✅ `{user capacity}` |
| "500ms" | ❌ Not converted ("ms" not in pattern) | ✅ `{response time target}` |
| "REQ001" | ❌ Not converted (no separator) | ✅ `{requirement id}` |
| "99.99% uptime" | ❌ Not converted (four 9s) | ✅ `{uptime target}` |
| "TLS 1.3" (security protocol) | ⚠️ Would convert to "{version}" | ✅ Kept literal (protocol standard) |
| "Document Version 1.0" | ⚠️ Would convert entire phrase | ✅ Only "1.0" → `{version}` |

### Real-World Impact

**Example Document:**
```markdown
# MyApp Mobile Client
**Release:** Q1 2026
**Owner:** Dr. Sarah Smith

### Requirements
- REQ001: Support 10,000 concurrent users
- REQ002: Response time under 500ms
```

**Regex Output (v2.0):** Most values unchanged, unusable as template
**AI Output (v2.1+):** Fully converted with semantic placeholders

```markdown
# {project name}
**Release:** {release date}
**Owner:** {product owner}

### Requirements
- {requirement id}: Support {user capacity}
- {requirement id}: Response time under {response time target}
```

## Semantic Section Matching

When mapping content to template sections, use semantic analysis to handle variations:

- **"Introduction" = "Overview" = "Background" = "Context"**
- **"Business Requirements" = "Requirements" = "Functional Requirements"**
- **"Rules" = "Business Rules" = "Validation Rules" = "Logic"**
- **"Ambiguities" = "Clarifications" = "Open Questions" = "Issues"**
- **"Scope" = "Scope Definition" = "Boundaries"**
- **"NFRs" = "Non-Functional Requirements" = "Quality Attributes"**

## Template Maintenance

Templates should be updated if:
- Organization requirements change
- New compliance standards emerge
- Stakeholder feedback requires adjustments
- Project methodology changes (e.g., SAFe to Scrum)

**Note:** When templates change, the BRD generator automatically adapts - no code changes needed.

---

## Template Structure Object

After parsing, the skill returns a structured object with:

**`templateStructure`** contains:
- **`sections`**: Array of section objects, each with:
  - `number`: Section number (e.g., "1", "2.1") or null
  - `name`: Section title (e.g., "Business Requirements")
  - `level`: Heading level (1-4, where 1=H1, 2=H2, etc.)
  - `subsections`: Nested array of child sections
  - `placeholders`: Array of `{placeholder}` strings found in section
  - `required`: Boolean (true unless section name contains "optional")
  - `content`: Full section content from template
- **`totalSections`**: Total count of sections
- **`placeholders`**: Array of all unique placeholders found
- **`idFormat`**: Detected ID pattern (e.g., "PRE_XXX", "SEC_BR0X")
- **`templatePath`**: Path to template file used

**Consuming workflows (like ba-brd) use this to:**
1. Know which sections to generate in the BRD
2. Understand section hierarchy for proper nesting
3. Fill placeholders with actual values from documents
4. Follow the exact template structure and ordering

---

**Template Version:** 2.1 (AI-Powered Executable Skill)
**Last Updated:** 2026-02-09
**Compatible with:** AIDLC-business-analyst v1.7+

**Changes in v2.1:**
- **BREAKING CHANGE:** Replaced rigid regex patterns with AI-powered semantic analysis
- AI automatically detects instance-specific values in ANY format
- Creates semantic, context-aware placeholders (e.g., {user capacity}, {product owner})
- Handles unconventional formats: "MyApp", "Q1 2026", "Dr. Smith", "10,000 users", etc.
- No longer limited to predefined patterns - truly universal template conversion
- Added validation and quality checks for conversion results
- Improved reporting with category detection and replacement summaries

**Changes in v2.0:**
- Now executable skill with allowed-tools
- Handles template loading and conversion (DOCX → MD)
- Detects example documents vs proper templates
- Converts examples to templates with placeholder substitution
- Returns structured TemplateStructure object
- Saves processed templates for reference
