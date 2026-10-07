# Quick Start

Get up and running with the Rally CLI in 5 minutes.

---

## Check CLI Availability

```bash
# macOS/Linux
~/.claude/venvs/ADLC/bin/rally --help 2>/dev/null || echo "NOT_FOUND"

# Windows (try $USERPROFILE first, then ~ as fallback)
$USERPROFILE/.claude/venvs/ADLC/Scripts/rally --help 2>/dev/null || \
  ~/.claude/venvs/ADLC/Scripts/rally --help 2>/dev/null || echo "NOT_FOUND"
```

If `NOT_FOUND`, try activating the ADLC venv first before running setup:

```bash
# macOS/Linux — activate venv and retry
source ~/.claude/venvs/ADLC/bin/activate 2>/dev/null && rally --help 2>/dev/null || echo "STILL_NOT_FOUND"

# Windows — activate venv and retry
source ~/.claude/venvs/ADLC/Scripts/activate 2>/dev/null && rally --help 2>/dev/null || echo "STILL_NOT_FOUND"
```

If still `NOT_FOUND` after activation, run `/AIDLC-scrum-master:setup` to install and configure.

---

> **Windows:** The `rally` command may not be on your PATH. Replace `rally` with the full path in all commands
> below: `~/.claude/venvs/ADLC/Scripts/rally` (or `$USERPROFILE/.claude/venvs/ADLC/Scripts/rally` if `~`
> does not expand).

## Find a Rally Item

```bash
# Find any item by FormattedID (auto-detects type: US, F, C, E, TA, DE)
rally find_by_formatted_id US12345
rally find_by_formatted_id F67890
```

---

## Query Stories

```bash
# Stories in an iteration
rally get_user_stories '{"query":"(Iteration.Name = Sprint 23)"}'

# Stories under a feature
rally get_user_stories '{"query":"(Feature.FormattedID = F12345)"}'

# All features
rally get_features
```

---

## Create a Story

```bash
rally create_user_story \
  /portfolioitem/feature/12345 \
  "Implement OAuth2 login" \
  "user" \
  "log in with OAuth2" \
  "I can access the system securely" \
  '["OAuth2 provider is configured","Login flow redirects to OAuth2 provider","Tokens are stored securely"]'
```

---

## Update an Item

**Requires user approval** — see [update-safety.md](update-safety.md).

```bash
rally update_object /hierarchicalrequirement/67890 '{"PlanEstimate":5,"ScheduleState":"In-Progress"}' true
```

---

## Tips

### Shell Quoting

```bash
# Use single quotes for JSON arguments to avoid shell expansion
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
# Use the literal string "null" for null/None arguments
rally prepare_iteration_and_release "Sprint 23" null true
```

### Debug Mode

```bash
# Add --debug flag to see full error tracebacks
rally --debug get_features '{"query":"invalid query"}'
```

### Discover Available Commands

```bash
# List all available methods with signatures
rally --help
```

---

## See Also

- [reference.md](reference.md) — Complete CLI command reference
- [examples.md](examples.md) — 50+ CLI examples by category
- [operations.md](operations.md) — Common operations quick reference
- [workflows.md](workflows.md) — Multi-step workflow patterns
- [update-safety.md](update-safety.md) — Approval workflow details
