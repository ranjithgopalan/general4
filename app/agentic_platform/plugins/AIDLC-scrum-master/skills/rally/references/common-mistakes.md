# Common Mistakes

This document lists common mistakes when working with the Rally API and how to avoid them.

**IMPORTANT:** Examples in this document show Python API code for illustration purposes only. **Always use the CLI commands** documented in `reference.md` for actual Rally operations. The Python API classes shown here are internal implementation details and should not be used directly.

---

## ❌ Forgetting Release/Iteration Inheritance

```python
# WRONG - story won't be in sprint
api.create_object('hierarchicalrequirement', {
    'Name': 'My Story',
    'PortfolioItem': feature['_ref']
})

# RIGHT - inherit from parent feature
feature = api.get_object(feature['_ref'], fetch='Release,Iteration')
api.create_object('hierarchicalrequirement', {
    'Name': 'My Story',
    'PortfolioItem': feature['_ref'],
    'Release': feature.get('Release', {}).get('_ref'),
    'Iteration': feature.get('Iteration', {}).get('_ref')
})
```

---

## ❌ Creating Orphan Stories

```python
# WRONG - story has no parent
api.create_object('hierarchicalrequirement', {'Name': 'My Story'})

# RIGHT - always specify parent feature
api.create_object('hierarchicalrequirement', {
    'Name': 'My Story',
    'PortfolioItem': feature['_ref']
})
```

---

## ❌ Non-Impact-Focused Story Titles

```python
# WRONG - user story format in title
'As a developer, I want to implement OAuth2 authentication so that users can securely log in'

# WRONG - action-focused, doesn't describe impact
'Implement OAuth2 authentication'
'Add login feature'

# RIGHT - impact-focused, describes what it enables
'Enable secure user authentication via OAuth2 with automatic token refresh'
'Authenticate users securely using Google OAuth2 with session management'
```

---

## ❌ Creating Features Without Administrative Tasks

```python
# WRONG - feature has no administrative tasks tracking
api.create_feature(name="My Feature", parent_ref=cap['_ref'])

# RIGHT - use helper that creates Administrative Tasks story
api.create_feature_with_stages(name="My Feature", parent_ref=cap['_ref'])
```

---

## ❌ Creating Duplicate Stories

```python
# WRONG - might create duplicate
api.create_object('hierarchicalrequirement', {'Name': 'OAuth implementation', ...})

# RIGHT - check first
validation = api.validate_before_create('hierarchicalrequirement', 'OAuth implementation', feature['_ref'])
if not validation['has_duplicate']:
    api.create_object('hierarchicalrequirement', {'Name': 'OAuth implementation', ...})
```

---

## ❌ Assigning to Unknown Users

```python
# WRONG - user might not exist or have wrong username
api.update_object(story['_ref'], {'Owner': '/user/12345'})

# RIGHT - check team.json first, use exact Rally username
# Location: .fe-sm-default/config/team.json (or .claude/.fe-sm/config/team.json if customized)
# Find: "John Smith" → Rally username: "john.smith@company.com"
```

---

## ❌ Story Points > 7

```python
# WRONG - too large, should be broken down
api.create_object('hierarchicalrequirement', {'Name': 'Build entire auth system', 'PlanEstimate': 13})

# RIGHT - break into smaller stories, each ≤ 7 points
```

---

## ❌ Tasks Without Estimates

```python
# WRONG - no estimate on task
api.create_object('task', {
    'Name': 'Implement feature',
    'WorkProduct': story['_ref']
})

# RIGHT - include Estimate (hours = points × 8)
api.create_object('task', {
    'Name': 'Implement feature',
    'WorkProduct': story['_ref'],
    'Estimate': 8  # 1 point = 8 hours
})
```

---

## ❌ Task Descriptions Without Goal

```python
# WRONG - vague, no goal
api.create_object('task', {
    'Name': 'Set up database',
    'Description': '<p>Set up the database.</p>',
    ...
})

# RIGHT - detailed description with Goal and Details
api.create_object('task', {
    'Name': 'Set up database',
    'Description': '''<h3>Goal</h3>
<p>Establish a reliable data persistence layer for the application.</p>

<h3>Details</h3>
<p>Set up PostgreSQL database with proper schema migrations. Configure connection pooling,
create initial tables for users and sessions, and set up backup procedures.</p>''',
    ...
})
```

---

## ❌ Task Hours Don't Match Story Points

