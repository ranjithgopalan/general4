# Common Workflows

Multi-step workflow patterns for common Rally operations using the `rally` CLI.

---

## Complete Feature Setup

End-to-end workflow: create feature, stories, tasks, and validate.

```bash
# 1. Create feature under a capability
rally create_feature "User Authentication" /portfolioitem/capability/123 "OAuth 2.0 login"

# 2. Find the created feature (get its ref)
rally find_by_formatted_id F12345

# 3. Create stories under the feature
rally create_user_story \
  /portfolioitem/feature/12345 \
  "Login UI" \
  "user" \
  "see a login form" \
  "I can authenticate" \
  '["Show email field","Show password field","Show login button"]'

# 4. Find the created story
rally find_by_formatted_id US67890

# 5. Create tasks under the story
rally create_task /hierarchicalrequirement/67890 "Design login form" "Create mockups" "Design responsive login UI"
rally create_task /hierarchicalrequirement/67890 "Implement form" "Build component" "Create login form with validation"

# 6. Validate estimates
rally validate_story_estimate /hierarchicalrequirement/67890
rally validate_feature_estimates /portfolioitem/feature/12345
```

---

## Bulk Update Stories

Update multiple stories in a feature (e.g., assign to iteration).

```bash
# 1. Get all stories under a feature
rally get_children /portfolioitem/feature/12345 hierarchicalrequirement

# 2. Update each story's iteration (requires user approval per batch)
rally update_object /hierarchicalrequirement/11111 '{"Iteration":"/iteration/98765"}' false
rally update_object /hierarchicalrequirement/22222 '{"Iteration":"/iteration/98765"}' false
rally update_object /hierarchicalrequirement/33333 '{"Iteration":"/iteration/98765"}' false
```

---

## Generate Iteration Report

Query and export completed work for a sprint.

```bash
# 1. Get all completed stories in an iteration
rally query hierarchicalrequirement \
  '{"query":"((Iteration.Name = Sprint 23) AND (ScheduleState = Completed))"}' \
  'Name,FormattedID,Owner,PlanEstimate,Tasks' \
  null 200 true

# 2. Get iteration details
rally find_iteration "Sprint 23"

# 3. Get defects in the iteration
rally query defect \
  '{"query":"(Iteration.Name = Sprint 23)"}' \
  'Name,FormattedID,State,Priority'
```

---

## Sprint Planning Workflow

Set up work items for an upcoming sprint.

```bash
# 1. Find target iteration and release
rally find_iteration "2026.PI1.Iteration3"
rally find_release "2026.PI1"

# 2. Prepare iteration and auto-set release
rally prepare_iteration_and_release "2026.PI1.Iteration3" null true

# 3. Get candidate stories (defined, unscheduled)
rally query hierarchicalrequirement \
  '{"query":"((ScheduleState = Defined) AND (Iteration = null))"}' \
  'Name,FormattedID,PlanEstimate,Feature'

# 4. Assign stories to the iteration (requires user approval)
rally update_object /hierarchicalrequirement/11111 \
  '{"Iteration":"/iteration/12345","Release":"/release/67890"}' true

# 5. Validate estimates for assigned stories
rally validate_feature_estimates /portfolioitem/feature/12345
```

---

## Status Reporting Workflow

Generate a status report for stakeholders.

```bash
# 1. Get iteration progress
rally query hierarchicalrequirement \
  '{"query":"(Iteration.Name = 2026.PI1.Iteration3)"}' \
  'Name,FormattedID,ScheduleState,PlanEstimate,Owner'

# 2. Get feature progress
rally find_by_formatted_id F12345
rally get_children /portfolioitem/feature/12345 hierarchicalrequirement

# 3. Check for blocked items
rally get_user_stories '{"query":"(Blocked = true)"}'

# 4. Get defect summary
rally get_defects '{"query":"(Iteration.Name = 2026.PI1.Iteration3)"}'

# 5. Audit estimates for consistency
rally audit_initiative_estimates /portfolioitem/initiative/98765
```

---

## Data Consistency Audit

Check and fix data issues across a portfolio hierarchy.

```bash
# 1. Analyze a feature for data consistency issues
rally analyze_data_consistency /portfolioitem/feature/12345 true true

# 2. If issues found, review and fix task estimates
rally fix_task_estimates /hierarchicalrequirement/67890 true   # dry run first
rally fix_task_estimates /hierarchicalrequirement/67890         # then apply

# 3. Validate the entire feature
rally validate_feature_estimates /portfolioitem/feature/12345
rally validate_hierarchy /portfolioitem/feature/12345
```

---

## See Also

- [reference.md](reference.md) — Complete CLI command reference
- [examples.md](examples.md) — 50+ CLI examples by category
- [operations.md](operations.md) — Common operations quick reference
- [quick-start.md](quick-start.md) — Getting started in 5 minutes
- [update-safety.md](update-safety.md) — Approval workflow details
