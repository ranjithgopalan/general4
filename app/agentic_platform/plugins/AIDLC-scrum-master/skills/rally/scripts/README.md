# Rally API CLI

Dynamic command-line interface for the Rally API client. Call any method on the `RallyAPI` class directly from the
terminal.

## Installation

### Option 1: Install globally with pip (Recommended)

Install the CLI as a system-wide command:

```bash
cd /Users/christopher.le/code/genlite-projects/genlite-aig-claude/plugins/AIDLC-scrum-master/skills/rally-api/scripts
pip install -e .
```

The `-e` flag installs in "editable" mode, so any updates to the code will be reflected immediately without
reinstalling.

After installation, the `rally` command will be available globally:

```bash
rally <method> [args...]
```

To uninstall:

```bash
pip uninstall rally-api-cli
```

### Option 2: Use the wrapper script directly

```bash
cd /path/to/scripts
./rally <method> [args...]
```

### Option 3: Add to PATH

Add this line to your `~/.bashrc` or `~/.zshrc`:

```bash
export PATH="/Users/christopher.le/code/genlite-projects/genlite-aig-claude/plugins/AIDLC-scrum-master/skills/rally-api/scripts:$PATH"
```

Then reload your shell and use:

```bash
rally <method> [args...]
```

### Option 4: Create an alias

Add to `~/.bashrc` or `~/.zshrc`:

```bash
alias rally="/Users/christopher.le/code/genlite-projects/genlite-aig-claude/plugins/AIDLC-scrum-master/skills/rally-api/scripts/rally"
```

## Prerequisites

- Python 3.8+
- Rally API credentials configured in `~/.claude/aig.json` (use `/AIDLC-scrum-master:setup-rally` to configure)
- Rally API Python package installed

## Usage

### Basic Syntax

```bash
rally <method_name> [arg1] [arg2] ...
```

Arguments are automatically parsed as:
- **JSON objects/arrays**: `'{"key":"value"}'` or `'["item1","item2"]'`
- **Booleans**: `true`, `false`, `yes`, `no`, `1`, `0`
- **Numbers**: `42`, `3.14`
- **null/None**: `null`, `None`
- **Strings**: Everything else

### Examples

#### List all methods

```bash
rally
```

Shows all available methods with their signatures and documentation.

#### Query methods

```bash
# Get workspaces
rally get_workspaces

# Get features with a query
rally get_features '{"query":"(State = In-Progress)"}'

# Find by FormattedID
rally find_by_formatted_id F12345

# Get object by reference
rally get_object /portfolioitem/feature/12345 'Name,FormattedID,State,Owner'
```

#### Create methods

```bash
# Create a feature
rally create_feature "My New Feature" /portfolioitem/capability/67890 "Feature description"

# Create a user story
rally create_user_story \
  /portfolioitem/feature/12345 \
  "User can login" \
  "user" \
  "login to the application" \
  "access my account" \
  '["User enters valid credentials","System authenticates","User is redirected to dashboard"]'

# Create a task
rally create_task \
  /hierarchicalrequirement/98765 \
  "Implement login UI" \
  "Create login form component" \
  "Build responsive login form with validation"
```

#### Update methods

```bash
# Update an object
rally update_object \
  /hierarchicalrequirement/98765 \
  '{"ScheduleState":"In-Progress","Owner":"/user/12345"}' \
  true

# Move a story to a different feature
rally move_story /hierarchicalrequirement/98765 /portfolioitem/feature/54321
```

#### Validation methods

```bash
# Validate story estimates
rally validate_story_estimate /hierarchicalrequirement/98765

# Validate feature estimates
rally validate_feature_estimates /portfolioitem/feature/12345

# Validate hierarchy
rally validate_hierarchy /portfolioitem/feature/12345
```

#### Search methods

```bash
# Search by name (exact match)
rally search_by_name hierarchicalrequirement "Login Feature" true

# Search by name (partial match)
rally search_by_name portfolioitem/feature "Authentication" false

# Find iteration
rally find_iteration "Sprint 23"

# Find release
rally find_release "Release 2.0"
```

#### Analysis methods

```bash
# Analyze data consistency
rally analyze_data_consistency /portfolioitem/feature/12345 true true

# Audit initiative estimates
rally audit_initiative_estimates /portfolioitem/initiative/98765
```

### Advanced Features

#### JSON Arguments

Use single quotes to pass JSON:

```bash
rally query hierarchicalrequirement '{"query":"(FormattedID = US12345)"}' 'Name,FormattedID,State'
```

#### Boolean Arguments

Use `true`/`false`, `yes`/`no`, or `1`/`0`:

```bash
rally get portfolioitem/feature '{"query":"(State = Funnel)"}' true
rally validate_story_estimate /hierarchicalrequirement/98765 false
```

#### Debug Mode

Add `--debug` to see full error tracebacks:

```bash
rally --debug get_features '{"query":"invalid"}'
```

### Method Discovery

To see what methods are available and their signatures:

```bash
rally
```

This will display all public methods on the `RallyAPI` class with:
- Method signature (parameters and types)
- Documentation summary
- Default values

Example output:
```
  find_by_formatted_id(formatted_id: str, raise_if_not_found: bool = True)
    → Find any Rally item by FormattedID. Automatically detects item type from prefix.

  create_feature(name: str, parent_ref: Optional = None, description: str = , state: str = Funnel)
    → Create a new feature.
```

## Tips

1. **Use shell completion**: Most shells support tab completion for file paths and commands
2. **Quote JSON carefully**: Use single quotes for JSON to avoid shell expansion
3. **Check method signatures**: Run `rally` without arguments to see available methods
4. **Use --debug for troubleshooting**: Add `--debug` flag to see full error details
5. **Start simple**: Test with read-only methods like `get_workspaces` before making changes

## Troubleshooting

### "Module not found" error

Ensure you're in the correct directory or the Python package is in your PYTHONPATH:

```bash
cd /Users/christopher.le/code/genlite-projects/genlite-aig-claude/plugins/AIDLC-scrum-master/skills/rally-api/scripts
export PYTHONPATH="$PWD:$PYTHONPATH"
```

### "401 Unauthorized" error

Configure your Rally API credentials:

```bash
# Using Claude Code skill
/AIDLC-scrum-master:setup-rally

# Or manually edit ~/.claude/aig.json
```

### "Method not found" error

Check available methods with:

```bash
rally
```

The CLI will also suggest similar method names if you mistype.

## Implementation Details

The CLI uses Python's `inspect` module and `getattr()` to dynamically invoke any public method on the `RallyAPI`
class. This means:

- No need to manually define CLI commands for each method
- Automatically supports new methods added to the client
- Type conversion is automatic (JSON, bool, number, string)
- Method signatures are introspected from the source code

This makes the CLI future-proof and maintainable as the API client evolves.
