# Rally CLI Examples

50+ examples organized by category. All examples use the `rally` CLI directly.

For command signatures and parameter details, see [reference.md](reference.md).

---

## Table of Contents

- [Query Operations](#query-operations)
- [Find Operations](#find-operations)
- [Create Operations](#create-operations)
- [Update Operations](#update-operations)
- [Bulk Operations](#bulk-operations)
- [Portfolio Hierarchy](#portfolio-hierarchy)
- [Iteration & Release](#iteration--release)
- [Task Management](#task-management)
- [Validation](#validation)
- [Data Consistency](#data-consistency)
- [Clone & Backup](#clone--backup)
- [Advanced Queries](#advanced-queries)
- [Tips & Best Practices](#tips--best-practices)

---

## Query Operations

### Query Stories by Iteration

```bash
rally get_user_stories '{"query":"(Iteration.Name = 2026.PI1.Iteration1)"}'
```

### Query Stories by Feature

```bash
# Find feature first, then query stories
rally find_by_formatted_id F12345
rally get_user_stories '{"query":"(Feature.FormattedID = F12345)"}'

# Or query by Feature ObjectID for exact match
rally query hierarchicalrequirement \
  '{"query":"(Feature.ObjectID = 12345)"}' \
  'FormattedID,Name,PlanEstimate,ScheduleState,Owner'
```

### Query All Stories in an Epic

```bash
# 1. Get capabilities under the epic
rally get_children /portfolioitem/epic/12345 portfolioitem/capability

# 2. Get features under each capability
rally get_children /portfolioitem/capability/11111 portfolioitem/feature

# 3. Get stories under each feature
rally get_children /portfolioitem/feature/22222 hierarchicalrequirement
```

### Query Tasks for a Story

```bash
rally find_by_formatted_id US12345
rally get_children /hierarchicalrequirement/67890 task
```

### Query with Specific Fields

```bash
rally query hierarchicalrequirement \
  '{"query":"(Iteration.Name = 2026.PI1.Iteration1)"}' \
  'FormattedID,Name,Owner,PlanEstimate,ScheduleState,Tasks,Feature'
```

### Query by Owner

```bash
rally get_user_stories '{"query":"(Owner.UserName = john.doe@example.com)"}'
```

### Query by State

```bash
rally get_features '{"query":"(State = In-Progress)"}'
rally get_user_stories '{"query":"(ScheduleState = Completed)"}'
rally get_defects '{"query":"(Priority = High Attention)"}'
```

### Query Blocked Items

```bash
rally get_user_stories '{"query":"(Blocked = true)"}'
```

### Query Stories with No Tasks

```bash
rally get_user_stories '{"query":"(Tasks.Count = 0)"}'
```

---

## Find Operations

### Find by Formatted ID (Any Type)

```bash
# Auto-detects type from prefix
rally find_by_formatted_id US12345
rally find_by_formatted_id F12345
rally find_by_formatted_id C12345
rally find_by_formatted_id E12345
rally find_by_formatted_id TA12345
rally find_by_formatted_id DE12345
```

### Find Type-Specific Items

```bash
rally find_feature F12345
rally find_user_story US12345
rally find_task TA12345
rally find_defect DE12345
rally find_portfolio_item C12345 capability
rally find_portfolio_item E12345 epic
rally find_portfolio_item I12345 initiative
```

### Find User by Username

```bash
rally find_user john.doe@example.com
```

### Find Iteration and Release

```bash
rally find_iteration "2026.PI1.Iteration1"
rally find_release "2026.PI1"
```

### Search by Name

```bash
# Exact match
rally search_by_name hierarchicalrequirement "Login Feature" true

# Partial/contains match
rally search_by_name portfolioitem/feature "Authentication" false
```

---

## Create Operations

### Create User Story

```bash
# Standard story with acceptance criteria
rally create_user_story \
  /portfolioitem/feature/12345 \
  "Implement OAuth2 login" \
  "developer" \
  "implement OAuth2 authentication" \
  "users can log in securely" \
  '["OAuth2 provider is configured","Login flow redirects correctly","Tokens are stored securely","Refresh tokens work"]'
```

### Create Story with Custom State

```bash
rally create_user_story \
  /portfolioitem/feature/12345 \
  "Add password reset flow" \
  "user" \
  "reset my password" \
  "I can regain access if I forget my password" \
  '["User can request reset via email","Reset link expires after 24 hours","New password set successfully"]' \
  "In-Progress"
```

### Create Task

```bash
# Basic task
rally create_task \
  /hierarchicalrequirement/67890 \
  "Implement OAuth2 integration" \
  "Integrate with OAuth2 provider" \
  "Build authentication middleware"

# Task with owner and estimate
rally create_task \
  /hierarchicalrequirement/67890 \
  "Design login form" \
  "Create mockups" \
  "Design responsive login UI with validation" \
  "Defined" \
  /user/12345 \
  8
```

### Create Multiple Tasks for a Story

```bash
# Create tasks that sum to story_points × 8 hours
rally create_task /hierarchicalrequirement/67890 "Design OAuth2 flow" "Create design docs" "Architecture and flow design"
rally create_task /hierarchicalrequirement/67890 "Implement OAuth2" "Build integration" "Code the OAuth2 middleware"
rally create_task /hierarchicalrequirement/67890 "Write unit tests" "Test coverage" "Jest tests for OAuth2 flow"
```

### Create Feature

```bash
rally create_feature \
  "User Authentication System" \
  /portfolioitem/capability/12345 \
  "Implement secure user authentication with OAuth2 and 2FA support" \
  "Backlog"
```

### Create Portfolio Hierarchy

```bash
# Top-down creation
rally create_theme "Digital Transformation" "Strategic initiative for modernization"
rally create_initiative "Cloud Migration" /portfolioitem/theme/12345 "Migrate services to AWS"
rally create_epic "User Authentication" /portfolioitem/initiative/67890 "Implement OAuth 2.0"
rally create_capability "Login System" /portfolioitem/epic/11111 "Multi-factor authentication"
rally create_feature "Password Reset" /portfolioitem/capability/22222 "Self-service password reset"
```

### Create Defect

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

### Estimate Story Points

```bash
# AI-assisted estimation (recommended)
rally estimate_user_story \
  "Implement OAuth 2.0 authentication" \
  "Add OAuth authentication with Google and GitHub providers" \
  '["Support Google OAuth","Support GitHub OAuth","Store tokens securely"]' \
  null true chris

# Manual complexity factors
rally estimate_user_story \
  "Implement OAuth 2.0" \
  "Add OAuth authentication" \
  null \
  '{"technical_complexity":4,"integration_requirements":3,"risk_uncertainty":2}' \
  false chris
```

---

## Update Operations

**All updates require user approval.** See [update-safety.md](update-safety.md).

### Update Story Points

```bash
rally update_object /hierarchicalrequirement/67890 '{"PlanEstimate":5}' true
```

### Update Story State

```bash
rally update_object /hierarchicalrequirement/67890 '{"ScheduleState":"In-Progress"}' true
```

### Assign Owner to Story

```bash
# Find user first
rally find_user john.doe@example.com

# Then assign (use the user's _ref from the find result)
rally update_object /hierarchicalrequirement/67890 '{"Owner":"/user/12345"}' true
```

### Update Feature State and Owner

```bash
rally update_object /portfolioitem/feature/12345 '{"State":"Implementing","Owner":"/user/98765"}' true
```

### Update Iteration and Release

```bash
# Find iteration and release first
rally find_iteration "2026.PI1.Iteration1"
rally find_release "2026.PI1"

# Then update the story
rally update_object /hierarchicalrequirement/67890 \
  '{"Iteration":"/iteration/12345","Release":"/release/67890"}' true
```

### Update Task State and Hours

```bash
rally update_object /task/11111 '{"State":"In-Progress","ToDo":4.0}' true
```

### Move Story to Different Feature

```bash
rally move_story /hierarchicalrequirement/67890 /portfolioitem/feature/54321

# Move and inherit release/iteration from new feature
rally move_story /hierarchicalrequirement/67890 /portfolioitem/feature/54321 true
```

---

## Bulk Operations

### Bulk Create Stories

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

### Bulk Update Stories (Iteration Assignment)

```bash
# Get stories under a feature
rally get_children /portfolioitem/feature/12345 hierarchicalrequirement

# Update each story (requires user approval)
rally update_object /hierarchicalrequirement/11111 '{"Iteration":"/iteration/98765","Release":"/release/55555"}' true
rally update_object /hierarchicalrequirement/22222 '{"Iteration":"/iteration/98765","Release":"/release/55555"}' true
rally update_object /hierarchicalrequirement/33333 '{"Iteration":"/iteration/98765","Release":"/release/55555"}' true
```

### Bulk Assign Owner

```bash
# Assign multiple stories to the same owner
rally update_object /hierarchicalrequirement/11111 '{"Owner":"/user/12345"}' true
rally update_object /hierarchicalrequirement/22222 '{"Owner":"/user/12345"}' true
rally update_object /hierarchicalrequirement/33333 '{"Owner":"/user/12345"}' true
```

### Bulk Assign with Different Owners

```bash
# Each story to a different owner
rally update_object /hierarchicalrequirement/11111 '{"Owner":"/user/12345"}' true
rally update_object /hierarchicalrequirement/22222 '{"Owner":"/user/67890"}' true
rally update_object /hierarchicalrequirement/33333 '{"Owner":"/user/11111"}' true
```

---

## Portfolio Hierarchy

### Get Complete Hierarchy from Epic

```bash
# 1. Find the epic
rally find_by_formatted_id E12345

# 2. Get capabilities under epic
rally get_children /portfolioitem/epic/12345 portfolioitem/capability

# 3. Get features under each capability
rally get_children /portfolioitem/capability/11111 portfolioitem/feature
rally get_children /portfolioitem/capability/22222 portfolioitem/feature

# 4. Get stories under each feature
rally get_children /portfolioitem/feature/33333 hierarchicalrequirement
rally get_children /portfolioitem/feature/44444 hierarchicalrequirement
```

### Get Feature with Story Summary

```bash
# Get feature details
rally find_by_formatted_id F12345

# Get all stories under the feature with key fields
rally query hierarchicalrequirement \
  '{"query":"(Feature.FormattedID = F12345)"}' \
  'FormattedID,Name,PlanEstimate,ScheduleState,Owner,Tasks'
```

### Get Object with Specific Fields

```bash
rally get_object /portfolioitem/feature/12345 'Name,FormattedID,State,Owner,PlannedEndDate,PercentDoneByStoryCount'
rally get_object /hierarchicalrequirement/67890 'Name,FormattedID,ScheduleState,PlanEstimate,Owner,Feature,Iteration'
```

---

## Iteration & Release

### Get All Stories in Iteration

```bash
rally query hierarchicalrequirement \
  '{"query":"(Iteration.Name = 2026.PI1.Iteration1)"}' \
  'FormattedID,Name,PlanEstimate,ScheduleState,Owner,Feature,Tasks'
```

### Move Stories to Different Iteration

```bash
# Find target iteration
rally find_iteration "2026.PI1.Iteration2"

# Move stories (requires user approval)
rally update_object /hierarchicalrequirement/11111 '{"Iteration":"/iteration/98765"}' true
rally update_object /hierarchicalrequirement/22222 '{"Iteration":"/iteration/98765"}' true
```

### Prepare Iteration and Release

```bash
# Auto-set release based on iteration dates
rally prepare_iteration_and_release "2026.PI1.Iteration1" null true

# Specify both iteration and release
rally prepare_iteration_and_release "2026.PI1.Iteration1" "2026.PI1" false
```

### Find All Iterations with Context

```bash
rally find_all_iterations_with_context "2026.PI1"
```

---

## Task Management

### Validate Story Estimates

```bash
# Validate that task hours = story points × 8
rally validate_story_estimate /hierarchicalrequirement/67890

# Skip stage stories in validation
rally validate_story_estimate /hierarchicalrequirement/67890 true
```

### Fix Task Estimates

```bash
# Preview fixes (dry run)
rally fix_task_estimates /hierarchicalrequirement/67890 true

# Apply fixes (requires user approval)
rally fix_task_estimates /hierarchicalrequirement/67890
```

### Validate All Stories in a Feature

```bash
rally validate_feature_estimates /portfolioitem/feature/12345
rally validate_feature_estimates /portfolioitem/feature/12345 true
```

### Audit Initiative Estimates

```bash
rally audit_initiative_estimates /portfolioitem/initiative/98765
rally audit_initiative_estimates /portfolioitem/initiative/98765 false
```

### Get Expected Task Hours

```bash
rally get_expected_task_estimate /hierarchicalrequirement/67890
```

---

## Validation

### Validate Hierarchy

```bash
# Validate feature has complete parent chain (Feature → Capability → Initiative)
rally validate_hierarchy /portfolioitem/feature/12345

# Validate story hierarchy (Story → Feature → Capability → Initiative)
rally validate_story_hierarchy /hierarchicalrequirement/67890
```

### Validate Before Create

```bash
# Check for duplicates and validate parent before creating
rally validate_before_create hierarchicalrequirement "New Story" /portfolioitem/feature/12345
```

---

## Data Consistency

### Analyze Feature for Issues

```bash
# Check feature and its children/parent
rally analyze_data_consistency /portfolioitem/feature/12345 true true

# Check only the item (no children/parent)
rally analyze_data_consistency /portfolioitem/feature/12345 false false
```

**What it checks:**
1. PlannedEndDate exists but Release/Iteration missing or misaligned
2. Iteration exists but Release missing
3. Release/Iteration exist but PlannedEndDate missing
4. Parent has PlannedEndDate but children don't (or vice versa)
5. Children have Release/Iteration but parent doesn't

### Fix Task Estimates

```bash
# Dry run first
rally fix_task_estimates /hierarchicalrequirement/67890 true

# Then apply
rally fix_task_estimates /hierarchicalrequirement/67890
```

---

## Clone & Backup

### Clone Items

```bash
# Clone a story
rally clone_item /hierarchicalrequirement/67890

# Clone with new name
rally clone_item /hierarchicalrequirement/67890 "Copy of Login Feature"

# Clone to different parent
rally clone_item /hierarchicalrequirement/67890 "New Story" /portfolioitem/feature/54321

# Clone feature with children
rally clone_item /portfolioitem/feature/12345 "Copy of Feature" null true
```

### Restore from Backup

```bash
# Restore to original location
rally restore_from_backup /path/to/backup/feature_12345.json

# Restore to different parent
rally restore_from_backup /path/to/backup/feature_12345.json /portfolioitem/capability/98765

# Restore without attachments
rally restore_from_backup /path/to/backup/feature_12345.json null false
```

### Delete with Backup

```bash
# Delete with automatic backup (default)
rally delete_object /task/11111

# Delete without backup
rally delete_object /task/11111 false
```

---

## Advanced Queries

### Compound Filters

```bash
# AND conditions
rally query hierarchicalrequirement \
  '{"query":"((Iteration.Name = Sprint 23) AND (ScheduleState = Completed))"}' \
  'Name,FormattedID,ScheduleState'

# OR conditions
rally query hierarchicalrequirement \
  '{"query":"((FormattedID = US123) OR (FormattedID = US456))"}' \
  'Name,FormattedID'

# Multiple AND
rally query hierarchicalrequirement \
  '{"query":"((Iteration.Name = Sprint 1) AND (ScheduleState != Accepted) AND (Owner.UserName = john.doe))"}' \
  'Name,FormattedID,ScheduleState'
```

### Comparison Operators

```bash
# Greater than
rally query hierarchicalrequirement \
  '{"query":"(PlanEstimate > 13)"}' \
  'Name,FormattedID,PlanEstimate'

# Date comparison
rally get_features '{"query":"(PlannedEndDate >= 2024-01-01)"}'

# Null check
rally query hierarchicalrequirement \
  '{"query":"(Iteration = null)"}' \
  'Name,FormattedID'
```

### Ordering and Pagination

```bash
# Order by creation date descending
rally query portfolioitem/feature \
  '{"query":"(State = Funnel)"}' \
  'Name,FormattedID' \
  'CreationDate desc' 200 true

# Limit to first 50 results
rally query hierarchicalrequirement \
  '{"query":"(ScheduleState = Defined)"}' \
  'Name,FormattedID' \
  null 50 true
```

### Low-Level API Calls

```bash
# Direct GET
rally get portfolioitem/feature '{"query":"(State = Funnel)"}' true

# Direct POST (create)
rally post hierarchicalrequirement '{"Name":"New Story","Feature":"/portfolioitem/feature/12345"}' true

# Generic query with all parameters
rally query hierarchicalrequirement \
  '{"query":"(FormattedID = US12345)"}' \
  'Name,FormattedID,State' \
  null 200 true
```

---

## Tips & Best Practices

### Shell Quoting

```bash
# Use single quotes for JSON to avoid shell expansion
rally get_features '{"query":"(State = In-Progress)"}'

# Use double quotes for simple string arguments
rally create_feature "My Feature" /portfolioitem/capability/123 "Description"
```

### Boolean Values

```bash
# All equivalent for true: true, yes, 1
rally validate_feature_estimates /portfolioitem/feature/123 true

# All equivalent for false: false, no, 0
rally validate_feature_estimates /portfolioitem/feature/123 false
```

### Null Values

```bash
# Use literal "null" for null/None arguments
rally prepare_iteration_and_release "Sprint 23" null true
rally clone_item /portfolioitem/feature/12345 "Copy" null true
```

### Debug Mode

```bash
# Add --debug before the command name for full tracebacks
rally --debug get_features '{"query":"invalid query"}'
```

### Discover Commands

```bash
# List all available methods with signatures
rally
```

### Workspace and Project

```bash
# Set workspace context
rally set_workspace /workspace/12345678

# Set project context
rally set_project /project/98765432 "My Project"
```

### Attachments

```bash
# Get attachments for an item
rally get_attachments /portfolioitem/feature/12345

# Upload attachment
rally upload_attachment \
  /portfolioitem/feature/12345 \
  "design.html" \
  "<html><body>Design document</body></html>" \
  "text/html" \
  "Feature design document"
```

---

## See Also

- [reference.md](reference.md) — Complete CLI command reference
- [quick-start.md](quick-start.md) — Getting started in 5 minutes
- [operations.md](operations.md) — Common operations quick reference
- [workflows.md](workflows.md) — Multi-step workflow patterns
- [update-safety.md](update-safety.md) — Approval workflow details
