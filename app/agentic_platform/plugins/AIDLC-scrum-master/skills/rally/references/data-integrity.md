# Rally Data Integrity Reference

Complete reference for maintaining data integrity in Rally, including hierarchy validation, release/iteration alignment, parent-child relationships, and required fields.

---

## Table of Contents

- [Overview](#overview)
- [Hierarchy Requirements](#hierarchy-requirements)
- [Release and Iteration Alignment](#release-and-iteration-alignment)
- [Parent-Child Inheritance](#parent-child-inheritance)
- [Required Fields](#required-fields)
- [Data Consistency Checks](#data-consistency-checks)
- [Automatic Enforcement](#automatic-enforcement)
- [Best Practices](#best-practices)

---

## Overview

Rally data integrity ensures:
- **Complete hierarchies** - All work items have proper parent chains
- **Aligned releases/iterations** - Dates and assignments are consistent
- **Proper inheritance** - Children inherit attributes from parents
- **Required fields** - Critical fields are populated
- **Consistency** - Parent and child data stays synchronized

**Why this matters:**
- Enables accurate portfolio roll-up and reporting
- Prevents orphaned or disconnected work items
- Maintains proper dependency tracking
- Supports sprint planning and capacity management
- Ensures reliable velocity and burn-down metrics

---

## Hierarchy Requirements

### Complete Parent Chains (MANDATORY)

Every work item MUST have a complete parent chain up to Initiative:

```
Initiative (I#####)
  └── Capability (C#####)
        └── Feature (F#####)
              └── User Story (US#####)
                    └── Task (TA#####)
```

### Required Hierarchies by Type

| Work Item Type | Required Parent Chain |
|----------------|----------------------|
| **Task**       | Task → Story → Feature → Capability → Initiative |
| **Story**      | Story → Feature → Capability → Initiative |
| **Feature**    | Feature → Capability → Initiative |
| **Capability** | Capability → Initiative |
| **Initiative** | Initiative (no parent required) |

### Automatic Validation

The API automatically validates hierarchies when creating stories or tasks:

```python
from rally_api import RallyAPI

api = RallyAPI()

# Create story - validates Feature → Capability → Initiative
story = api.create_user_story(
    feature_ref=feature["_ref"],
    name="Implement OAuth2 login",
    role="developer",
    want="implement OAuth2 authentication",
    so_that="users can log in securely",
    acceptance_criteria=["..."],
    validate_hierarchy=True  # Default: True
)
# Output: ✓ Validated hierarchy: Story → F12345 → C12345 → I12345
```

### Manual Validation

Check hierarchy completeness before creation:

```python
# Validate feature hierarchy
hierarchy = api.validate_hierarchy(feature["_ref"])
# Returns: {
#   "feature": {"id": "F12345", "name": "...", "ref": "..."},
#   "capability": {"id": "C12345", "name": "...", "ref": "..."},
#   "initiative": {"id": "I12345", "name": "...", "ref": "..."},
#   "complete": True
# }

# Validate story hierarchy
hierarchy = api.validate_story_hierarchy(story["_ref"])
# Returns complete chain including story
```

### Common Hierarchy Violations

**❌ Orphaned Feature (No Parent Capability)**
```
Feature F12345 has no parent Capability.
Features must be linked to a Capability, which must be linked to an Initiative.
```

**❌ Orphaned Story (No Parent Feature)**
```
Story US12345 has no parent Feature.
Stories must be linked to a Feature → Capability → Initiative.
```

**❌ Incomplete Capability Chain (No Parent Initiative)**
```
Capability C12345 has no parent Initiative.
The hierarchy must be: Story → Feature → Capability → Initiative.
```

---

## Release and Iteration Alignment

### Fundamental Rules

1. **Iteration must fall within Release date range**
   - Iteration.StartDate ≥ Release.ReleaseStartDate
   - Iteration.EndDate ≤ Release.ReleaseDate

2. **Stories inherit Release/Iteration from Feature**
   - When creating stories, inherit from parent by default
   - When moving stories, update Release/Iteration to match new parent

3. **PlannedEndDate should align with Iteration/Release**
   - If Iteration assigned, PlannedEndDate should match Iteration.EndDate
   - If only Release assigned, PlannedEndDate should match Release.ReleaseDate

### Automatic Release/Iteration Handling

```python
# Auto-find release matching iteration dates
fields = api.prepare_iteration_and_release(
    iteration_name="2026.PI1.Iteration1",
    auto_set_release=True  # Default: True
)
# Returns: {"Iteration": "...", "Release": "..."} (auto-matched)

# Manual specification
fields = api.prepare_iteration_and_release(
    iteration_name="2026.PI1.Iteration1",
    release_name="2026.PI1",
    auto_set_release=False
)
```

### Inheritance from Parent

**Stories inherit from Feature:**
```python
story = api.create_user_story(
    feature_ref=feature["_ref"],
    name="...",
    role="...",
    want="...",
    so_that="...",
    acceptance_criteria=["..."],
    inherit_from_parent=True  # Default: True
)
# Automatically inherits:
# - Owner (if feature has owner)
# - Release (if feature has release)
```

**Bulk stories inherit from Feature:**
```python
stories = api.create_bulk_stories(
    feature_ref=feature["_ref"],
    stories=[...],
    inherit_from_feature=True  # Default: True
)
# All stories inherit Release, Iteration, Owner from feature
```

**Moving stories updates Release/Iteration:**
```python
api.move_story_to_feature(
    story_ref=story["_ref"],
    new_feature_ref=new_feature["_ref"],
    inherit_release_iteration=True  # Default: False
)
# Story now has new parent's Release and Iteration
```

### Common Alignment Issues

**Issue 1: Iteration without Release**
```python
# Story has Iteration but no Release
{
  "issue": "Item has Iteration assigned but no Release",
  "suggestion": "Set Release to '2026.PI1' (matches Iteration dates)"
}
```

**Issue 2: PlannedEndDate without Iteration**
```python
# Story has PlannedEndDate but no Iteration
{
  "issue": "Item has PlannedEndDate (2026-01-26) but no Iteration assigned",
  "suggestion": "Set Iteration to '2026.PI1.Iteration1' (matches PlannedEndDate)"
}
```

**Issue 3: Iteration/Release without PlannedEndDate**
```python
# Story has Iteration/Release but no PlannedEndDate
{
  "issue": "Item has Release/Iteration assigned but no PlannedEndDate",
  "suggestion": "Set PlannedEndDate to Iteration end date (2026-01-26)"
}
```

---

## Parent-Child Inheritance

### Inheritance Flow

```
Feature (F#####)
  ├─ Owner → Stories inherit owner
  ├─ Release → Stories inherit release
  └─ Iteration → Stories inherit iteration (optional)

Story (US#####)
  ├─ Owner → Tasks inherit owner (optional)
  ├─ Release → Tasks inherit release (optional)
  ├─ Iteration → Tasks inherit iteration (optional)
  └─ PlanEstimate → Tasks auto-estimate from story points
```

### Owner Assignment Rules

**From Project Settings (settings.json):**
```json
{
  "validation_rules": {
    "require_owner_assignment": true
  }
}
```

**Inheritance Patterns:**

1. **Explicit owner wins** - If story data specifies owner, use it
2. **Inherit from parent** - Otherwise, inherit from feature owner
3. **No owner** - Allow unassigned if explicitly set to None

```python
# Explicit owner (highest priority)
story = api.create_user_story(
    feature_ref=feature["_ref"],
    name="...",
    role="...",
    want="...",
    so_that="...",
    acceptance_criteria=["..."]
)
# In story data, provide: {"owner": user_ref}

# Inherit from feature (default when inherit_from_parent=True)
story = api.create_user_story(
    feature_ref=feature["_ref"],
    name="...",
    role="...",
    want="...",
    so_that="...",
    acceptance_criteria=["..."],
    inherit_from_parent=True  # Gets feature's owner
)

# Unassigned (no owner)
story = api.create_user_story(
    feature_ref=feature["_ref"],
    name="...",
    role="...",
    want="...",
    so_that="...",
    acceptance_criteria=["..."],
    inherit_from_parent=False
)
# No owner set
```

### Task Estimation from Story

Tasks auto-calculate estimates from story Plan Estimate:

```python
# Story has 3 points, task auto-estimates 24 hours (3 * 8)
task = api.create_task(
    story_ref=story["_ref"],
    name="Implement OAuth2 flow",
    goal="Implement OAuth2 authentication",
    details="Set up OAuth2 provider, handle token refresh",
    auto_estimate=True  # Default: True
)
# Task.Estimate = Story.PlanEstimate * 8
```

**Manual override:**
```python
task = api.create_task(
    story_ref=story["_ref"],
    name="...",
    goal="...",
    details="...",
    estimate=16.0  # Explicit estimate wins
)
```

### Parent-Child Synchronization

When parent Release/Iteration changes, children should be updated:

```python
# Update feature's release
api.update_object(feature["_ref"], {"Release": new_release["_ref"]})

# Find and update all child stories
stories = api.query(
    "hierarchicalrequirement",
    query=f'(Feature.ObjectID = {feature["ObjectID"]})',
    fetch="FormattedID,Name,_ref"
)

for story in stories:
    api.update_object(story["_ref"], {"Release": new_release["_ref"]})
```

**Automatic consistency check detects this:**
```python
# Analyze parent
analysis = api.analyze_data_consistency(feature["_ref"], check_children=True)

# Detects if children have different Release than parent
{
  "issue": "Parent has Release '2026.PI1' but 5 children have '2026.PI2'",
  "suggestion": "Update children to match parent Release"
}
```

---

## Required Fields

### Validation Rules

From project settings (`settings.json`):

```json
{
  "validation_rules": {
    "require_acceptance_criteria": true,
    "require_tasks_per_story": true,
    "require_owner_assignment": true,
    "require_html_descriptions": true,
    "max_story_points": 7
  }
}
```

### Story Requirements

**1. Acceptance Criteria (require_acceptance_criteria: true)**
```python
# ❌ WRONG - No acceptance criteria
story = api.create_user_story(
    feature_ref=feature["_ref"],
    name="Implement login",
    role="user",
    want="log in",
    so_that="I can access the system",
    acceptance_criteria=[]  # Empty - violates rule
)

# ✅ CORRECT - At least one criterion
story = api.create_user_story(
    feature_ref=feature["_ref"],
    name="Implement login",
    role="user",
    want="log in",
    so_that="I can access the system",
    acceptance_criteria=[
        "User can log in with email and password",
        "Invalid credentials show error message",
        "Successful login redirects to dashboard"
    ]
)
```

**2. Tasks per Story (require_tasks_per_story: true)**
```python
# After creating story, must create at least one task
story = api.create_user_story(...)

# Create required task
task = api.create_task(
    story_ref=story["_ref"],
    name="Implement OAuth2 flow",
    goal="Set up OAuth2 authentication",
    details="Configure provider, handle callbacks, store tokens"
)
```

**3. Owner Assignment (require_owner_assignment: true)**
```python
# ❌ WRONG - No owner
story = api.create_user_story(
    feature_ref=feature["_ref"],
    name="...",
    role="...",
    want="...",
    so_that="...",
    acceptance_criteria=["..."],
    inherit_from_parent=False  # No owner inherited
)
# Violates rule if feature also has no owner

# ✅ CORRECT - Has owner (inherited or explicit)
story = api.create_user_story(
    feature_ref=feature["_ref"],
    name="...",
    role="...",
    want="...",
    so_that="...",
    acceptance_criteria=["..."],
    inherit_from_parent=True  # Inherits from feature
)
```

**4. HTML Descriptions (require_html_descriptions: true)**
```python
# Automatically enforced - templates generate HTML
story = api.create_user_story(...)
# Description is rendered from template as HTML:
# <h3>User Story</h3>
# <p>As a {role}, I want {want} so that {so_that}.</p>
# <h4>Acceptance Criteria</h4>
# <ul><li>...</li></ul>
```

**5. Story Points Limit (max_story_points: 7)**
```python
# ❌ WRONG - Too many points
story_data = {
    "name": "Rewrite entire authentication system",
    "role": "developer",
    "want": "rewrite authentication",
    "so_that": "it's more secure",
    "acceptance_criteria": ["..."],
    "plan_estimate": 13  # Exceeds max_story_points: 7
}

# ✅ CORRECT - Break into smaller stories
story1 = api.create_user_story(
    feature_ref=feature["_ref"],
    name="Implement OAuth2 provider setup",
    role="developer",
    want="set up OAuth2 provider",
    so_that="users can authenticate",
    acceptance_criteria=["..."]
)  # 3 points

story2 = api.create_user_story(
    feature_ref=feature["_ref"],
    name="Implement token refresh flow",
    role="developer",
    want="handle token refresh",
    so_that="sessions stay active",
    acceptance_criteria=["..."]
)  # 2 points
```

---

## Data Consistency Checks

### Automatic Analysis

The `analyze_data_consistency()` method detects issues:

```python
from rally_api import RallyAPI
import json

api = RallyAPI()
feature = api.find_by_formatted_id("F12345")

# Analyze for consistency issues
analysis = api.analyze_data_consistency(
    feature["_ref"],
    check_children=True,  # Check child stories
    check_parent=True     # Check parent capability
)

print(json.dumps(analysis, indent=2))
```

### What Gets Checked

**1. PlannedEndDate ↔ Release/Iteration Alignment**
- Item has PlannedEndDate but missing Iteration → suggest matching iteration
- Item has Iteration but missing Release → suggest matching release
- Item has Release/Iteration but missing PlannedEndDate → suggest date

**2. Parent ↔ Child Consistency**
- Parent has PlannedEndDate but children don't → suggest copying to children
- Children have PlannedEndDate but parent doesn't → suggest using earliest child date
- Parent has Release/Iteration but children don't → suggest copying to children
- Children have Release/Iteration but parent doesn't → suggest using most common child value

**3. Cross-Hierarchy Checks**
- Validate complete parent chain exists
- Ensure Release date ranges encompass Iteration dates
- Check for orphaned work items

### Analysis Output Format

```json
{
  "has_issues": true,
  "item": {
    "id": "F12345",
    "name": "User Authentication",
    "type": "Feature"
  },
  "issues": [
    "Item has PlannedEndDate (2026-01-26) but no Iteration assigned",
    "Item has Iteration assigned but no Release",
    "3 children have PlannedEndDate but this item doesn't"
  ],
  "suggestions": [
    {
      "description": "Set Iteration to '2026.PI1.Iteration1' (matches PlannedEndDate)",
      "action": "set_iteration",
      "data": {
        "item_ref": "...",
        "iteration_name": "2026.PI1.Iteration1",
        "iteration_ref": "..."
      }
    },
    {
      "description": "Set Release to '2026.PI1' (matches Iteration dates)",
      "action": "set_release",
      "data": {
        "item_ref": "...",
        "release_name": "2026.PI1",
        "release_ref": "..."
      }
    }
  ]
}
```

### Applying Fixes

```python
# Apply suggested fixes (creates automatic backups)
result = api.fill_missing_data(
    feature["_ref"],
    analysis["suggestions"],
    apply_all=True
)

# Output:
{
  "status": "success",
  "applied": [
    "Set Iteration to 2026.PI1.Iteration1",
    "Set Release to 2026.PI1",
    "Set PlannedEndDate to 2026-01-26"
  ],
  "failed": [],
  "backups": ["backup_12345.json"]
}
```

---

## Automatic Enforcement

### Creation-Time Validation (Default)

The API automatically enforces data integrity during creation:

```python
# Story creation validates:
# 1. Hierarchy completeness (Feature → Capability → Initiative)
# 2. Parent exists and is accessible
story = api.create_user_story(
    feature_ref=feature["_ref"],
    name="...",
    role="...",
    want="...",
    so_that="...",
    acceptance_criteria=["..."],
    validate_hierarchy=True  # Default: True
)

# Task creation validates:
# 1. Hierarchy completeness (Story → Feature → Capability → Initiative)
# 2. Parent story exists
task = api.create_task(
    story_ref=story["_ref"],
    name="...",
    goal="...",
    details="...",
    validate_hierarchy=True  # Default: True
)
```

### Inheritance Defaults

```python
# Stories inherit from feature by default
story = api.create_user_story(
    feature_ref=feature["_ref"],
    name="...",
    role="...",
    want="...",
    so_that="...",
    acceptance_criteria=["..."],
    inherit_from_parent=True  # Default: True
)
# Inherits: Owner, Release

# Tasks auto-estimate from story by default
task = api.create_task(
    story_ref=story["_ref"],
    name="...",
    goal="...",
    details="...",
    auto_estimate=True  # Default: True
)
# Estimate = Story.PlanEstimate * 8
```

### Backup Before Updates

From project settings (`settings.json`):

```json
{
  "workflow": {
    "require_backup_before_update": true
  }
}
```

When enabled, all updates create automatic backups:

```python
# Automatic backup created before update
api.update_object(
    story["_ref"],
    {"Release": new_release["_ref"]},
    backup=True  # Default: True from settings
)
# Backup saved to: .claude/.fe-sm/backups/backup_US12345_20260127_142530.json
```

---

## Best Practices

### 1. Always Validate Hierarchies

```python
# Before creating work items, validate parent hierarchy
hierarchy = api.validate_hierarchy(feature["_ref"])
if hierarchy["complete"]:
    # Safe to create stories
    story = api.create_user_story(feature_ref=feature["_ref"], ...)
```

### 2. Use Inheritance by Default

```python
# Let stories inherit from features
story = api.create_user_story(
    feature_ref=feature["_ref"],
    name="...",
    role="...",
    want="...",
    so_that="...",
    acceptance_criteria=["..."],
    inherit_from_parent=True  # Recommended
)
```

### 3. Check Consistency Regularly

```python
# Before sprint planning
features = api.query("portfolioitem/feature", ...)
for feature in features:
    analysis = api.analyze_data_consistency(
        feature["_ref"],
        check_children=True
    )
    if analysis["has_issues"]:
        # Present to user and offer fixes
        ...
```

### 4. Keep Release/Iteration Aligned

```python
# Use auto-matching for release
fields = api.prepare_iteration_and_release(
    iteration_name="2026.PI1.Iteration1",
    auto_set_release=True  # Recommended
)
```

### 5. Synchronize Parent-Child Changes

```python
# When updating parent Release, update children too
api.update_object(feature["_ref"], {"Release": new_release["_ref"]})

# Update all child stories
stories = api.query(
    "hierarchicalrequirement",
    query=f'(Feature.ObjectID = {feature["ObjectID"]})',
    fetch="_ref"
)
for story in stories:
    api.update_object(story["_ref"], {"Release": new_release["_ref"]})
```

### 6. Use Backups for Risky Updates

```python
# Always backup before bulk updates
for story in stories:
    api.update_object(
        story["_ref"],
        {"Iteration": new_iteration["_ref"]},
        backup=True  # Create backup before each update
    )
```

### 7. Validate After Bulk Operations

```python
# After bulk story creation
for story_ref in created_stories:
    hierarchy = api.validate_story_hierarchy(story_ref)
    analysis = api.analyze_data_consistency(story_ref)

    if not hierarchy["complete"] or analysis["has_issues"]:
        # Log and fix issues
        ...
```

---

## Summary

### Core Principles

1. **Complete Hierarchies** - Every work item must have a parent chain to Initiative
2. **Aligned Dates** - Release, Iteration, and PlannedEndDate must be consistent
3. **Proper Inheritance** - Children inherit attributes from parents
4. **Required Fields** - Critical fields must be populated
5. **Consistent Data** - Parent and child data stays synchronized

### Automatic Enforcement

- ✅ Hierarchy validation on creation (default: enabled)
- ✅ Inheritance from parent (default: enabled)
- ✅ Auto-estimate tasks from stories (default: enabled)
- ✅ Auto-match Release to Iteration (default: enabled)
- ✅ Backups before updates (default: enabled)

### When to Check

- Before sprint planning or PI planning
- After bulk creation or updates
- After moving stories between features
- When users report data inconsistencies
- Regularly as part of scrum master workflow

### Related Documentation

- **operations.md** - Hierarchy validation examples
- **workflow-guide.md** - Data consistency check workflow
- **reference.md** - Complete API method documentation
- **common-mistakes.md** - Hierarchy and inheritance mistakes to avoid

---

*This document is part of the AIDLC-scrum-master Rally API skill (v1.4.4)*