```python
# WRONG - story is 3 points (24 hours) but tasks sum to 16 hours
story = api.create_object('hierarchicalrequirement', {'PlanEstimate': 3, ...})
api.create_object('task', {'Estimate': 8, 'WorkProduct': story['_ref'], ...})
api.create_object('task', {'Estimate': 8, 'WorkProduct': story['_ref'], ...})
# Total: 16 hours ≠ 24 hours (3 × 8) ✗

# RIGHT - tasks must sum to story points × 8
story = api.create_object('hierarchicalrequirement', {'PlanEstimate': 3, ...})
api.create_object('task', {'Estimate': 8, 'WorkProduct': story['_ref'], ...})
api.create_object('task', {'Estimate': 8, 'WorkProduct': story['_ref'], ...})
api.create_object('task', {'Estimate': 8, 'WorkProduct': story['_ref'], ...})
# Total: 24 hours = 24 hours (3 × 8) ✓
```

---

## ❌ Missing Description Template

```python
# WRONG - no structured description
api.create_object('hierarchicalrequirement', {
    'Name': 'Add login feature',
    'Description': 'Need to add login'
})

# RIGHT - use As a/I want/So that template with acceptance criteria
api.create_object('hierarchicalrequirement', {
    'Name': 'Add login feature',
    'Description': '''<p><strong>As a</strong> user,<br/>
<strong>I want</strong> to log in with my credentials,<br/>
<strong>So that</strong> I can access my account securely.</p>

<h3>Acceptance Criteria</h3>
<ul>
<li>Email/password form validates input</li>
<li>Invalid credentials show error message</li>
<li>Successful login redirects to dashboard</li>
</ul>'''
})
```

---

## ❌ Administrative Tasks Mixed with Development

```python
# WRONG - admin tasks scattered across development stories
api.create_object('hierarchicalrequirement', {
    'Name': 'Implement OAuth2',
    'PlanEstimate': 5
})
api.create_object('task', {'Name': 'Write OAuth code', 'Estimate': 32, ...})
api.create_object('task', {'Name': 'Schedule meeting with security team', 'Estimate': 8, ...})  # Admin task!

# RIGHT - separate admin tasks into their own story with 0 estimates
api.create_object('hierarchicalrequirement', {
    'Name': 'Implement OAuth2',
    'PlanEstimate': 4  # Only development work
})
api.create_object('task', {'Name': 'Write OAuth code', 'Estimate': 32, ...})

api.create_object('hierarchicalrequirement', {
    'Name': 'Administrative Tasks',
    'PlanEstimate': 0  # Admin = 0 points
})
api.create_object('task', {'Name': 'Schedule meeting with security team', 'Estimate': 0, ...})
```

---

## ❌ Stories Without Tasks

```python
# WRONG - story has no tasks
story = api.create_object('hierarchicalrequirement', {
    'Name': 'Implement feature',
    'PlanEstimate': 3,
    'PortfolioItem': feature['_ref']
})
# Story created but no tasks added - INVALID!

# RIGHT - always create at least 1 task for every story
story = api.create_object('hierarchicalrequirement', {
    'Name': 'Implement feature',
    'PlanEstimate': 3,
    'PortfolioItem': feature['_ref']
})
api.create_object('task', {
    'Name': 'Implement core functionality',
    'WorkProduct': story['_ref'],
    'Estimate': 24  # 3 points × 8 hours
})
```

---

## Pre-Creation Checklist

Before creating any Rally item, verify:

| Check                            | How                                                          |
| -------------------------------- | ------------------------------------------------------------ |
| ✓ Parent exists                 | `api.find_portfolio_item()` returns result                   |
| ✓ No duplicates                 | `api.validate_before_create()` passes                        |
| ✓ Owner is valid                | Username exists in team.json                                 |
| ✓ Title is impact-focused       | Describes what it enables/achieves, not just "Implement X"   |
| ✓ Description follows template  | Use templates from `.fe-sm-default/templates/`               |
| ✓ Points ≤ 7                    | For user stories only (see rules.md)                         |
| ✓ Release/Iteration inherited   | From parent feature                                          |
| ✓ **At least 1 task per story** | Every story must have at least 1 task (required)             |
| ✓ Tasks have estimates          | `Estimate` = hours (1 point = 8 hours)                       |
| ✓ Task hours = story points × 8 | Sum of task `Estimate` fields must equal `PlanEstimate × 8`  |
| ✓ Task descriptions detailed    | Include Goal and Details sections (see task template)        |
| ✓ Admin tasks grouped           | Non-code tasks go under "Administrative Tasks" story with 0 estimates |
