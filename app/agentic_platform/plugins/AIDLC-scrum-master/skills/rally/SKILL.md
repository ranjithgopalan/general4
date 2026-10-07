---
name: rally
description: Rally API CLI examples and comprehensive command reference for querying, creating, and managing Rally work items with mandatory user approval for all update operations
license: Proprietary
compatibility: Requires Rally CLI installed at ~/.claude/venvs/ADLC/ and configured via setup skill. Python 3.8+ required.
metadata:
  author: ADLC Scrum Master Team
  version: "1.1.1"
  organization: AIG
  plugin: AIDLC-scrum-master
allowed-tools: Bash Read Write AskUserQuestion
---

# Rally API Skill

CLI interface for querying, creating, and managing Rally work items. All examples use the `rally` command directly.

## Prerequisites Check

**IMPORTANT: Before executing any rally commands, verify the Rally CLI is installed and configured.**

### Check CLI Availability

```bash
# macOS/Linux
~/.claude/venvs/ADLC/bin/rally --help 2>/dev/null || echo "NOT_FOUND"

# Windows (use full path — tilde may not expand in all Windows contexts)
$USERPROFILE/.claude/venvs/ADLC/Scripts/rally --help 2>/dev/null || \
  ~/.claude/venvs/ADLC/Scripts/rally --help 2>/dev/null || echo "NOT_FOUND"
```

### Auto-Setup if Not Found

If the command returns "NOT_FOUND" or fails:

1. **Try activating the ADLC venv and retrying:**
   ```bash
   # macOS/Linux
   source ~/.claude/venvs/ADLC/bin/activate 2>/dev/null && rally --help 2>/dev/null || echo "STILL_NOT_FOUND"

   # Windows
   source ~/.claude/venvs/ADLC/Scripts/activate 2>/dev/null && rally --help 2>/dev/null || echo "STILL_NOT_FOUND"
   ```
2. If rally works after activation: proceed (venv is now active in the current shell session)
3. If still `STILL_NOT_FOUND` after venv activation:
   - Display: `Rally CLI not found. Setting up Rally CLI now...`
   - **Invoke the setup skill:** Use the Skill tool to invoke `AIDLC-scrum-master:setup`
   - After setup, re-verify the rally command is available before proceeding
   - If setup fails, display: `Rally CLI setup was not completed. Run /AIDLC-scrum-master:setup to configure.` — then STOP

### Windows: Always Use Full Path

On Windows, the `rally` command may not be on the system PATH. **Always use the full path** for all rally commands:

```bash
# Windows — use full path for every rally command
~/.claude/venvs/ADLC/Scripts/rally find_by_formatted_id US12345
~/.claude/venvs/ADLC/Scripts/rally get_features '{"query":"(State = In-Progress)"}'
```

If `~` does not expand (Git Bash not available or PATH issue), use the explicit Windows home path:
```bash
$USERPROFILE/.claude/venvs/ADLC/Scripts/rally find_by_formatted_id US12345
```

## Quick Start — Most Common Commands

> **Windows:** Replace `rally` with the full path: `~/.claude/venvs/ADLC/Scripts/rally`

```bash
# Find any item by ID (auto-detects type)
rally find_by_formatted_id US12345
rally find_by_formatted_id F67890

# Query items with filters
rally get_features '{"query":"(State = In-Progress)"}'
rally get_user_stories '{"query":"(Iteration.Name = Sprint 23)"}'
rally get_user_stories '{"query":"(Feature.FormattedID = F12345)"}'

# Get children of an item
rally get_children /portfolioitem/feature/12345 hierarchicalrequirement

# Create a user story (inherits Release/Iteration/Owner from parent)
rally create_user_story \
  /portfolioitem/feature/12345 \
  "Story name" "role" "want" "so_that" \
  '["AC 1","AC 2","AC 3"]'

# Create a feature under a capability
rally create_feature "Feature name" /portfolioitem/capability/12345 "Description"

# Update an item (⚠️ REQUIRES user approval — see below)
rally update_object /hierarchicalrequirement/67890 '{"ScheduleState":"In-Progress"}' true

# Validate estimates
rally validate_feature_estimates /portfolioitem/feature/12345

# Advanced query with specific fields
rally query hierarchicalrequirement \
  '{"query":"((Iteration.Name = Sprint 23) AND (ScheduleState = Completed))"}' \
  'Name,FormattedID,Owner,PlanEstimate'
```

