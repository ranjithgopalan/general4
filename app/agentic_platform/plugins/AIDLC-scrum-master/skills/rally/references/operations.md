# Common Operations

Quick reference for common Rally CLI operations. For complete command reference, see [reference.md](reference.md).
For data integrity details including hierarchy validation, release/iteration alignment, and inheritance rules,
see [data-integrity.md](data-integrity.md).

---

## Hierarchy Validation

**CRITICAL: All work items must have complete parent hierarchy:**
- Task → Story → Feature → Capability → Initiative
- Story → Feature → Capability → Initiative
- Feature → Capability → Initiative

When creating stories or tasks, the CLI automatically validates the complete hierarchy by default.

### Validate Feature Hierarchy

```bash
rally validate_hierarchy /portfolioitem/feature/12345
```

### Validate Story Hierarchy

```bash
rally validate_story_hierarchy /hierarchicalrequirement/67890
```

### Create Story with Hierarchy Validation (Default)

```bash
# Hierarchy is validated automatically when creating stories
rally create_user_story \
  /portfolioitem/feature/12345 \
  "Implement OAuth2 login" \
  "developer" \
  "implement OAuth2 authentication" \
  "users can log in securely" \
  '["OAuth2 flow works with Google provider","Tokens are stored securely","Refresh tokens work correctly"]'
```

Output shows validation:
```
✓ Validated hierarchy: Story → F12345 → C12345 → I12345
```

---

## Query Portfolio Hierarchy

```bash
# Get capabilities under an epic
rally find_by_formatted_id E12345
rally get_children /portfolioitem/epic/12345 portfolioitem/capability

# Get features under a capability
rally get_children /portfolioitem/capability/12345 portfolioitem/feature

# Get stories under a feature
rally get_children /portfolioitem/feature/67890 hierarchicalrequirement
```

---

## Assign Owner

```bash
# Find story and user, then update owner
rally find_user_story US12345
rally find_user john.doe

# Assign owner (requires user approval)
rally update_object /hierarchicalrequirement/67890 '{"Owner":"/user/12345"}' true
```

---

## Update Iteration and Release

```bash
# Find iteration and release
rally find_iteration "2026.PI1.Iteration1"
rally find_release "2026.PI1"

# Update stories (requires user approval for each)
rally update_object /hierarchicalrequirement/67890 '{"Iteration":"/iteration/12345","Release":"/release/67890"}' true
```

---

## See Also

- [reference.md](reference.md) — Complete CLI command reference
- [examples.md](examples.md) — 50+ CLI examples by category
- [quick-start.md](quick-start.md) — Getting started in 5 minutes
- [workflows.md](workflows.md) — Multi-step workflow patterns
- [update-safety.md](update-safety.md) — Approval workflow details
- [data-integrity.md](data-integrity.md) — Data integrity rules
