# Product Owner/Scrum Master Plugin - User Guide

**Your AI-powered Agile assistant for Rally**

Query work items, create stories, manage sprints, and track progress—all from Claude Code using natural language.

---

## Table of Contents

### Getting Started
- [Quick Start](#quick-start)
- [Installation](#installation)
  - [Step 1: Run Setup Wizard](#step-1-run-setup-wizard-2-minutes)
  - [Step 2: Get Your Rally API Key](#step-2-get-your-rally-api-key)
  - [Step 3: Test Your Setup](#step-3-test-your-setup)
- [Safety First](#safety-first-)
- [Basic Usage](#basic-usage)
  - [Natural Language Queries](#natural-language-queries)
  - [Creating Work Items](#creating-work-items)
  - [Managing Sprints](#managing-sprints)

### Configuration & Customization
- [Customizing Templates](#customizing-templates)
  - [Understanding the Template System](#understanding-the-template-system)
    - [Option 1: Use Default Templates](#option-1-use-default-templates-recommended-for-most-users)
    - [Option 2: Create Custom Templates](#option-2-create-custom-templates-for-specific-projects)
  - [Setting Up Custom Templates](#setting-up-custom-templates)
  - [Editing Custom Templates](#editing-custom-templates)
  - [Template Variables](#template-variables)
  - [Example: Custom Story Template](#example-custom-story-template)
  - [Switching Between Default and Custom](#switching-between-default-and-custom)
- [Customizing Rules and Settings](#customizing-rules-and-settings)
  - [Quick Customization Examples](#quick-customization-examples)
  - [Understanding Rules and Settings](#understanding-rules-and-settings)
  - [Default Estimation Algorithm](#default-estimation-algorithm)
  - [Customizing Estimation for Your Project](#customizing-estimation-for-your-project)
  - [Example Customizations](#example-customizations)
  - [Viewing Current Rules](#viewing-current-rules)
  - [Resetting to Defaults](#resetting-to-defaults)
- [Managing Team Members](#managing-team-members)
  - [Setting Up Your Team](#setting-up-your-team)
  - [Understanding Estimate Multipliers](#understanding-estimate-multipliers)
  - [Using Team Configuration](#using-team-configuration)
  - [Managing Pods](#managing-pods)

### Planning & Estimation
- [BRD to Planning](#brd-to-planning)
  - [End-to-End BRD Processing](#end-to-end-brd-processing)
  - [BRD Structure Recognition](#brd-structure-recognition)
- [TDDs to User Stories and Tasks](#tdds-to-user-stories-and-tasks)
  - [From TDD to Rally Work Items](#from-tdd-to-rally-work-items)
  - [Story Point Estimation](#story-point-estimation)
  - [Estimate Rationale](#estimate-rationale)
  - [Adjusted Estimates for Team Members](#adjusted-estimates-for-team-members)
  - [Task Generation from Stories](#task-generation-from-stories)

### Sprint Management
- [Updating Story Progress Mid-Sprint](#updating-story-progress-mid-sprint)
  - [Moving Stories Through Workflow](#moving-stories-through-workflow)
  - [Bulk Status Updates](#bulk-status-updates)
  - [Updating Story Details](#updating-story-details)
  - [Adding Notes and Comments](#adding-notes-and-comments)
  - [Checking Sprint Progress](#checking-sprint-progress)
- [Capacity Planning and Sprint Rebalancing](#capacity-planning-and-sprint-rebalancing)
  - [Capacity Analysis](#capacity-analysis)
  - [Assignment Recommendations](#assignment-recommendations)
  - [Sprint Planning](#sprint-planning)
  - [Sprint Rebalancing](#sprint-rebalancing)
  - [Future Sprint Planning](#future-sprint-planning)
  - [Handling Team Changes](#handling-team-changes)

### Advanced Topics
- [Advanced Features](#advanced-features)
  - [Smart Name Resolution](#smart-name-resolution)
  - [Stage Story Handling](#stage-story-handling)
  - [Hierarchy Validation](#hierarchy-validation)
  - [Estimate Validation](#estimate-validation)
  - [Attachment Handling](#attachment-handling)
  - [Clone and Template Stories](#clone-and-template-stories)
  - [Rally Field Customization](#rally-field-customization)

### Examples & Reference
- [Real-World Examples](#real-world-examples)
  - [Example 1: Sprint Planning Session](#example-1-sprint-planning-session)
  - [Example 2: Creating Stories from Technical Design](#example-2-creating-stories-from-technical-design)
  - [Example 3: Quick Status Check](#example-3-quick-status-check)
  - [Example 4: Breaking Down TDD Documents](#example-4-breaking-down-tdd-documents-into-rally)
  - [Example 5: Sprint Rebalancing](#example-5-sprint-rebalancing-with-team-velocity)
- [Safety Features](#safety-features)
  - [1. Preview Before Changes](#1-preview-before-changes)
  - [2. Automatic Backups](#2-automatic-backups)
  - [3. Backup Management](#3-backup-management)
  - [4. Restore from Backup](#4-restore-from-backup)
  - [5. Explicit Confirmation](#5-explicit-confirmation)
  - [Backup Best Practices](#backup-best-practices)
- [Troubleshooting](#troubleshooting)
  - [CLI Not Found](#cli-not-found)
  - [API Key Issues](#api-key-issues)
  - [Unicode Encoding Errors](#unicode-encoding-errors)
  - [Stories Not Found](#stories-not-found)
  - [Backup Restoration Failed](#backup-restoration-failed)

---

# Getting Started

## Quick Start

**Think of this plugin as:** Your personal Product Owner and Scrum Master that lives in your CLI, speaks your language, and keeps Rally in sync with your workflow.

**What you can do:**
- "Show me all stories in F12345"
- "Assign unassigned stories to jane"
- "Create a story under F12345 with 5 points"
- "Move all telemetry stories to next sprint"
- "Check estimates for F116904"

**Time to value:** 5 minutes from install to first Rally query

---

## Installation

### Step 1: Run Setup Wizard (2 minutes)

```bash
/Product Owner/Scrum Master Plugin:setup
```

The wizard will:
1. Install Rally CLI in shared Python venv (`~/.claude/venvs/ADLC/`)
2. Collect your Rally API key
3. Configure your default workspace and portfolio
4. Save configuration to `~/.claude/aig.json`

### Step 2: Get Your Rally API Key

1. Navigate to https://rally1.rallydev.com/#/api_key
2. Click "Create New API Key"
3. Copy the key (starts with `_`)

### Step 3: Test Your Setup

```
You: "Show me all stories in iteration 2026.PI1.Iteration1"
```

If you see Rally data, you're ready to go! 🎉

---

## Safety First ⚠️

**Important: The plugin never makes changes to Rally without your explicit approval.**

Before any CREATE, UPDATE, or DELETE operation:
1. ✅ **Preview** - See exactly what will change with tree structure
2. ✅ **Backup** - Automatic backup created for all UPDATE/DELETE operations
3. ✅ **Confirm** - You must explicitly approve before changes are made
4. ✅ **Restore** - Backups can be restored anytime if something goes wrong

See [Safety Features](#safety-features) for complete details on backups and restore procedures.

---

## Basic Usage

Just ask in plain English - no special syntax required:

| What You Want        | What You Say                                          |
| -------------------- | ----------------------------------------------------- |
| Stories in a feature | "Show me stories for F116904"                         |
| Iteration work items | "List all stories in iteration 2026.PI1.Iteration1"   |
| Unassigned work      | "Find unassigned stories in F116883"                  |
| Owner lookup         | "Who owns F117572?"                                   |
| Hierarchy view       | "Get the full hierarchy for E4570"                    |
| Estimate audit       | "Check estimates for F116904"                         |
| Sprint progress      | "What's the status of iteration 2026.PI1.Iteration2?" |
| Team workload        | "Show me all stories assigned to Chris"               |

### Creating Work Items

**Single story:**
```
You: "Create a story under F12345 called 'Implement login' with 5 points"
```

**Bulk creation:**
```
You: "Create stories for F12345 based on the TDD in ~/docs/login-tdd.md"
```

**With assignment:**
```
You: "Create a defect for the authentication bug, 3 points, assign to chris"
```

> **Note:** All operations show a preview and require your confirmation before creating/updating Rally items. See [Safety Features](#safety-features) for backup and restore options.

### Managing Sprints

**Assign work:**
```
You: "Assign all unassigned stories in F12345 to dhananjay"
```

**Move iterations:**
```
You: "Move all telemetry stories to iteration 2026.PI1.Iteration2"
```

**Check status:**
```
You: "Give me a status summary for F116904"
```

> **Safety reminder:** All updates create automatic backups and require confirmation. If you make a mistake, you can restore from backup - see [Safety Features](#safety-features).

---

# Configuration & Customization

## Customizing Templates

Product Owner/Scrum Master Plugin uses a template system to generate professional, HTML-formatted descriptions for all Rally work items. Templates support variable substitution and can be fully customized to match your organization's standards.

### Understanding the Template System

The plugin provides **two template options** to fit your workflow:

#### Option 1: Use Default Templates (Recommended for Most Users)

Default templates are built into the plugin and work out-of-the-box:
- **Epic** (`epic.md`) - Strategic portfolio items
- **Capability** (`capability.md`) - Portfolio items under epics
- **Feature** (`feature.md`) - Portfolio items under capabilities
- **Story** (`story.md`) - User stories with "As a/I want/So that" format
- **Task** (`task.md`) - Tasks under stories
- **Defect** (`defect.md`) - Bug reports
- **Administrative Tasks** (`administrative-tasks.md`) - Overhead work

These templates follow industry-standard formats and require no setup. Simply start using the plugin and Rally work items will be automatically formatted.

**When to use default templates:**
- Your team follows standard Agile/SAFe conventions
- You want zero-configuration setup
- Multiple projects share the same Rally formatting standards
- You're new to the plugin and want to start quickly

#### Option 2: Create Custom Templates for Specific Projects

For projects with unique requirements, you can create **project-specific custom templates** that override the defaults.

Custom templates live in `.claude/.fe-sm-templates/` within your project directory. The plugin checks this location first before falling back to defaults:

```
Priority: Custom templates > Default templates
```

**When to use custom templates:**
- Your organization requires specific compliance sections (SOX, HIPAA, GDPR)
- You need custom acceptance criteria formats (BDD Given/When/Then)
- Client contracts mandate particular documentation standards
- You want to add company-specific headers, footers, or legal disclaimers

### Setting Up Custom Templates

**Initial setup:**
```
You: "Customize the Rally templates for this project"
```

This command copies all default templates from the plugin to `.claude/.fe-sm-templates/` in your project directory, where you can edit them independently without affecting other projects.

**Result:**
```
✓ Copied 7 templates to .claude/.fe-sm-templates/
  - epic.md
  - capability.md
  - feature.md
  - story.md
  - task.md
  - defect.md
  - administrative-tasks.md
```

### Editing Custom Templates

**Modify a specific template:**
```
You: "Update the story template to include our company's acceptance criteria format"
```

**Preview before applying:**
```
You: "Show me what a story description will look like with the current template"
```

**Common customizations:**
- Adding company-specific headers or footers
- Including compliance requirements (security, privacy, accessibility)
- Adjusting user story format (e.g., BDD-style Given/When/Then)
- Adding custom sections for architecture decisions or technical debt

### Template Variables

Templates use `{{variable_name}}` syntax for dynamic content:

**Common variables:**
- `{{title}}`, `{{owner}}`, `{{points}}` - Basic work item fields
- `{{role}}`, `{{want}}`, `{{benefit}}` - User story components
- `{{epic_id}}`, `{{feature_id}}`, `{{story_id}}` - Hierarchy references
- `{{date}}` - Current date (YYYY-MM-DD format)

### Example: Custom Story Template

**Scenario:** Your organization requires security and accessibility sections for every story.

**Custom template** (`.claude/.fe-sm-templates/story.md`):
```html
<p><strong>As a</strong> {{role}},<br/>
<strong>I want</strong> {{want}},<br/>
<strong>So that</strong> {{benefit}}.</p>

<h3>Acceptance Criteria</h3>
<ul>
{{acceptance_criteria_html}}
</ul>

<h3>Security Considerations</h3>
<p>All implementations must follow OWASP guidelines and undergo security review before deployment.</p>

<h3>Accessibility Requirements</h3>
<p>Must meet WCAG 2.1 Level AA compliance. Test with screen readers and keyboard navigation.</p>

<hr/>
<p><small>© 2026 Accenture | Confidential and Proprietary</small></p>
```

**Usage:**
```
You: "Create a story under F116904 called 'Implement search filters' with 5 points"
```

The plugin automatically uses your custom story template, ensuring every story in this project includes security, accessibility, and copyright sections.

### Switching Between Default and Custom

**Check current template mode:**
```
You: "Are we using custom templates or defaults?"
```

**Revert to defaults (delete custom templates):**
```
You: "Remove custom templates and use the plugin defaults"
```

**Reset a single template to default:**
```
You: "Reset story.md to the default template"
```

---

## Customizing Rules and Settings

Beyond templates, the plugin uses a **rules file** (`rules.md`) and **settings file** (`settings.json`) to control business logic like story point calculation, validation rules, and workflow conventions.

### Quick Customization Examples

You can customize using **conversational prompts** (easier) or **manual file editing** (more control).

**Conversational prompts:**
```
"Change max story points from 7 to 13"

"Use 6 hours per point instead of 8"

"We use T-shirt sizing - map XS/S/M/L/XL to 1/3/5/8/13 points"

"Require all stories to have security and accessibility sections"

"For infrastructure projects, weight integration complexity higher than technical complexity"

"Show me the current story point calculation rules"

"Reset all rules to plugin defaults"
```

**Manual file paths:**
- **Rules**: `.claude/rally/rules.md` - Story point calculation, complexity factors, writing guidelines
- **Settings**: `.claude/rally/settings.json` - Max points, hours per point, validation rules
- **Defaults**: `plugins/AIDLC-scrum-master/.fe-sm-default/config/` - Plugin defaults to copy/compare

### Understanding Rules and Settings

The plugin provides intelligent defaults but allows full customization of:

**Story Point Estimation:**
- Complexity factors (technical, integration, risk)
- Fibonacci mapping thresholds
- Base hours per point (default: 8)
- Maximum story points (default: 7)

**Validation Rules:**
- Require acceptance criteria
- Require tasks per story
- Require owner assignment
- Require HTML descriptions

**Writing Conventions:**
- Story title format (impact-focused vs implementation-focused)
- Task description structure
- Administrative tasks workflow

**Rally Conventions:**
- Release naming format (e.g., `2026.PI1`)
- Iteration naming format (e.g., `2026.PI1.Iteration1`)
- Stage story prefixes

### Default Estimation Algorithm

The plugin uses a **three-factor complexity analysis** to auto-estimate story points:

**Complexity Factors (1-5 scale):**
1. **Technical Complexity**: Implementation difficulty, algorithms, new technology
2. **Integration Requirements**: Number of external systems, API complexity
3. **Risk/Uncertainty**: Requirements clarity, technical unknowns, team experience

**Formula:**
```
Sum of factors (3-15 range)
Complexity Score = (sum - 3) / 12 × 100  (normalizes to 0-100)
```

**Fibonacci Mapping:**
- **1 pt**: Score 0-15 (simple CRUD, config changes)
- **2-3 pts**: Score 16-45 (standard features, use 3 if risk ≥ 4)
- **5 pts**: Score 46-60 (complex algorithms, custom development)
- **8 pts**: Score 61-75 (very complex, architectural changes)
- **13 pts**: Score 76-100 ⚠️ **Split recommended**

**Example:**
```
OAuth Integration Story:
- Technical Complexity: 4 (complex, secure token handling)
- Integration Requirements: 3 (Google + GitHub providers)
- Risk/Uncertainty: 2 (team has OAuth experience)

Sum: 9
Complexity Score: (9-3)/12×100 = 50
Recommended Estimate: 5 points
```

### Customizing Estimation for Your Project

You have **two options** for customizing rules: conversational prompts or manual file editing.

#### Initial Setup (Creates Project-Specific Rules)

**Option 1 - Conversational:**
```
You: "Customize the Rally project rules for this codebase"

You: "I want to override the default estimation rules for this project"
```

**Option 2 - Manual:**
```bash
# Copy default rules to your project
cp plugins/AIDLC-scrum-master/.fe-sm-default/config/rules.md .claude/rally/
cp plugins/AIDLC-scrum-master/.fe-sm-default/config/settings.json .claude/rally/

# Edit files directly
code .claude/rally/rules.md
code .claude/rally/settings.json
```

Both approaches copy `rules.md` and `settings.json` from the plugin to `.claude/rally/` in your project directory.

#### Modify Story Point Thresholds

**Conversational:**
```
You: "Change the 5-point threshold to complexity scores 40-60 instead of 46-60"

You: "I want stories between 50-70 complexity to be 8 points, not 5 points"

You: "Adjust the Fibonacci mapping so 13-point stories start at complexity 85 instead of 76"
```

**Manual (edit `.claude/rally/rules.md`):**
```markdown
**Fibonacci Mapping:**
- **1 pt**: Score 0-15 (simple)
- **2-3 pts**: Score 16-45 (use 3 if risk ≥ 4, else 2)
- **5 pts**: Score 40-60 (complex) ← Changed from 46-60
- **8 pts**: Score 61-75 (very complex)
- **13 pts**: Score 76-100 ⚠️ **Split recommended**
```

#### Change Base Hours Per Point

**Conversational:**
```
You: "Our team uses 6 hours per point, not 8. Update the settings"

You: "Change base_hours_per_point to 10 in our Rally settings"
```

**Manual (edit `.claude/rally/settings.json`):**
```json
{
  "story_points": {
    "calculation_method": "skill_based",
    "max_points": 7,
    "base_hours_per_point": 6
  }
}
```

#### Adjust Max Story Points

**Conversational:**
```
You: "Increase the max story points from 7 to 13"

You: "We allow stories up to 21 points. Update the validation rules"

You: "Set max_story_points to 8"
```

**Manual (edit `.claude/rally/settings.json`):**
```json
{
  "validation_rules": {
    "require_acceptance_criteria": true,
    "require_tasks_per_story": true,
    "require_owner_assignment": true,
    "max_story_points": 13
  }
}
```

#### Modify Complexity Factor Weights

**Conversational:**
```
You: "For our infrastructure team, integration complexity should be weighted more heavily than technical complexity"

You: "Change the risk/uncertainty factor to be less important in our story estimates"
```

**Manual (edit `.claude/rally/rules.md`):**
```markdown
#### Complexity Factors (Adjusted for Infrastructure)

**1. Technical Complexity (30% weight)**
- 1: Simple CRUD, configuration changes
- 3: Standard infrastructure patterns
- 5: Custom networking, security architecture

**2. Integration Requirements (50% weight)**  ← Increased from 33%
- 1: Self-contained
- 3: 2-3 external systems
- 5: 5+ systems with orchestration

**3. Risk/Uncertainty (20% weight)**  ← Decreased from 33%
- 1: All requirements clear
- 3: Some unknowns
- 5: Major uncertainty
```

#### Update Validation Rules

**Conversational:**
```
You: "Require all stories to have acceptance criteria and at least 2 tasks"

You: "Turn off the requirement for owner assignment when creating stories"

You: "Add a validation rule that all stories must have security considerations"
```

**Manual (edit `.claude/rally/settings.json`):**
```json
{
  "validation_rules": {
    "require_acceptance_criteria": true,
    "require_tasks_per_story": true,
    "min_tasks_per_story": 2,
    "require_owner_assignment": false,
    "require_security_section": true,
    "max_story_points": 7
  }
}
```

### Example Customizations

#### Scenario 1: Your team uses T-shirt sizing

Instead of Fibonacci (1, 2, 3, 5, 8, 13), you want Small, Medium, Large mapped to points.

**Approach A - Conversational:**
```
You: "We use T-shirt sizing instead of Fibonacci. Map it like this:
- 1 point = XS (simple CRUD, score 0-20)
- 3 points = Small (standard feature, score 21-40)
- 5 points = Medium (moderate complexity, score 41-60)
- 8 points = Large (high complexity, score 61-80)
- 13 points = XL (needs splitting, score 81-100)

Update our rules.md file"
```

**Approach B - Manual (edit `.claude/rally/rules.md`):**
```markdown
## Story Point Estimation

### T-Shirt Sizing Mapping

We use T-shirt sizes instead of raw Fibonacci numbers:

- **1 pt (XS)**: Score 0-20 - Simple CRUD, configuration changes
- **3 pts (S)**: Score 21-40 - Standard feature with known patterns
- **5 pts (M)**: Score 41-60 - Moderate complexity, custom logic
- **8 pts (L)**: Score 61-80 - High complexity, significant development
- **13 pts (XL)**: Score 81-100 - Split into smaller stories

When communicating estimates, use T-shirt sizes (e.g., "This is a Medium story").
```

**Result:** All auto-estimated stories will use T-shirt size terminology

---

#### Scenario 2: Your organization requires compliance sections

Your security team requires all stories to include security and privacy sections.

**Approach A - Conversational:**
```
You: "Add validation rules requiring security and privacy sections for all stories. Also set max story points to 8"

You: "Update settings.json to enforce these requirements:
- All stories must have acceptance criteria
- All stories must have a security section
- All stories must have a privacy section
- Maximum 8 points per story"
```

**Approach B - Manual:**

**Edit `.claude/rally/settings.json`:**
```json
{
  "validation_rules": {
    "require_acceptance_criteria": true,
    "require_tasks_per_story": true,
    "require_owner_assignment": true,
    "require_security_section": true,
    "require_privacy_section": true,
    "max_story_points": 8
  }
}
```

**Edit `.claude/.fe-sm-templates/story.md`:**
```html
<p><strong>As a</strong> {{role}},<br/>
<strong>I want</strong> {{want}},<br/>
<strong>So that</strong> {{benefit}}.</p>

<h3>Acceptance Criteria</h3>
<ul>
{{acceptance_criteria_html}}
</ul>

<h3>Security Considerations</h3>
<p>Document security implications, authentication requirements, and data protection measures.</p>

<h3>Privacy Considerations</h3>
<p>Document PII handling, data retention policies, and GDPR/compliance requirements.</p>
```

**Result:** All stories automatically include security and privacy sections, validation enforces completion

---

#### Scenario 3: Adjusted complexity factors for infrastructure teams

Your team works on infrastructure (less UI, more integration), so integration complexity matters more.

**Approach A - Conversational:**
```
You: "Our infrastructure team needs different complexity weights. Adjust the estimation rules:
- Technical Complexity: 30% weight (less important, mostly standard patterns)
- Integration Requirements: 50% weight (very important, lots of system integrations)
- Risk/Uncertainty: 20% weight (less important, we have good docs)

Update the rules to reflect this"

You: "For integration requirements, change the scale:
- Level 1: Self-contained
- Level 3: 2-3 systems with standard APIs
- Level 5: 5+ systems with complex orchestration and event streaming"
```

**Approach B - Manual (edit `.claude/rally/rules.md`):**
```markdown
## Story Point Estimation (Infrastructure Team)

### Complexity Factors - Weighted for Infrastructure Work

**1. Technical Complexity (30% weight)**
Infrastructure work typically follows standard patterns, so technical complexity is less variable.

- 1: Configuration changes, parameter tuning
- 2: Standard infrastructure patterns (VPC, security groups)
- 3: Custom networking setup, load balancer configuration
- 4: Custom security architecture, multi-region setup
- 5: Novel architecture, cutting-edge technologies

**2. Integration Requirements (50% weight)** ← Primary driver for infrastructure
Infrastructure projects heavily depend on system integration complexity.

- 1: Self-contained, no external dependencies
- 2: Single external system with standard API
- 3: 2-3 systems with standard APIs (AWS SDK, Terraform)
- 4: 4-5 systems, complex orchestration, bi-directional sync
- 5: 5+ systems, real-time event streaming, legacy system integration

**3. Risk/Uncertainty (20% weight)** ← Less variable with good documentation
Well-documented infrastructure work has lower uncertainty.

- 1: Well-documented approach, team has experience
- 2: Standard approach with 1-2 minor questions
- 3: Some research needed, incomplete vendor docs
- 4: Limited documentation, unclear dependencies
- 5: Major unknowns, recommend spike story first

### Calculation Process (Unchanged)
Formula: (sum - 3) / 12 × 100
Fibonacci Mapping: Same as default (1, 2-3, 5, 8, 13)
```

**Result:** Estimation algorithm prioritizes integration complexity over technical complexity, reflecting infrastructure-heavy workloads

### Viewing Current Rules

**Approach A - Conversational:**
```
You: "Show me the current story point estimation rules"

You: "What's our max story points setting?"

You: "Are we using default rules or custom rules for this project?"

You: "Explain how story points are calculated for this project"

You: "What are the complexity thresholds for 5-point stories?"

You: "Show me the validation rules we have enabled"

You: "What's our base hours per point setting?"
```

**Approach B - Manual:**
```bash
# View current rules
cat .claude/rally/rules.md

# View current settings
cat .claude/rally/settings.json

# Check if using defaults (files don't exist = using defaults)
ls -la .claude/rally/
```

**Compare with defaults (Conversational):**
```
You: "How do our custom rules differ from the plugin defaults?"

You: "Show me what changed in our estimation rules"
```

**Compare with defaults (Manual):**
```bash
# Compare custom rules with plugin defaults
diff .claude/rally/rules.md plugins/AIDLC-scrum-master/.fe-sm-default/config/rules.md

diff .claude/rally/settings.json plugins/AIDLC-scrum-master/.fe-sm-default/config/settings.json
```

### Resetting to Defaults

**Approach A - Conversational:**
```
You: "Reset our Rally rules to the plugin defaults"

You: "Remove the custom rules and use the standard estimation algorithm"

You: "Delete .claude/rally/rules.md and fall back to defaults"
```

**Approach B - Manual:**
```bash
# Remove all custom rules (fall back to defaults)
rm .claude/rally/rules.md
rm .claude/rally/settings.json

# Or move to backup instead of deleting
mv .claude/rally/rules.md .claude/rally/rules.md.backup
mv .claude/rally/settings.json .claude/rally/settings.json.backup
```

Both approaches remove custom rules and fall back to the standard three-factor complexity analysis.

**Reset specific settings (Conversational):**
```
You: "Reset just the story point thresholds to defaults, keep our other customizations"

You: "Restore the default max_story_points but keep our custom validation rules"
```

**Reset specific settings (Manual):**
```bash
# Copy just the sections you want to reset
# Example: Reset validation rules, keep custom estimation thresholds
# Edit .claude/rally/settings.json and replace validation_rules section with defaults
```

---

## Managing Team Members

Team member configuration enables accurate capacity planning, story assignment by name, and velocity-adjusted estimates. The team roster is stored in `.claude/rally/team.json` within your project.

### Setting Up Your Team

**Initial team configuration:**
```
You: "Set up the team roster for Rally"
```

**Add a team member:**
```
You: "Add sarah.johnson to the team with rally username sarah.johnson@accenture.com"
```

**Update team member details:**
```
You: "Update Chris's estimate multiplier to 0.7 because he's ramping down on this project"
```

### Understanding Estimate Multipliers

The `estimate_multiplier` field adjusts story point estimates based on individual capacity and experience:

- **< 1.0** (e.g., 0.6): Part-time allocation, senior developers who work faster
- **1.0**: Standard velocity, baseline team member
- **> 1.0** (e.g., 1.5): Junior developers ramping up, learning new technology

**Example team configuration:**
```json
{
  "members": [
    {
      "name": "Chris",
      "rally_username": "chris.le@accenture.com",
      "role": "Senior Developer",
      "estimate_multiplier": 0.6,
      "skills": ["Python", "AWS", "Angular"],
      "notes": "Part-time on this project, 60% allocation",
      "pods": ["POD 1"]
    },
    {
      "name": "Mano",
      "rally_username": "mano.nanda@accenture.com",
      "role": "Junior Developer",
      "estimate_multiplier": 1.5,
      "skills": ["JavaScript", "Node.js"],
      "notes": "Ramping up, may need mentoring",
      "pods": ["POD 2"]
    },
    {
      "name": "Mike",
      "rally_username": "mike.p@accenture.com",
      "role": "Project Manager",
      "location": "East Coast",
      "pods": ["POD 1", "POD 2"]
    }
  ]
}
```

**Team member fields:**

| Field                 | Required | Description                                                 |
| --------------------- | -------- | ----------------------------------------------------------- |
| `name`                | Yes      | Display name for assignments and reports                    |
| `rally_username`      | Yes      | Rally email address for API lookups                         |
| `role`                | No       | Job title (used for assignment recommendations)             |
| `estimate_multiplier` | No       | Velocity adjustment factor (default: 1.0)                   |
| `location`            | No       | Geographic location                                         |
| `skills`              | No       | Technical skills list (used for assignment recommendations) |
| `notes`               | No       | Additional context (allocation %, ramp-up status, etc.)     |
| `pods`                | No       | Pod/sub-team memberships for filtering and reporting        |

### Using Team Configuration

**Assign by name (instead of email):**
```
You: "Assign US12345 to Chris"
```

**Capacity-adjusted estimates:**
```
You: "Estimate this story for Mano"
```

When auto-estimating stories, the system calculates both the base estimate and the adjusted estimate for specific team members, showing effective hours accounting for their multiplier.

**View team capacity:**
```
You: "Show me the team capacity for iteration 2026.PI1.Iteration3"
```

### Managing Pods

Pods (sub-teams) allow you to organize team members into groups for filtering and capacity reporting.

**Query team by pod:**
```
You: "Show me all team members in POD 1"
You: "What pods do we have?"
```

**Update a member's pod assignment:**
```
You: "Add Chris to POD 2"
You: "Update Mano's pods to POD 1 and POD 2"
```

Members can belong to multiple pods. Pod names are case-insensitive for filtering.

---

# Planning & Estimation

## BRD to Planning

Transform Business Requirements Documents (BRDs) into executable Rally work items with automatic feature decomposition, story creation, and estimate generation.

> **Safety reminder:** All work item creation shows a preview hierarchy and requires your confirmation before creating in Rally. See [Safety Features](#safety-features).

### End-to-End BRD Processing

**Analyze a BRD and create work items:**
```
You: "I have a BRD at ~/Documents/customer-portal-requirements.docx. Create an epic and features in Rally under portfolio item I4570"
```

The plugin will:
1. Read and parse the BRD document
2. Identify major feature areas and capabilities
3. Propose an epic/capability/feature hierarchy
4. Generate user stories from requirements
5. Auto-estimate story points
6. Create all work items in Rally with preview

**Interactive refinement:**
```
You: "I have a BRD for the new payment gateway. Help me plan this in Rally under Epic E4570"

Claude: [After reading BRD]
I found 3 major feature areas:
1. Credit Card Processing (PCI compliance, tokenization)
2. Payment Method Management (save cards, select default)
3. Transaction History (view past payments, download receipts)

Shall I create these as 3 separate features under E4570?

You: Actually, split Credit Card Processing into two features - one for processing, one for PCI compliance

Claude: Understood. Updated structure:
Capability: Payment Gateway
├─ Feature: Card Payment Processing
├─ Feature: PCI Compliance & Security
├─ Feature: Payment Method Management
└─ Feature: Transaction History

Would you like me to create these in Rally?
```

### BRD Structure Recognition

The plugin recognizes common BRD formats and extracts:
- **Functional Requirements** → User Stories
- **Business Rules** → Acceptance Criteria
- **System Capabilities** → Features
- **Strategic Objectives** → Epics/Capabilities
- **Technical Constraints** → Task-level details

**Example prompts:**
```
"Extract user stories from the BRD section titled 'User Management Requirements'"

"Create features based on the capability breakdown in chapter 3 of the BRD"

"Generate acceptance criteria from the business rules table in the requirements doc"
```

---

## TDDs to User Stories and Tasks

Technical Design Documents (TDDs) provide the foundation for creating detailed user stories with accurate estimates. The plugin analyzes technical complexity to generate realistic story points and break down implementation tasks.

> **Safety reminder:** Story and task creation operations show a complete preview with estimates and require confirmation. See [Safety Features](#safety-features).

### From TDD to Rally Work Items

**Create stories from a TDD:**
```
You: "I have a UI TDD at ./docs/search-component-tdd.md. Create user stories under Feature F117571"
```

**Create stories from multiple TDDs:**
```
You: "Analyze the TDDs in ./docs/booking-flow/ and create stories under F892402"
```

The plugin reads the TDD, identifies implementation layers (UI, API, Lambda, Database), and generates corresponding stories with appropriate estimates.

### Story Point Estimation

Story points are calculated using a complexity-based algorithm that analyzes:

1. **Technical Complexity (1-5)**: Implementation difficulty, algorithm complexity, technology familiarity
2. **Integration Requirements (1-5)**: Number of external systems, API complexity, data transformations
3. **Risk/Uncertainty (1-5)**: Requirements clarity, technical unknowns, dependency risks

**Complexity Score Formula:**
```
complexity_score = (technical + integration + risk) / 15 * 100
```

**Story Points Mapping:**
| Complexity Score | Story Points |
| ---------------- | ------------ |
| 0-20             | 1-2 points   |
| 21-33            | 3 points     |
| 34-53            | 5 points     |
| 54-73            | 8 points     |
| 74-100           | 13 points    |

### Estimate Rationale

Every auto-estimated story includes a detailed rationale explaining the estimate:

```
You: "Auto-estimate the stories in F117571 and include rationale"
```

**Example output:**
```
📊 Auto-estimated 'Implement OAuth2 authentication': 8 points (score: 60/100)

Complexity Factors:
  Technical Complexity:      4/5
  Integration Requirements:  4/5
  Risk/Uncertainty:          2/5

  Sum: 10
  Complexity Score: 67/100

Recommended Estimate: 8 points

📝 Rationale:
  Key Drivers:
    • High technical complexity requiring OAuth2 flow implementation
    • Integration with external identity providers (Google, GitHub)
    • Secure token storage and refresh logic required

  Assumptions:
    • Team has OAuth2 experience from previous projects
    • Requirements are well-defined with clear acceptance criteria

  Recommendation: Accept
    ✓ Story is appropriately sized (8 points ≤ 8 point threshold)
```

The rationale is stored in the Rally story's Notes field for transparency and audit purposes.

### Adjusted Estimates for Team Members

When estimating for specific team members, the multiplier is applied:

```
You: "Estimate this OAuth story for Mano"

📊 Story Point Estimation

Recommended Estimate: 8 points

Team Member: Mano
  Adjusted Estimate: 12.0 points (advisory)
  Task Hours: 96 hours (8 × 8 × 1.5)
```

**Note:** Adjusted estimates are advisory. The story is recorded with the base estimate in Rally, but capacity planning accounts for the adjusted value.

### Task Generation from Stories

**Break down a story into tasks:**
```
You: "Create implementation tasks for US773876 based on the Lambda TDD"
```

**Automatic task generation with hour estimates:**
```
You: "Generate tasks for the OAuth story with estimates"
```

Tasks are estimated in hours using the formula:
```
task_hours = story_points × 8 hours_per_point × team_member_multiplier
```

For a 5-point story assigned to a team member with 0.6 multiplier:
```
task_hours = 5 × 8 × 0.6 = 24 hours
```

---

# Sprint Management

## Updating Story Progress Mid-Sprint

Track and update story status throughout the sprint using natural language commands.

> **Safety guarantee:** All updates require your confirmation and create automatic backups before modifying Rally. You can restore from backup anytime - see [Safety Features](#safety-features).

### Moving Stories Through Workflow

**Update story state:**
```
You: "Move US773876 to In-Progress"

You: "Mark US773874 as completed"

You: "Set US773875 back to Defined"
```

Valid schedule states:
- **Defined**: Story is ready for development
- **In-Progress**: Work has started
- **Completed**: Work is done, ready for acceptance
- **Accepted**: Product Owner has accepted the story

### Bulk Status Updates

**Update multiple stories:**
```
You: "Mark all stories assigned to Chris in F116904 as In-Progress"

You: "Set all completed stories in iteration 2026.PI1.Iteration2 to Accepted"
```

### Updating Story Details

**Change story points:**
```
You: "Update US773876 to 8 points, we underestimated the complexity"
```

**Reassign owner:**
```
You: "Reassign US773876 from Chris to Jane because Chris is out sick"
```

**Update multiple fields:**
```
You: "Update US773876: set to 8 points, assign to Jane, move to In-Progress"
```

### Adding Notes and Comments

**Add implementation notes:**
```
You: "Add a note to US773876 that we're using the Google OAuth library instead of implementing from scratch"
```

**Track blockers:**
```
You: "Add a blocker note to US773875: waiting for API key from security team"
```

### Checking Sprint Progress

**Daily standup status:**
```
You: "Show me what stories are In-Progress in iteration 2026.PI1.Iteration2"

You: "What stories are assigned to Chris and not yet started?"
```

**Feature progress:**
```
You: "Give me a progress summary for F116904"
```

**Team member workload:**
```
You: "Show me all stories assigned to Jane in the current iteration"
```

---

## Capacity Planning and Sprint Rebalancing

Use team velocity data and estimate multipliers to perform accurate capacity planning and rebalance sprints when priorities change.

> **Safety guarantee:** Sprint rebalancing operations (moving stories between iterations) show a complete preview and require confirmation. Automatic backups ensure you can rollback changes - see [Safety Features](#safety-features).

### Capacity Analysis

**Calculate sprint capacity with ASCII progress bars:**
```
You: "What's our team capacity for iteration 2026.PI1.Iteration3?"
```

Output shows utilization with visual progress bars:
```
Sprint Capacity: 2026.PI1.Iteration3
======================================================================

Team Overall:  [################------------------------]  40.0%
  Assigned: 26.0 / 64.4 effective points  (Available: 38.4)

----------------------------------------------------------------------
  Chris           [#########################---------------]  64.1%
    Effective: 7.8 pts (nominal 13.0 x 0.6)  Role: Senior Developer
    Pods: POD 1

  Jane            [##########------------------------------]  25.4%
    Effective: 25.2 pts (nominal 21.0 x 1.2)  Role: Mid-Level Engineer

  Dhananjay       [###################---------------------]  47.6%
    Effective: 21.0 pts (nominal 21.0 x 1.0)  Role: Developer

  Senthil         [########--------------------------------]  19.2%
    Effective: 10.4 pts (nominal 13.0 x 0.8)  Role: Junior Developer
    Pods: POD 2
```

**Over-allocation warnings:**
When a team member has more assigned points than their effective capacity, the output includes warnings:
```
----------------------------------------------------------------------
WARNINGS:
  ! Chris is over-allocated: 10.0 points assigned vs 7.8 effective capacity
```

The progress bar uses plain ASCII characters (`#`, `-`, `!`) for cross-platform compatibility with Windows Git Bash.

### Assignment Recommendations

**Get AI-powered assignment recommendations for unassigned stories:**
```
You: "Recommend assignments for unassigned stories in iteration 2026.PI1.Iteration3"
```

The system analyzes team skills, roles, and remaining capacity to suggest optimal assignments:
```
Assignment Recommendations: 2026.PI1.Iteration3
======================================================================

Recommendations (3 stories):
----------------------------------------------------------------------
  [+++] US12345: Implement Python AWS Lambda function (5 pts)
    -> Chris | skill match (100%), good capacity (2.8 pts remaining), role fit (Senior Developer)
    Alternatives: Senthil, Jane

  [++ ] US12346: Build Node.js API endpoint (3 pts)
    -> Mano | skill match (67%), good capacity (6.5 pts remaining)
    Alternatives: Kevin, Chris

  [+  ] US12347: Update documentation (2 pts)
    -> Jane | available capacity
    Alternatives: Dhananjay, Senthil

Capacity Utilization:
----------------------------------------------------------------------
  Chris           [##############################----------]  75.0%  (5.9/7.8 pts)
  Mano            [####------------------------------------]  10.0%  (2.0/19.5 pts)
  Jane            [########--------------------------------]  20.0%  (5.0/25.2 pts)
```

**Confidence indicators:**
- `[+++]` **High** - Strong skill match and good capacity fit
- `[++ ]` **Medium** - Partial skill match or moderate capacity
- `[+  ]` **Low** - No skill match, assigned based on available capacity

**Scope to a specific feature:**
```
You: "Recommend assignments for unassigned stories in F12345 for iteration 2026.PI1.Iteration3"
```

**How scoring works:**

The recommendation engine scores each candidate member using a weighted formula:
- **Skill match (50%)**: Member's skills matched against story title and description
- **Capacity fit (30%)**: How much remaining capacity the member has
- **Role relevance (20%)**: Role suitability (e.g., PMs score low for development stories)

Stories are processed largest-first for optimal bin-packing, and capacity is tracked as recommendations are made.

### Sprint Planning

**Plan work for upcoming sprint:**
```
You: "We have 65 effective points of capacity in Iteration3. What features should we pull in?"
```

**Check if sprint is overallocated:**
```
You: "Is iteration 2026.PI1.Iteration2 overloaded?"

Output:
Current: 78 nominal points = 73.8 effective points
Capacity: 68 nominal points = 64.4 effective points
⚠️ Overallocated by 14.6% (9.4 effective points)

Recommendation: Move 10-13 points of work to next iteration
```

### Sprint Rebalancing

**Responding to priority changes:**
```
You: "Leadership wants F116904 done in Iteration2 instead of Iteration3. Help me rebalance"

Claude: [Analyzes current sprint allocations]
F116904 has 48 points remaining across 6 stories.
Iteration2 currently has 68 points (101% capacity).

To fit F116904 in Iteration2 while staying under 110% capacity, I recommend:
- Move F117572 (21 points) to Iteration3
- Keep F117399 core stories (21 points) in Iteration2
- Defer F117399 reporting stories (8 points) to Iteration3

New Iteration2: 48 (F116904) + 21 (F117399 core) = 69 points (107% capacity)

Would you like me to make these changes in Rally?
```

**Move specific work between sprints:**
```
You: "Move all F117572 stories from Iteration2 to Iteration3"

You: "Swap F116904 and F119972 between Iteration2 and Iteration3"
```

### Future Sprint Planning

**Look ahead multiple sprints:**
```
You: "Show me the planned work for Iterations 2-4"

You: "Balance the workload across Iterations 2, 3, and 4 to avoid overallocation"
```

**What-if analysis:**
```
You: "If we add F120001 (34 points) to Iteration3, will we be overallocated?"
```

### Handling Team Changes

**Adjust for team member absence:**
```
You: "Chris is out for 2 weeks starting Iteration3. Rebalance his 13 points to the rest of the team"
```

**Adding team capacity:**
```
You: "Sarah is joining the team in Iteration4. Add her to team.json with 1.0 multiplier and 21 point capacity"
```

---

# Advanced Topics

## Advanced Features

### Smart Name Resolution

Assign stories using team member first names instead of full Rally usernames:

```
You: "Assign US773876 to Chris"
```

The plugin looks up "Chris" in `.claude/rally/team.json` and resolves to `chris.le@accenture.com` for Rally.

### Stage Story Handling

Administrative Tasks stories (ceremony overhead, meetings) are excluded from estimate validation:

```
You: "Validate estimates for F116904"
```

Stories with names starting with "Administrative Tasks" or "Stage" are automatically skipped, as they represent non-development overhead.

### Hierarchy Validation

All story creation automatically validates the complete parent hierarchy:

```
Story → Feature → Capability → Initiative
```

If any level is missing, the creation fails with a clear error message indicating which parent level is incomplete.

### Estimate Validation

**Validate feature estimates:**
```
You: "Audit all story estimates in F116904"

Output:
✓ US773874: 8 points (valid)
✓ US773875: 5 points (valid)
⚠️ US773876: 13 points (warning: consider splitting)
✗ US773877: 0 points (missing estimate)
```

**Fix estimate issues:**
```
You: "Fix the estimate issues in F116904"
```

### Attachment Handling

**Attach design documents to stories:**
```
You: "Attach the file ./docs/oauth-design.pdf to US773876"
```

**Attach multiple files:**
```
You: "Attach all files in ./docs/api-specs/ to F117571"
```

### Clone and Template Stories

**Clone an existing story:**
```
You: "Clone US773876 as a template for implementing GitHub OAuth"
```

**Create similar stories:**
```
You: "Create 3 stories like US773876 for Google, GitHub, and Microsoft OAuth"
```

### Rally Field Customization

**Configure validation rules in `.claude/rally/settings.json`:**
```json
{
  "validation_rules": {
    "require_acceptance_criteria": true,
    "require_tasks_per_story": true,
    "require_owner_assignment": true,
    "max_story_points": 8
  }
}
```

**Customize for your workflow:**
```
You: "Update settings to require acceptance criteria and limit stories to 8 points"
```

---


# Examples & Reference

## Real-World Examples

> **Note:** These examples are derived from actual conversations during plugin development sprints by the AI COE team. Feature IDs, story numbers, and team member names reflect genuine Rally data.

### Example 1: Sprint Planning Session

**Context:** Starting a new sprint, need to see what's assigned to the team.

**Conversation:**

```
You: Show me all stories in iteration 2026.PI1.Iteration1 assigned to our team

Claude: Let me query Rally for stories in that iteration.
[Executes Rally query]

Stories in 2026.PI1.Iteration1 (12 total, 89 points)

By Owner:
• chris.le: 3 stories, 21 points
  - US773874: Configuration module for telemetry (8 pts) - In-Progress
  - US773875: User identity detection (5 pts) - Defined
  - US773876: GAME API telemetry hook (8 pts) - Defined

• jane.smith: 4 stories, 34 points
  - US774967: AIDLC-code optimization (13 pts) - In-Progress
  - US774968: AIDLC-docs optimization (8 pts) - Completed
  [...]

• Unassigned: 5 stories, 34 points

You: Assign the unassigned stories to dhananjay.agrawat

Claude: I'll assign these 5 stories to Dhananjay Agrawat. Let me preview the changes first.

Preview: Rally Changes
══════════════════════════════════════════
UPDATE Story US782974 "Ask user how they want nested components"
  └─ Owner: None → Dhananjay Agrawat

UPDATE Story US782975 "Create TypeScript interfaces from JSON"
  └─ Owner: None → Dhananjay Agrawat

[3 more stories...]

Total: 5 stories to update
Backup: 5 stories backed up
══════════════════════════════════════════

You: go

Claude: ✅ Successfully updated 5 stories
Backup saved: .claude/rally/backups/20260122_143052_UPDATE_bulk_assignment.json
```

**Key Benefits:**
- View entire sprint at a glance
- Bulk assignment with preview
- Automatic backup before changes

---

### Example 2: Creating Stories from Technical Design

**Context:** Working on F117571 (User Feedback & Live Testing), need to fix how AIDLC-axis generates Angular components.

**Conversation:**

```
You: need to create a story under F117571. I have 2 files - one showing what axis-ui-generator
currently produces (wrong), one showing what we manually created after our meeting (correct).
Can you analyze them and draft a story?

Claude: Let me get the feature details and read both files.
[Queries Rally for F117571]
[Reads wrong-example.component.ts and correct-example.component.ts]

Found Feature F117571: "User Feedback & Live Testing" (Owner: christopher l)

I see the issue:

Current (Wrong):
• Generates full HTML with hardcoded form fields
• Each input, dropdown, datepicker explicitly coded
• 200+ lines of boilerplate per form

Expected (Correct):
• Uses <axis-ui-generator [uiStructure]="uiStructure" [uiData]="uiData">
• Form fields defined in JSON config file
• Component is minimal with just event handlers
• Dynamic rendering from JSON structure

This defeats the purpose of the AUG component. Should I create a story to fix axis-ui-generator?

You: yes. 3 points, assign to vensan, attach both files as expected-example-output.zip

Claude: I'll create the story with those acceptance criteria. Let me preview:

Preview: Rally Changes
══════════════════════════════════════════
Feature F117571: User Feedback & Live Testing

  CREATE Story (3 points)
    ├─ Title: "Generate minimal Angular components using axis-ui-generator
    │   AUG component with JSON configuration instead of hardcoded fields"
    ├─ Assigned to: Vensan Cabardo
    └─ Description:

        As a developer using AIDLC-axis plugin,
        I want axis-ui-generator to produce minimal components using
        <axis-ui-generator> with JSON configuration,
        So that I leverage dynamic UI rendering instead of manual field coding.

        Expected Output:
        1. AUG JSON file ({component}.form-structure.ts)
        2. Minimal TypeScript component with uiStructure, uiData
        3. HTML using <axis-ui-generator [uiStructure]="uiStructure"
           [uiData]="uiData" (uiEmitUpdate)="onFieldChange($event)">
        4. POM mapping for backend data columns

        Acceptance Criteria:
        • axis-ui-generator produces AUG JSON structure
        • Uses <axis-ui-generator> instead of individual fields
        • Accepts POM model for field-to-backend mapping
        • axis-router routes to dynamic-ui-generator correctly
        • Generated code passes axis-code-validator

        Attachments:
        • expected-example-output.zip (wrong vs correct examples)

Total: 1 story (3 points)
══════════════════════════════════════════

You: ok create it

Claude: ✅ Created story in Rally:
• US777742: Generate minimal Angular components using axis-ui-generator AUG
  component with JSON configuration instead of hardcoded fields
• Assigned to: Vensan Cabardo
• Feature: F117571 (User Feedback & Live Testing)
• Iteration: 2025/2026IterationIP
• Attachment: expected-example-output.zip

Story created with before/after examples attached.
```

**Key Benefits:**
- Analyzes code files to draft story
- Generates proper user story format with acceptance criteria
- Attaches reference files automatically
- Assigns and estimates in one step

---

### Example 3: Quick Status Check

**Context:** Daily standup, need to report on feature progress.

**Conversation:**

```
You: Give me a status summary for F116904

Claude: Let me get the complete picture for Feature F116904.
[Queries Rally for feature and all child stories]

Feature F116904: Plugin Usage Telemetry & Analytics with GAME

Status: In Development
Total: 6 stories, 48 points

Progress:
✅ Completed: 3 stories (24 points) - 50%
🔄 In-Progress: 2 stories (16 points) - 33%
📋 Defined: 1 story (8 points) - 17%

By Owner:
• senthilkumar b: 3 stories (24 pts) - 2 done, 1 in progress
• chris.le: 2 stories (16 pts) - 1 done, 1 in progress
• Unassigned: 1 story (8 pts)

Recent Completions:
✅ US773874: Configuration module for telemetry (8 pts)
✅ US773875: User identity detection (5 pts)

Blockers: None reported

You: What still needs to be done?

Claude: Based on the query, here's what's remaining:

🔄 In Progress:
• US773876: GAME API telemetry hook handler (8 pts) - chris.le
• US773877: LLM token usage estimation (8 pts) - senthilkumar b

📋 Not Started:
• US773878: Async non-blocking hook pattern (8 pts) - Unassigned

The unassigned story needs an owner. Would you like me to assign it?
```

**Key Benefits:**
- Instant status summary
- Progress visualization by percentage
- Owner breakdown
- Identifies blockers and unassigned work

---

### Example 4: Breaking Down TDD Documents into Rally

**Context:** You have a folder with multiple TDD (Technical Design Documents) and need to create capabilities and features in Rally.

**File Structure:**
```
~/Documents/Inscore-Design-Docs/Policy/
├── 1.Master TDD/master-tdd-full.md
├── 2. Common Library/common-library-tdd.md
├── 3. DynamoDB/dynamodb-specification.md
├── 4. Initiate Booking/
│   ├── 1. UI/angular-ui-design.md
│   └── 3. Lambda/
│       ├── booking-management-service.md
│       └── ui-components-service.md
├── 6. Producer Information/
│   ├── 1. UI/producer-information-ui-design.md
│   ├── 2. Request-Response/ProducerInfo-API-Documentation.md
│   ├── 3. Lambda/integration-proxy-producer-service.md
│   └── 4. DynamoDB/integration-proxy-flow-engine.md
└── Risk Location/
    ├── UI/risk-location-ui-design.md
    └── Lambda/
        ├── risk-location-lambda-service.md
        └── ui-component-risk-lambda-service.md
```

**Conversation:**

```
You: I have a folder with TDD documents at ~/Documents/Inscore-Design-Docs. Can you analyze
them and help me create capabilities and features in Rally under epic E4570?

Claude: Let me analyze the TDD documents in that folder to understand the scope.
[Reads all TDD files in the directory structure]

I found design documents for an insurance policy management system (Inscore) with 4 main
feature areas:
1. Common Library - Shared utilities and components
2. Initiate Booking - Policy booking workflow with UI and Lambda services
3. Producer Information - Producer search and management with full stack
   (UI, Lambda, API, DynamoDB)
4. Risk Location - Risk assessment location module with UI and Lambda

Based on the technical complexity and domain boundaries, I recommend organizing these
into 2 capabilities:

Capability 1: Policy Booking & Management
- Feature: Initiate Booking Workflow (UI + Lambda services)
- Feature: Producer Information Management (UI + Lambda + API + DynamoDB)
- Feature: Common Library & Shared Components

Capability 2: Risk Assessment
- Feature: Risk Location Module (UI + Lambda services)
- Feature: DynamoDB Schema & Data Layer

Would you like me to create these in Rally under Epic E4570?

You: Wait, I want to make some adjustments. The DynamoDB schema should be part of each
feature, not a separate feature. And let's split the Common Library into its own capability
since it's shared infrastructure.

Claude: Great point! Let me reorganize the structure:

Capability 1: Policy Booking & Management
- Feature: Initiate Booking Workflow (UI + Lambda + DynamoDB)
- Feature: Producer Information Management (UI + Lambda + API + DynamoDB)

Capability 2: Risk Assessment
- Feature: Risk Location Module (UI + Lambda + DynamoDB)

Capability 3: Shared Infrastructure
- Feature: Common Library & Shared Components

This way each feature owns its data layer, and common code is clearly separated as infrastructure.
Would you like me to proceed with this structure?

You: OK go

Claude: I'll create 3 capabilities and 4 features in Rally. Let me build the hierarchy and
preview the changes.
[Queries Rally for Epic E4570 details]

Preview: Rally Changes
══════════════════════════════════════════
Epic E4570: Inscore Policy Management Platform

  CREATE Capability "Policy Booking & Management"
    └─ Description: End-to-end policy booking workflow with producer management

    ├─ CREATE Feature "Initiate Booking Workflow"
    │   └─ Description: Policy booking UI with Angular, Lambda services, DynamoDB integration
    │      Based on: Policy/4. Initiate Booking/
    │      Estimated complexity: 42 points (UI + 2 Lambda + DB)

    └─ CREATE Feature "Producer Information Management"
        └─ Description: Producer search/management with full stack + data layer
           Based on: Policy/6. Producer Information/
           Estimated complexity: 63 points (UI + Lambda + API + DB)

  CREATE Capability "Risk Assessment"
    └─ Description: Risk evaluation with data persistence

    └─ CREATE Feature "Risk Location Module"
        └─ Description: Risk location assessment with UI, Lambda services, and DynamoDB
           Based on: Policy/Risk Location/ + Policy/3. DynamoDB/
           Estimated complexity: 42 points (UI + 2 Lambda + DB)

  CREATE Capability "Shared Infrastructure"
    └─ Description: Common libraries and shared components

    └─ CREATE Feature "Common Library & Shared Components"
        └─ Description: Reusable utilities, components, and services
           Based on: Policy/2. Common Library/common-library-tdd.md
           Estimated complexity: 21 points (library development)

Total: 3 capabilities, 4 features (168 story points estimated)
══════════════════════════════════════════

You: looks good but where'd you get those story point estimates?

Claude: Those are rough estimates based on the complexity of each layer:
• UI layer: typically 8-13 pts per feature (component + validators + services)
• Lambda layer: 8-13 pts per service (handler + processor + helpers + tests)
• API layer: 8-13 pts (endpoints + auth + error handling)
• DynamoDB: 8-13 pts (schema + migrations + queries)

These are just initial ballparks. You'll want to refine them when you break down each
feature into actual user stories based on the TDD details.

You: That UI layer estimate of 8-13 points seems way too high. This is just a basic form
with a few fields.

Claude: You're absolutely right. Looking at the TDD more carefully, this is much simpler
than I estimated. Let me adjust:
  • UI: 5-8 points (basic form with validation)
  • Lambda: 5-8 points (standard CRUD handler)
  • API: 3-5 points (straightforward endpoints)
  • DynamoDB: 3-5 points (single table, no complex indexes)

That brings us to 16-26 points per feature instead of 32-52.

[... conversation continues with estimate refinement ...]

You: Perfect. That's way more realistic than 168 points. Now go ahead and create the capabilities.

Claude: ✅ Created Rally work items:

Capability 1: Policy Booking & Management
• C892401: Policy Booking & Management
  ├─ F892402: Initiate Booking Workflow
  └─ F892403: Producer Information Management

Capability 2: Risk Assessment
• C892404: Risk Assessment
  └─ F892405: Risk Location Module

Capability 3: Shared Infrastructure
• C892406: Shared Infrastructure
  └─ F892407: Common Library & Shared Components

All work items created under Epic E4570.

Next steps:
1. Break down each feature into user stories (I can help with that based on the TDD specs)
2. Assign features to teams/owners
3. Set target iterations for delivery

You: Now create user stories for the Producer Information feature based on the TDD specs

Claude: Let me read the Producer Information TDD files to extract the requirements.
[Reads Policy/6. Producer Information/ files - UI, Lambda, API, DynamoDB specs]

Based on the multi-layer architecture (UI → Lambda → API → DynamoDB), I found 7 user stories:

1. Producer search UI component (8 pts)
2. Producer information display panel (5 pts)
3. Integration proxy Lambda service (8 pts)
4. Producer Info API endpoints (8 pts)
5. DynamoDB flow engine integration (8 pts)
6. Producer search filters and validation (5 pts)
7. Producer data caching and optimization (5 pts)

Total: 47 story points

Would you like me to create these stories under Feature F892403?

You: create all 7, assign to chris

Claude: Creating 7 stories under F892403...
[Generates story descriptions from TDD specs, applies story template]

Preview: Rally Changes
══════════════════════════════════════════
Feature F892403: Producer Information Management

  ├─ CREATE Story "Build producer search UI component" (8 pts)
  │   ├─ Owner: chris.le
  │   └─ Description: [Generated from producer-information-ui-design.md]
  │       • Angular component with Axis UI search control
  │       • Real-time search with debouncing
  │       • Display results in data grid

  ├─ CREATE Story "Create producer information display panel" (5 pts)
  │   ├─ Owner: chris.le
  │   └─ Description: [Generated from UI TDD]

  [... 5 more stories ...]

  └─ CREATE Story "Implement producer data caching" (5 pts)
      ├─ Owner: chris.le
      └─ Description: [Generated from integration specs]
          • Lambda-level caching with TTL
          • DynamoDB query optimization
          • Cache invalidation strategy

Total: 7 stories, 47 points
══════════════════════════════════════════

You: create them

Claude: ✅ Created 7 user stories in Rally:
• US892410 through US892416 under Feature F892403
• All stories assigned to chris.le
• Total: 47 story points

Your Producer Information feature is now fully decomposed in Rally. You can:
- Assign UI stories to frontend team, Lambda stories to backend team
- Set target iterations for each layer (UI → Lambda → API → DB)
- Link stories to specific TDD documents as needed

Would you like me to process the next feature (Initiate Booking) or move on to another task?
```

**Key Benefits:**
- **Automatic TDD Analysis**: Reads and understands multiple design documents across complex folder structures
- **Flexible Planning**: Review and adjust the proposed structure before creating work items
- **Logical Grouping**: Reorganizes features into capabilities based on domain boundaries
- **Hierarchy Creation**: Creates the complete Epic → Capability → Feature → Story hierarchy
- **Story Decomposition**: Extracts requirements from multi-layer TDDs to generate user stories
- **Interactive Refinement**: Supports back-and-forth conversation to perfect the Rally structure

---

### Example 5: Sprint Rebalancing with Team Velocity

**Context:** Leadership changed priorities. F116904 (Telemetry) is now critical and needs to be done in the next 2 iterations. Need to rebalance based on team velocity.

**Team Configuration** (`~/.claude/rally/team.json`):
```json
{
  "members": [
    {
      "name": "Chris",
      "rally_username": "chris.le",
      "estimate_multiplier": 0.6,
      "note": "Part-time on this project, 60% allocation"
    },
    {
      "name": "Jane",
      "rally_username": "jane.smith",
      "estimate_multiplier": 1.2,
      "note": "Senior developer, consistently exceeds estimates"
    },
    {
      "name": "Dhananjay",
      "rally_username": "dhananjay.agrawat",
      "estimate_multiplier": 1.0,
      "note": "Standard velocity"
    },
    {
      "name": "Senthilkumar",
      "rally_username": "senthilkumar.b",
      "estimate_multiplier": 0.8,
      "note": "New team member, ramping up"
    }
  ]
}
```

**Conversation:**

```
You: Leadership just changed priorities. F116904 (Telemetry) is now critical and needs to
be done in the next 2 iterations. Can you analyze our current sprint plan and help me rebalance?

Claude: Let me analyze the current sprint allocations and team capacity.
[Queries Rally for iterations 2026.PI1.Iteration2 and Iteration3]
[Reads team.json for velocity multipliers]

Current Sprint Plan:

Iteration 2 (Current + 1 week):
• F117399: Requirements Validator - 34 points across 4 stories
• F117572: BRD Generation - 21 points across 3 stories
• F116904: Telemetry - 13 points across 2 stories (PARTIAL)
Total: 68 points

Iteration 3 (Current + 3 weeks):
• F119972: Task Generation - 55 points across 6 stories
• F116904: Telemetry - 35 points across 4 stories (REMAINDER)
Total: 90 points

Team Capacity Analysis (Effective Points):

• Chris (0.6x): 13 pts nominal = 7.8 pts effective per iteration
• Jane (1.2x): 21 pts nominal = 25.2 pts effective per iteration
• Dhananjay (1.0x): 21 pts nominal = 21 pts effective per iteration
• Senthilkumar (0.8x): 13 pts nominal = 10.4 pts effective per iteration

Team Total per Iteration: 68 nominal points = 64.4 effective points

Recommended Rebalancing:

To prioritize F116904 (48 total points), I recommend:

Iteration 2:
+ Move ALL F116904 stories here (48 points)
+ Keep F117399: Requirements Validator (34 points - HIGH PRIORITY)
- Defer F117572: BRD Generation (21 points)
New Total: 82 nominal points = 77.6 effective points (120% capacity - achievable with focus)

Iteration 3:
+ Pull in F117572: BRD Generation (21 points - deferred from Iter 2)
+ Keep F119972: Task Generation (55 points)
New Total: 76 nominal points = 71.9 effective points (112% capacity - manageable)

This ensures F116904 is 100% complete by end of Iteration 2.

Would you like me to make these changes in Rally?

You: 120% in Iter 2 is too much. Can we defer some of the Requirements Validator stories?

Claude: Good call. Let me check the F117399 stories to see what can be deferred.
[Queries Rally for F117399 child stories]

F117399 has 4 stories:
• US782901: Core validation engine (13 pts) - CRITICAL
• US782902: Rally API integration (8 pts) - CRITICAL
• US782903: Missing stories validator (8 pts) - Can defer
• US782904: Generate validation reports (5 pts) - Can defer

Adjusted Rebalancing:

Iteration 2:
+ F116904: All Telemetry stories (48 points)
+ F117399: Core engine + Rally integration only (21 points)
- Defer F117399: Missing stories + Reports (13 points)
- Defer F117572: BRD Generation (21 points)
New Total: 69 nominal points = 65.2 effective points (101% capacity - comfortable)

Iteration 3:
+ F117399: Missing stories + Reports (13 points - deferred from Iter 2)
+ F117572: BRD Generation (21 points - deferred from Iter 2)
+ F119972: Task Generation (55 points)
New Total: 89 nominal points = 84.2 effective points (131% capacity)

⚠️ Warning:
Iteration 3 is still overloaded. Recommend splitting F119972 or deferring lower priority stories.

You: Good for Iter 2. Defer F117572 to Iter 4 instead. Do it.

Claude: Excellent. Final plan:

Iteration 2: 69 points (101% capacity)
Iteration 3: 68 points (106% capacity with F117572 deferred)
Iteration 4: F117572 (21 points)

Let me preview the Rally changes:

Preview: Rally Changes
══════════════════════════════════════════
MOVE to 2026.PI1.Iteration2:
• 4 stories from F116904 currently in Iteration3
  └─ US773876, US773877, US773878, US773879

MOVE to 2026.PI1.Iteration3:
• 2 stories from F117399 currently in Iteration2
  └─ US782903, US782904

MOVE to 2026.PI1.Iteration4:
• 3 stories from F117572 currently in Iteration2
  └─ US783401, US783402, US783403

Total: 9 stories to move
Backup: 9 stories backed up
══════════════════════════════════════════

You: OK do it

Claude: ✅ Successfully rebalanced sprints
• Moved 4 stories to Iteration 2 (F116904 now complete in this sprint)
• Moved 2 stories to Iteration 3
• Moved 3 stories to Iteration 4

Backup saved: .claude/rally/backups/20260122_154823_UPDATE_sprint_rebalance.json

Final Sprint Capacity:
✓ Iteration 2: 69 pts nominal / 65.2 pts effective (101% - achievable)
✓ Iteration 3: 68 pts nominal / 64.4 pts effective (100% - perfect fit)
ℹ Iteration 4: 21 pts nominal / 19.9 pts effective (31% - room for more)

F116904 (Telemetry) is now fully scheduled for Iteration 2 completion! 🎯
```

**Key Benefits:**
- **Team Velocity Tracking**: Uses estimate_multiplier to account for part-time, ramping, and high-performing team members
- **Capacity Planning**: Calculates effective points based on individual multipliers
- **Sprint Rebalancing**: Moves stories between iterations based on changing priorities
- **Interactive Planning**: User can refine the plan through multiple iterations
- **What-If Analysis**: Shows capacity impact before committing changes
- **Safety Net**: Creates backups before sprint changes for easy rollback

---

## Safety Features

Product Owner/Scrum Master Plugin provides multiple layers of protection to prevent accidental data loss or unwanted changes in Rally.

### 1. Preview Before Changes

Every operation that modifies Rally shows a detailed preview with tree structure:

```
Preview: Rally Changes
══════════════════════════════════════════
UPDATE Story US782974 "Ask user how they want nested components"
  └─ Owner: None → Dhananjay Agrawat

Total: 5 stories to update
Backup: 5 stories backed up
══════════════════════════════════════════
```

### 2. Automatic Backups

Every UPDATE or DELETE operation automatically creates a timestamped backup before making changes:

```
✅ Backup created: .claude/rally/backups/20260210_143052_UPDATE_US773876.json
```

Backups include:
- Full Rally work item details (name, description, estimates, owners, hierarchy)
- Timestamp and operation type (UPDATE/DELETE)
- Original values before the change

### 3. Backup Management

**View recent backups:**
```
You: "Show me recent Rally backups"

You: "List all backups from today"
```

**Check specific backup contents:**
```
You: "Show me what's in the backup file 20260210_143052_UPDATE_US773876.json"
```

**Create manual backup:**
```
You: "Backup all stories in F116904 before I manually edit them in the Rally UI"
```

### 4. Restore from Backup

**Restore a single work item:**
```
You: "Restore US773876 from the backup at 14:30 today"

You: "Restore from backup .claude/rally/backups/20260210_143052_UPDATE_US773876.json"
```

**Dry run (preview restore):**
```
You: "Preview what would happen if I restore from backup 20260210_143052_UPDATE_US773876.json"
```

**Restore multiple work items:**
```
You: "Restore all stories from the backup I created at 2pm"

You: "Restore F116904 and all its child stories from this morning's backup"
```

### 5. Explicit Confirmation

Nothing changes in Rally without your approval. The workflow always follows this pattern:
1. **Analyze**: Parse your request and query Rally for current state
2. **Preview**: Show you exactly what will change with tree structure
3. **Backup**: Create automatic backup for UPDATE/DELETE operations
4. **Confirm**: Ask for explicit approval before proceeding
5. **Execute**: Make changes only after you confirm

### Backup Best Practices

1. **Preview changes before confirming** - Review the tree structure to ensure correctness
2. **Keep backups organized** - Backups are automatically timestamped and categorized by operation type
3. **Verify after bulk operations** - After bulk updates, check a few stories in Rally to confirm correctness
4. **Document major changes** - Add notes to stories explaining why estimates or assignments changed

**Command-line restore (alternative method):**
```bash
# List recent backups
~/.claude/venvs/ADLC/Scripts/rally list_backups

# Restore from backup
~/.claude/venvs/ADLC/Scripts/rally restore_from_backup ~/.claude/rally/backups/20260122_143052.json
```

---

---

## Troubleshooting

### CLI Not Found

**Problem:** `rally: command not found`

**Solution:**
The Rally CLI is installed at `~/.claude/venvs/ADLC/Scripts/rally` (Windows) or `~/.claude/venvs/ADLC/bin/rally` (macOS/Linux).

Use the full path or add to your PATH:
```bash
# Windows (Git Bash)
export PATH="$HOME/.claude/venvs/ADLC/Scripts:$PATH"

# macOS/Linux
export PATH="$HOME/.claude/venvs/ADLC/bin:$PATH"
```

### API Key Issues

**Problem:** "Authentication failed" or "Invalid API key"

**Solution:**
1. Verify your API key at https://rally1.rallydev.com/#/api_key
2. Check `~/.claude/aig.json` has the correct key
3. Re-run setup: `/Product Owner/Scrum Master Plugin:setup`

### Unicode Encoding Errors

**Problem:** `UnicodeEncodeError: 'charmap' codec can't encode character`

**Solution:**
This was fixed in v0.9.6-dev.5. Update your plugin:
```bash
/plugin update Product Owner/Scrum Master Plugin
```

### Stories Not Found

**Problem:** Rally query returns empty results

**Solution:**
1. Verify the iteration/feature name is correct
2. Check you're in the correct workspace/project
3. Ensure stories exist in Rally (check via web UI)

Example:
```
# Wrong: 2026.IT1.Iteration3
# Correct: 2026.PI1.Iteration3 (it's "PI" not "IT")
```

### Backup Restoration Failed

**Problem:** Restore command fails or doesn't undo changes

**Solution:**
1. List all backups: `rally list_backups`
2. Check backup file exists and is valid JSON
3. Use the Rally web UI as a fallback to manually restore
4. Contact your Rally administrator if data is critical

---

## Origin Story

Originally developed as an internal tool for the AI COE (Center of Excellence) Team to manage their own Sprint workflows in Rally, Product Owner/Scrum Master Plugin proved so valuable that it's now available for the wider AIG (Accenture Innovation Group) team.

- **Battle-tested**: Used daily by the AI COE team for sprint planning, story creation, and progress tracking throughout 2025
- **Real-world refined**: Every feature was built to solve actual pain points encountered during plugin development sprints

---

**Version:** 1.1.0
**Last Updated:** February 16, 2026