**CLI Tips:** Use single quotes for JSON args. Booleans: `true`/`false`. Null: `null`. Debug: `rally --debug <command>`.

## CLI-First Constraint

**ALWAYS use the `rally` CLI command for all Rally operations. NEVER work around it by:**
- Importing `rally_api` modules directly in Python (`from rally_api import ...`)
- Running inline Python scripts that call the Rally API (`python -c "..."`)
- Calling `client.py` or any source file directly

The CLI is the stable, supported interface. Direct Python access bypasses logging, argument validation, and error
handling built into the CLI layer. If a CLI command fails, fix the root cause — don't substitute Python code.

**Correct:**
```bash
~/.claude/venvs/ADLC/Scripts/rally create_initiative "Name" /portfolioitem/theme/12345 "Description"
```

**Wrong:**
```bash
~/.claude/venvs/ADLC/Scripts/python -c "from rally_api import RallyAPI; client = RallyAPI(); ..."
```

If a `rally` command returns an error, diagnose and address the error. Do not fall back to running Python directly.

---

## OPERATIONAL CONSTRAINTS

### Rally Update Safety Protocol

OPERATIONAL_CONSTRAINTS[Type,Rules]:
  ALWAYS,"BEFORE any Rally update operation (update_object, move_story, delete_object, bulk_update), MUST display to user exactly what will change | MUST show current values vs new values | MUST use AskUserQuestion to get explicit user approval | MUST provide clear options: 'Approve changes' vs 'Cancel' | MUST NOT proceed with updates if user cancels | MUST explain reason for each proposed change | MUST show which Rally items (FormattedID) will be affected"
  NEVER,"make Rally updates without explicit user approval via AskUserQuestion | update Rally based on assumed user intent | skip the approval step even if changes seem minor | proceed if user selects 'Cancel' or equivalent option | make destructive changes (delete, move) without extra confirmation"

**Approval Workflow Template:**

1. **Display proposed changes** (nest when updates span parent → children):
```markdown
## Proposed Rally Updates (3 items)

**F12345** — User Authentication Feature
├── State: Backlog → Implementation
├── Reason: Starting sprint work
└── Stories:
    ├── **US67890** — Login page
    │   ├── ScheduleState: Defined → In-Progress
    │   └── Iteration: _(none)_ → Sprint 23
    └── **US67891** — Logout button
        ├── ScheduleState: Defined → In-Progress
        └── Iteration: _(none)_ → Sprint 23
```

For a single-item update, flatten to one level:
```markdown
## Proposed Rally Updates (1 item)

**US774991** — Create BRD template system
├── ScheduleState: Completed → Accepted
├── ActualEndDate: _(empty)_ → 2026-02-16
└── Reason: Plugin implementation is complete and deployed
```

2. **Use AskUserQuestion:**
```json
{
  "questions": [{
    "question": "Do you approve these Rally updates?",
    "header": "Rally Update",
    "options": [
      {
        "label": "Approve changes",
        "description": "Proceed with updating [N] Rally item(s)"
      },
      {
        "label": "Cancel",
        "description": "Do not make any changes to Rally"
      }
    ],
    "multiSelect": false
  }]
}
```

3. **Act based on response:**
- If "Approve changes": Proceed with Rally updates
- If "Cancel": Do NOT make any changes, inform user that updates were cancelled

**Exception:** Read-only operations (get_*, query_*, find_*, validate_*, analyze_*) do NOT require approval.

## Additional Resources

Full documentation in `references/` directory:

- **[reference.md](references/reference.md)** — Complete CLI command reference (all commands, parameters, types)
- **[examples.md](references/examples.md)** — 50+ CLI examples organized by category
- **[quick-start.md](references/quick-start.md)** — Getting started in 5 minutes with tips
- **[operations.md](references/operations.md)** — Common operations quick reference
- **[workflows.md](references/workflows.md)** — Multi-step workflow patterns (feature setup, sprint planning)
- **[update-safety.md](references/update-safety.md)** — Detailed approval workflow and exempt operations
