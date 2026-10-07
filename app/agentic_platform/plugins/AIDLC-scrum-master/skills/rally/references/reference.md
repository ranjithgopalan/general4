# Rally CLI Command Reference

Complete command reference for the `rally` CLI. All commands assume the CLI is installed and Rally credentials are
configured. See [quick-start.md](quick-start.md) to get started.

---

## Table of Contents

- [Prerequisites](#prerequisites)
- [CLI Argument Rules](#cli-argument-rules)
- [Query Commands](#query-commands)
- [Find Commands](#find-commands)
- [Create Commands](#create-commands)
- [Update Commands](#update-commands)
- [Delete Commands](#delete-commands)
- [Validation Commands](#validation-commands)
- [Data Consistency Commands](#data-consistency-commands)
- [Iteration & Release Commands](#iteration--release-commands)
- [Clone & Backup Commands](#clone--backup-commands)
- [Attachment Commands](#attachment-commands)
- [Low-Level API Commands](#low-level-api-commands)
- [Utility Commands](#utility-commands)
- [Workspace & Project Commands](#workspace--project-commands)
- [Object Types](#object-types)
- [Field Reference](#field-reference)
- [SAFe Portfolio Item Workflow States](#safe-portfolio-item-workflow-states)
- [Query Syntax](#query-syntax)
- [Configuration](#configuration)

---

## Prerequisites

```bash
# Verify CLI is installed
# macOS/Linux
~/.claude/venvs/ADLC/bin/rally --help 2>/dev/null || echo "NOT_FOUND"

# Windows
~/.claude/venvs/ADLC/Scripts/rally --help 2>/dev/null || echo "NOT_FOUND"
```

If `NOT_FOUND`, invoke the setup skill: `/AIDLC-scrum-master:setup`

---

## CLI Argument Rules

| Type | Format | Example |
|------|--------|---------|
| String | Bare or quoted | `US12345` or `"Sprint 23"` |
| JSON object | Single-quoted JSON | `'{"query":"(State = Funnel)"}'` |
| JSON array | Single-quoted JSON | `'["item1","item2"]'` |
| Boolean | `true`/`false` (also `yes`/`no`, `1`/`0`) | `true` |
| Null | Literal `null` | `null` |
| Ref | Rally reference path | `/portfolioitem/feature/12345` |
| Fields list | Comma-separated, single-quoted | `'Name,FormattedID,State'` |

**Debug mode:** Add `--debug` before the command name for full error tracebacks:
```bash
rally --debug get_features '{"query":"(State = Funnel)"}'
```

---

## Query Commands

### get_* — Get All Items by Type

```bash
rally get_workspaces
rally get_features
rally get_capabilities
rally get_epics
rally get_initiatives
rally get_themes
rally get_user_stories
rally get_tasks
rally get_defects
rally get_iterations
```

### get_* with Filters

Pass a JSON object with a `query` key:

```bash
rally get_features '{"query":"(State = In-Progress)"}'
rally get_features '{"query":"(Parent.FormattedID = C12345)"}'
rally get_user_stories '{"query":"(Iteration.Name = Sprint 23)"}'
rally get_user_stories '{"query":"(Owner.UserName = john.doe@example.com)"}'
rally get_user_stories '{"query":"(Blocked = true)"}'
rally get_defects '{"query":"(Priority = High Attention)"}'
```

### query — Advanced Query

```bash
rally query <object_type> '<query_json>' '<fetch_fields>' '<order>' <pagesize> <include_project_scope>
```

**Parameters (positional):**
1. `object_type` (str): Rally object type (e.g., `hierarchicalrequirement`, `portfolioitem/feature`)
2. `query_json` (JSON): Query filter as `'{"query":"(...)"}'`
3. `fetch_fields` (str): Comma-separated fields to retrieve (default: `Name,FormattedID,ObjectID`)
4. `order` (str|null): Sort field (e.g., `"CreationDate desc"`)
5. `pagesize` (int): Results per page (default: 200)
6. `include_project_scope` (bool): Include project scope (default: true)

```bash
# Stories in iteration AND state
rally query hierarchicalrequirement \
  '{"query":"((Iteration.Name = Sprint 23) AND (ScheduleState = Completed))"}' \
  'Name,FormattedID,ScheduleState'

# Features with no children
rally query portfolioitem/feature \
  '{"query":"(Children.Count = 0)"}' \
  'Name,FormattedID,State'

# Overdue items
rally query portfolioitem/feature \
  '{"query":"((PlannedEndDate < today) AND (State != Shipped))"}' \
  'Name,FormattedID,PlannedEndDate,State'

# Order by creation date, limit to 50
rally query portfolioitem/feature \
  '{"query":"(State = Funnel)"}' \
  'Name,FormattedID' \
  'CreationDate desc' 50 true
```

### get_object — Get Object by Ref

```bash
rally get_object <ref> [fetch_fields]
```

```bash
rally get_object /portfolioitem/feature/12345
rally get_object /portfolioitem/feature/12345 'Name,FormattedID,State,Owner,PlannedEndDate'
rally get_object /hierarchicalrequirement/67890 'Name,FormattedID,ScheduleState,Owner,PlanEstimate,Tasks,Feature'
```

### get_children — Get Child Items

```bash
rally get_children <parent_ref> [child_type]
```

```bash
rally get_children /portfolioitem/capability/12345
rally get_children /portfolioitem/capability/12345 portfolioitem/feature
rally get_children /portfolioitem/feature/67890 hierarchicalrequirement
rally get_children /hierarchicalrequirement/11111 task
```

---

## Find Commands

### find_by_formatted_id — Find Any Item by ID

Auto-detects type from the ID prefix (US, F, C, E, TA, DE, etc.).

```bash
rally find_by_formatted_id <formatted_id>
```

```bash
rally find_by_formatted_id F12345
rally find_by_formatted_id US67890
rally find_by_formatted_id TA4321
rally find_by_formatted_id DE9999
```

### find_* — Type-Specific Find

```bash
rally find_feature F12345
rally find_user_story US67890
rally find_task TA4321
rally find_defect DE9999
rally find_portfolio_item C12345 capability
rally find_portfolio_item E54321 epic
rally find_portfolio_item I98765 initiative
```

### search_by_name — Search by Name

```bash
rally search_by_name <object_type> <name> <exact_match>
```

```bash
rally search_by_name hierarchicalrequirement "Login Feature" true
rally search_by_name portfolioitem/feature "Authentication" false
```

### find_iteration / find_release / find_user

```bash
rally find_iteration "Sprint 23"
rally find_release "Release 2.0"
rally find_user john.doe@example.com
```

---

## Create Commands

### create_theme

```bash
rally create_theme <name> [description]
```

```bash
rally create_theme "Digital Transformation" "Strategic initiative for modernization"
```

### create_initiative

```bash
rally create_initiative <name> <parent_ref> [description]
```

```bash
rally create_initiative "Cloud Migration" /portfolioitem/theme/12345 "Migrate services to AWS"
```

### create_epic

```bash
rally create_epic <name> <parent_ref> [description]
```

```bash
rally create_epic "User Authentication" /portfolioitem/initiative/67890 "Implement OAuth 2.0 authentication"
```

### create_capability

```bash
rally create_capability <name> <parent_ref> [description]
```

```bash
rally create_capability "Login System" /portfolioitem/epic/11111 "Multi-factor authentication"
```

### create_feature

```bash
rally create_feature <name> <parent_ref> [description] [state]
```

```bash
rally create_feature "Password Reset" /portfolioitem/capability/22222 "Self-service password reset flow"
rally create_feature "Auth System" /portfolioitem/capability/22222 "OAuth2 and 2FA support" "Backlog"
```

### create_user_story

```bash
rally create_user_story <feature_ref> <name> <role> <want> <so_that> <acceptance_criteria_json> [schedule_state]
```

**Parameters (positional):**
1. `feature_ref`: Parent feature reference
2. `name`: Story name/title (3-8 words)
3. `role`: User role (e.g., "user", "developer", "admin")
4. `want`: What the user wants
5. `so_that`: The benefit
6. `acceptance_criteria_json`: JSON array of criteria
7. `schedule_state` (optional): Initial state (default: "Defined")

```bash
rally create_user_story \
  /portfolioitem/feature/12345 \
  "User can reset password" \
  "user" \
  "reset my password without contacting support" \
  "I can regain access to my account quickly" \
  '["User clicks forgot password link","User enters email address","System sends reset link","User creates new password"]'

# With custom schedule state
rally create_user_story \
  /portfolioitem/feature/12345 \
  "User receives confirmation email" \
  "user" \
  "receive a confirmation email after password reset" \
  "I know the change was successful" \
  '["Email is sent within 1 minute","Email contains confirmation message"]' \
  "In-Progress"
```

**Note:** Automatically inherits Release, Iteration, and Owner from parent feature.

### create_task

```bash
rally create_task <story_ref> <name> <description> [detail] [state] [owner_ref] [estimate]
```

```bash
rally create_task \
  /hierarchicalrequirement/67890 \
  "Design password reset UI" \
  "Create mockups for password reset flow" \
  "Design user-friendly password reset form with validation"

# With owner and estimate
rally create_task \
  /hierarchicalrequirement/67890 \
  "Implement password reset API" \
  "Build backend endpoint for password reset" \
  "Create Lambda function with token generation" \
  "Defined" \
  /user/12345 \
  16
```

### create_defect

```bash
rally create_defect <name> <description> <root_cause> <steps_to_reproduce> <expected> <actual> \
  <environment> [state] [priority] [severity]
```

```bash
rally create_defect \
  "Login fails with special characters" \
  "Users cannot login when password contains special characters" \
  "Password validation incorrectly rejects valid special characters" \
  "Use password with !@#\$%^&*()" \
  "User should be able to login" \
  "Login fails with validation error" \
  "Production environment" \
  "Submitted" \
  "High Attention" \
  "Major Problem"
```

### bulk_create_stories

```bash
rally bulk_create_stories <feature_ref> <stories_json> [inherit] [parallel] [auto_estimate] [use_ai]
```

```bash
rally bulk_create_stories \
  /portfolioitem/feature/12345 \
  '[{"name":"Story 1","description":"First story"},{"name":"Story 2","description":"Second story"}]' \
  true true

# With auto-estimation
rally bulk_create_stories \
  /portfolioitem/feature/12345 \
  '[{"name":"Story 1","description":"..."},{"name":"Story 2","description":"..."}]' \
  true true true true
```

### estimate_user_story

```bash
rally estimate_user_story <name> <description> <acceptance_criteria_json> <complexity_json> \
  <use_ai> <team_member>
```

```bash
# AI-assisted estimation
rally estimate_user_story \
  "Implement OAuth 2.0 authentication" \
  "Add OAuth authentication with Google and GitHub providers" \
  '["Support Google OAuth","Support GitHub OAuth","Store tokens securely"]' \
  null true chris

# Manual complexity factors (technical, integration, risk each 1-5)
rally estimate_user_story \
  "Implement OAuth 2.0" \
  "Add OAuth authentication" \
  null \
  '{"technical_complexity":4,"integration_requirements":3,"risk_uncertainty":2}' \
  false chris
```

---

## Update Commands

**All update operations require user approval.** See [update-safety.md](update-safety.md).

### update_object

```bash
rally update_object <ref> <fields_json> [backup]
```

**Parameters (positional):**
1. `ref`: Object reference URL
2. `fields_json`: JSON object of fields to update
3. `backup` (bool, optional): Create backup before update (default: true)

```bash
# Update story state
rally update_object /hierarchicalrequirement/67890 '{"ScheduleState":"In-Progress"}' true

# Update feature state and owner
rally update_object /portfolioitem/feature/12345 '{"State":"Implementing","Owner":"/user/98765"}' true

# Update task status and hours
rally update_object /task/11111 '{"State":"In-Progress","Estimate":8,"ToDo":4}' true

# Update without backup
rally update_object /hierarchicalrequirement/67890 '{"PlanEstimate":13}' false
```

### move_story

```bash
rally move_story <story_ref> <target_feature_ref> [inherit]
```

```bash
rally move_story /hierarchicalrequirement/67890 /portfolioitem/feature/54321
rally move_story /hierarchicalrequirement/67890 /portfolioitem/feature/54321 true
```

---

## Delete Commands

```bash
rally delete_object <ref> [backup]
```

```bash
rally delete_object /task/11111          # With backup (default)
rally delete_object /task/11111 false    # Without backup
```

---

## Validation Commands

### validate_story_estimate

```bash
rally validate_story_estimate <story_ref> [skip_stage_stories]
```

```bash
rally validate_story_estimate /hierarchicalrequirement/67890
rally validate_story_estimate /hierarchicalrequirement/67890 true
```

### validate_story_hierarchy

```bash
rally validate_story_hierarchy <story_ref>
```

```bash
rally validate_story_hierarchy /hierarchicalrequirement/67890
```

### validate_feature_estimates

```bash
rally validate_feature_estimates <feature_ref> [skip_stage_stories]
```

```bash
rally validate_feature_estimates /portfolioitem/feature/12345
rally validate_feature_estimates /portfolioitem/feature/12345 true
```

### validate_hierarchy

```bash
rally validate_hierarchy <item_ref>
```

```bash
rally validate_hierarchy /portfolioitem/feature/12345
```

### audit_initiative_estimates

```bash
rally audit_initiative_estimates <initiative_ref> [skip_stage_stories]
```

```bash
rally audit_initiative_estimates /portfolioitem/initiative/98765
rally audit_initiative_estimates /portfolioitem/initiative/98765 false
```

### validate_before_create

```bash
rally validate_before_create <object_type> <name> <parent_ref>
```

```bash
rally validate_before_create hierarchicalrequirement "New Story" /portfolioitem/feature/12345
```

---

## Data Consistency Commands

### analyze_data_consistency

Checks for: missing PlannedEndDate, misaligned Release/Iteration, parent-child date inconsistencies.

```bash
rally analyze_data_consistency <item_ref> [check_children] [check_parent]
```

```bash
rally analyze_data_consistency /portfolioitem/feature/12345
rally analyze_data_consistency /portfolioitem/feature/12345 true true
rally analyze_data_consistency /portfolioitem/feature/12345 true false
```

### fix_task_estimates

```bash
rally fix_task_estimates <story_ref> [dry_run]
```

```bash
rally fix_task_estimates /hierarchicalrequirement/67890 true   # Dry run
rally fix_task_estimates /hierarchicalrequirement/67890         # Apply fixes
```

---

## Iteration & Release Commands

### find_iteration / find_release

```bash
rally find_iteration "Sprint 23"
rally find_release "Release 2.0"
```

### find_release_for_iteration

```bash
rally find_release_for_iteration '{"Name":"Sprint 23","StartDate":"2024-01-01","EndDate":"2024-01-14"}'
```

### find_all_iterations_with_context

```bash
rally find_all_iterations_with_context "Sprint 23"
```

### prepare_iteration_and_release

```bash
rally prepare_iteration_and_release <iteration_name> <release_name|null> <auto_set_release>
```

```bash
rally prepare_iteration_and_release "Sprint 23" null true
rally prepare_iteration_and_release "Sprint 23" "Release 2.0" false
```

---

## Clone & Backup Commands

### clone_item

```bash
rally clone_item <ref> [new_name] [new_parent_ref] [include_children]
```

```bash
rally clone_item /hierarchicalrequirement/67890
rally clone_item /hierarchicalrequirement/67890 "Copy of Login Feature"
rally clone_item /hierarchicalrequirement/67890 "New Story" /portfolioitem/feature/54321
rally clone_item /portfolioitem/feature/12345 "Copy of Feature" null true
```

### restore_from_backup

```bash
rally restore_from_backup <backup_path> [parent_ref] [include_attachments]
```

```bash
rally restore_from_backup /path/to/backup/feature_12345.json
rally restore_from_backup /path/to/backup/feature_12345.json /portfolioitem/capability/98765
rally restore_from_backup /path/to/backup/feature_12345.json null false
```

---

## Attachment Commands

```bash
rally get_attachments <item_ref>
rally upload_attachment <item_ref> <filename> <content> <content_type> [description]
```

```bash
rally get_attachments /portfolioitem/feature/12345
rally upload_attachment \
  /portfolioitem/feature/12345 \
  "design.html" \
  "<html><body>Design document</body></html>" \
  "text/html" \
  "Feature design document"
```

---

## Low-Level API Commands

### get / post — Direct HTTP

```bash
rally get <object_type> '<query_json>' [include_project_scope]
rally post <object_type> '<fields_json>' [include_project_scope]
```

```bash
rally get portfolioitem/feature '{"query":"(State = Funnel)"}' true
rally post hierarchicalrequirement '{"Name":"New Story","Feature":"/portfolioitem/feature/12345"}' true
```

---

## Utility Commands

```bash
# Check if a story name is a stage story
rally is_stage_story "Stage 1 - Design"
rally is_stage_story "Regular User Story"

# Calculate expected task hours for a story
rally get_expected_task_estimate /hierarchicalrequirement/67890

# List all available commands
rally
```

---

## Workspace & Project Commands

```bash
rally set_workspace /workspace/12345678
rally set_project /project/98765432 "My Project"
```

---

## Object Types

### Portfolio Items

| Type | Object Type String | ID Format |
|------|-------------------|-----------|
| Theme | `portfolioitem/theme` | T##### |
| Initiative | `portfolioitem/initiative` | I##### |
| Epic | `portfolioitem/epic` | E##### |
| Capability | `portfolioitem/capability` | C##### |
| Feature | `portfolioitem/feature` | F##### |

### Work Items

| Type | Object Type String | ID Format |
|------|-------------------|-----------|
| User Story | `hierarchicalrequirement` | US##### |
| Task | `task` | TA##### |
| Defect | `defect` | DE##### |

### Other

| Type | Object Type String |
|------|-------------------|
| Iteration | `iteration` |
| Release | `release` |
| User | `user` |
| Attachment | `attachment` |

---

## Field Reference

### User Story Fields

**Common Fields:**
- `Name` (str): Story title
- `FormattedID` (str): Story ID (e.g., "US12345")
- `PlanEstimate` (int): Story points
- `ScheduleState` (str): State (Defined, In-Progress, Completed, Accepted, Deployed)
- `Owner` (ref): Owner user reference
- `Feature` (ref): Parent feature reference
- `Iteration` (ref): Iteration reference
- `Release` (ref): Release reference
- `Description` (str): Story description (HTML)
- `Tasks` (collection): Related tasks
- `Blocked` (bool): Whether story is blocked
- `_ref` (str): Object reference URL
- `ObjectID` (int): Object ID

**States:** `Idea` → `Defined` → `In-Progress` → `Completed` → `Accepted` → `Deployed`

### Task Fields

**Common Fields:**
- `Name` (str): Task title
- `FormattedID` (str): Task ID (e.g., "TA12345")
- `State` (str): State (Defined, In-Progress, Completed)
- `Estimate` (float): Estimated hours
- `ToDo` (float): Remaining hours
- `Owner` (ref): Owner user reference
- `WorkProduct` (ref): Parent story/defect reference
- `Description` (str): Task description
- `Blocked` (bool): Whether task is blocked

**States:** `Defined` → `In-Progress` → `Completed`

### Feature Fields

**Common Fields:**
- `Name` (str): Feature title
- `FormattedID` (str): Feature ID (e.g., "F12345")
- `State` (str): State (Funnel, Review, Analysis, Backlog, Implementation, Done)
- `Parent` (ref): Parent capability reference
- `Description` (str): Feature description
- `Owner` (ref): Owner user reference
- `PlannedStartDate` (date): Planned start date
- `PlannedEndDate` (date): Planned end date
- `PercentDoneByStoryCount` (float): Progress percentage

**States:** `Funnel` → `Review` → `Analysis` → `Backlog` → `Implementation` → `Done`

### Iteration Fields

- `Name` (str): Iteration name
- `StartDate` (date): Start date
- `EndDate` (date): End date
- `_ref` (str): Iteration reference URL

### Release Fields

- `Name` (str): Release name
- `ReleaseDate` (date): Release date
- `_ref` (str): Release reference URL

---

## SAFe Portfolio Item Workflow States

State names vary by portfolio item type. Pass state as a name string — the CLI automatically resolves names to Rally
`_ref` URLs.

### Initiatives

**Flow:** `Discovering → Developing → Measuring → Done`

| State | Meaning |
|-------|---------|
| Discovering | Defining the hypothesis and exploring the opportunity |
| Developing | Actively building the solution |
| Measuring | Validating outcomes against the hypothesis |
| Done | Delivered and outcomes confirmed |

### Epics, Capabilities, Features

**Flow:** `Funnel → Review → Analysis → Backlog → Implementation → Done`

| State | Meaning |
|-------|---------|
| Funnel | Initial idea — not yet committed |
| Review | Evaluating feasibility and business value |
| Analysis | Detailed scoping and design |
| Backlog | Ready for a PI, waiting to be pulled |
| Implementation | Active development in progress |
| Done | Delivered and accepted |

**Note:** Portfolio items use `State` field. Stories use `ScheduleState` field with different workflow:
`Idea → Defined → In-Progress → Completed → Accepted → Deployed`

---

## Query Syntax

```
# Exact match
(Field = "value")

# Contains
(Field contains "value")

# Not equal
(Field != "value")

# Null check
(Field = null)

# Nested field
(Owner.UserName = "john.doe")

# Compound AND
((Field1 = "a") AND (Field2 = "b"))

# Compound OR
((Field1 = "a") OR (Field1 = "b"))

# Multiple conditions
((Iteration.Name = "Sprint 1") AND (ScheduleState != "Accepted") AND (Owner.UserName = "john.doe"))
```

---

## Configuration

Configuration is stored in `~/.claude/aig.json`:

```json
{
  "rally": {
    "api_key": "<paste your Rally API key here>",
    "default_workspace": "Workspace Name",
    "last_project": {
      "workspace_name": "Workspace Name",
      "workspace_id": "12345",
      "workspace_ref": "https://rally1.rallydev.com/...",
      "project_name": "Project Name",
      "project_id": "67890",
      "project_ref": "https://rally1.rallydev.com/..."
    }
  }
}
```

---

## See Also

- [quick-start.md](quick-start.md) — Getting started in 5 minutes
- [examples.md](examples.md) — 50+ CLI examples by category
- [operations.md](operations.md) — Common operations quick reference
- [workflows.md](workflows.md) — Multi-step workflow patterns
- [update-safety.md](update-safety.md) — Approval workflow details
