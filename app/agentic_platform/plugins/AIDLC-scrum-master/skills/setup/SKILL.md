---
name: setup
description: Setup wizard for AIDLC-scrum-master plugin - installs Rally CLI in shared Python venv and configures API credentials, workspace, and portfolios with interactive prompts
license: Proprietary
compatibility: Requires Python 3.8+ and pip. Creates shared venv at ~/.claude/venvs/ADLC/ and stores config in ~/.claude/aig.json
metadata:
  author: ADLC Scrum Master Team
  version: "1.1.0"
  organization: AIG
  plugin: AIDLC-scrum-master
allowed-tools: Bash Read Write AskUserQuestion
---

## Setup AIDLC-scrum-master Plugin

This skill helps you set up the AIDLC-scrum-master plugin by:
1. Installing the Rally CLI in a shared ADLC Python virtual environment (`~/.claude/venvs/ADLC/`)
2. Collecting Rally API credentials (API key, workspace, portfolio information)

## Configuration Flow

Follow these steps to collect Rally configuration and install the CLI.

### Step 1: Ask for Rally API Key

**First, check if config exists:**

Read `~/.claude/aig.json` to check if `rally.api_key` already exists.

**If API key exists:**

Display current value (masked) and ask if user wants to keep it or change it:

```javascript
{
  "questions": [{
    "question": "Rally API key is already configured. What would you like to do?",
    "header": "API Key",
    "options": [
      { "label": "Keep existing key", "description": "Continue using _***...*** (current key)" },
      { "label": "Change key", "description": "Enter a new API key" }
    ],
    "multiSelect": false
  }]
}
```

If user selects "Keep existing key", skip to Step 2 with the existing API key.

**If API key does NOT exist or user wants to change it:**

Use AskUserQuestion to ask the user for their Rally API key:

```javascript
{
  "questions": [{
    "question": "Please enter your Rally API key (starts with _ like _abc123...)",
    "header": "API Key",
    "options": [
      { "label": "I have my key", "description": "Enter your API key in the 'Other' field below" },
      { "label": "I need help", "description": "Show me how to get an API key" }
    ],
    "multiSelect": false
  }]
}
```

Use AskUserQuestion to ask the user for their Rally API key:

```javascript
{
  "questions": [{
    "question": "Please enter your Rally API key (starts with _ like _abc123...)",
    "header": "API Key",
    "options": [
      { "label": "I have my key", "description": "Enter your API key in the 'Other' field below" },
      { "label": "I need help", "description": "Show me how to get an API key" }
    ],
    "multiSelect": false
  }]
}
```

**If User Needs Help Getting API Key:**

Display these instructions:

```
To get your Rally API key:
1. Visit: https://rally1.rallydev.com/login
2. Log in with your credentials
3. Go to: https://rally1.rallydev.com/#/api_key
4. Click 'Create New API Key'
5. Copy the generated key (starts with underscore, like _abc123...)

Once you have your API key, run /AIDLC-scrum-master:setup again.
```

### Step 2: Ask for Default Workspace

**Check if workspace exists:**

Read `~/.claude/aig.json` to check if `rally.default_workspace` already exists.

**If workspace exists:**

Ask if user wants to keep it or change it:

```javascript
{
  "questions": [{
    "question": "Default workspace is already configured. What would you like to do?",
    "header": "Workspace",
    "options": [
      { "label": "Keep existing", "description": "Continue using '<current-workspace-name>'" },
      { "label": "Change workspace", "description": "Enter a new workspace name" }
    ],
    "multiSelect": false
  }]
}
```

If user selects "Keep existing", skip to Step 3 with the existing workspace.

**If workspace does NOT exist or user wants to change it:**

Ask for the default Rally workspace:

```javascript
{
  "questions": [{
    "question": "What is your default Rally workspace name?",
    "header": "Workspace",
    "options": [
      { "label": "Enter workspace", "description": "Enter your workspace name in the 'Other' field (e.g., 'General Insurance Workspace')" },
      { "label": "I don't know", "description": "Show me how to find my workspace" }
    ],
    "multiSelect": false
  }]
}
```

**If User Needs Help Finding Workspace:**

```
To find your Rally workspace:
1. Visit: https://rally1.rallydev.com/
2. Log in with your credentials
3. Click your profile icon (top right)
4. Look for "Workspace:" in the dropdown
5. Copy the exact workspace name

Common workspace names:
- "General Insurance Workspace"
- "Commercial Insurance Workspace"
- "Technology Workspace"
```

### Step 3: Ask for Default Portfolio

**Check if portfolio exists:**

Read `~/.claude/aig.json` to check if `rally.default_portfolio` already exists.

**If portfolio exists:**

Ask if user wants to keep it or change it:

```javascript
{
  "questions": [{
    "question": "Default portfolio is already configured. What would you like to do?",
    "header": "Portfolio",
    "options": [
      { "label": "Keep existing", "description": "Continue using '<current-portfolio-name>'" },
      { "label": "Change portfolio", "description": "Select a new portfolio" }
    ],
    "multiSelect": false
  }]
}
```

If user selects "Keep existing", skip to Step 4 with the existing portfolio.

**If portfolio does NOT exist or user wants to change it:**

Ask for the default portfolio:

```javascript
{
  "questions": [{
    "question": "What is your default portfolio name?",
    "header": "Portfolio",
    "options": [
      { "label": "forward engineering", "description": "Forward Engineering portfolio" },
      { "label": "reverse engineering", "description": "Reverse Engineering portfolio" },
      { "label": "genlite", "description": "GenLite portfolio" }
    ],
    "multiSelect": false
  }]
}
```

**Note:** User can also enter a custom portfolio name using the "Other" option.

### Step 4: Collect Portfolio IDs

After receiving the default portfolio, ask for portfolio IDs in JSON format:

```javascript
{
  "questions": [{
    "question": "Enter portfolio IDs as JSON (e.g., {\"forward engineering\": \"123456\", \"genlite\": \"789012\"})",
    "header": "Portfolio IDs",
    "options": [
      { "label": "I have the IDs", "description": "Enter JSON mapping in the 'Other' field" },
      { "label": "Use default IDs", "description": "Use standard Forward Engineering portfolio IDs" },
      { "label": "I need help", "description": "Show me how to find portfolio IDs" }
    ],
    "multiSelect": false
  }]
}
```

**Default Portfolio IDs (if user selects "Use default IDs"):**

```json
{
  "forward engineering": "839039831411",
  "reverse engineering": "839039833045",
  "genlite": "826846225869"
}
```

**If User Needs Help Finding Portfolio IDs:**

```
To find Rally portfolio IDs:
1. Visit: https://rally1.rallydev.com/
2. Navigate to your portfolio
3. Look at the URL: https://rally1.rallydev.com/#/portfolioitem?PORTFOLIO_ID
4. Copy the numeric ID from the URL

Alternatively:
- Use the Rally CLI: rally get_portfolios
- Ask your Scrum Master or Rally administrator
```

### Step 5: Save Configuration

After collecting all information, save to `~/.claude/aig.json`:

```json
{
  "rally": {
    "api_key": "<user-provided-api-key>",
    "default_portfolio": "<user-provided-default-portfolio>",
    "default_workspace": "<user-provided-workspace>",
    "portfolio_ids": {
      "<portfolio-name-1>": "<portfolio-id-1>",
      "<portfolio-name-2>": "<portfolio-id-2>",
      "<portfolio-name-3>": "<portfolio-id-3>"
    }
  }
}
```

**Use the Write tool to create/update the file at:** `~/.claude/aig.json`

### Step 6: Install Rally CLI

After saving configuration, ensure the Rally CLI is installed in a dedicated Python virtual environment.

**Check if venv exists:**

Use Bash to check if `~/.claude/venvs/ADLC/` exists:
```bash
if [ -d ~/.claude/venvs/ADLC ]; then echo "EXISTS"; else echo "NOT_FOUND"; fi
```

**If venv does NOT exist:**

1. Display installation message:
```
🔧 Installing Rally CLI...
Creating shared ADLC Python virtual environment at ~/.claude/venvs/ADLC/
```

2. Create the venv (cross-platform):
```bash
python -m venv ~/.claude/venvs/ADLC
```

3. Get the plugin root path (use the Claude Code environment variable or detect from skill path)

4. Install Rally CLI in editable mode:
```bash
# Windows
~/.claude/venvs/ADLC/Scripts/pip install -e <plugin-root>/skills/rally/scripts

# macOS/Linux
~/.claude/venvs/ADLC/bin/pip install -e <plugin-root>/skills/rally/scripts
```

5. Verify installation by testing the rally command:
```bash
# Windows
~/.claude/venvs/ADLC/Scripts/rally --help

# macOS/Linux
~/.claude/venvs/ADLC/bin/rally --help
```

6. Display success message:
```
✅ Rally CLI installed successfully at ~/.claude/venvs/ADLC/

The 'rally' command is now available via:
- Windows: ~/.claude/venvs/ADLC/Scripts/rally
- macOS/Linux: ~/.claude/venvs/ADLC/bin/rally
```

**If venv already exists:**

Display message:
```
✅ Shared ADLC venv already exists at ~/.claude/venvs/ADLC/
Installing Rally CLI into existing venv...
```

Then proceed with installation (steps 4-5) to add Rally CLI to the existing venv.

**If installation fails:**

Display error with troubleshooting steps:
```
❌ Rally CLI installation failed

Troubleshooting:
1. Ensure Python 3.8+ is installed: python --version
2. Ensure pip is available: python -m pip --version
3. Check permissions for ~/.claude/ directory
4. Try manual installation:
   python -m venv ~/.claude/venvs/ADLC
   ~/.claude/venvs/ADLC/Scripts/pip install -e <plugin-path>/skills/rally/scripts

For help, visit: https://github.aig.net/christle/genlite-aig-claude
```

**Note:** Even if CLI installation fails, the configuration in `~/.claude/aig.json` is still valid and saved. The user can retry installation later or install manually.

### Step 7: Confirmation

Display success message with configuration summary:

```
✅ Rally setup complete!

Rally CLI:
- Installed at: ~/.claude/venvs/ADLC/
- Command: ~/.claude/venvs/ADLC/Scripts/rally (Windows) or ~/.claude/venvs/ADLC/bin/rally (macOS/Linux)

Configuration:
- API Key: _***...*** (hidden)
- Default Workspace: <workspace-name>
- Default Portfolio: <portfolio-name>
- Portfolio IDs: <count> portfolios configured
- Config file: ~/.claude/aig.json

Next steps:
- Use natural language to query Rally: "Show me stories for F12345"
- Create work items: "Create a story under F12345 called 'Implement login'"
- Run /AIDLC-scrum-master:rally for advanced CLI operations
- Test CLI directly: ~/.claude/venvs/ADLC/Scripts/rally get_iterations
```

---

## Example Configuration

```json
{
  "rally": {
    "api_key": "_EXAMPLE-RALLY-API-KEY-REPLACE-ME",
    "default_portfolio": "forward engineering",
    "default_workspace": "General Insurance Workspace",
    "portfolio_ids": {
      "forward engineering": "839039831411",
      "reverse engineering": "839039833045",
      "genlite": "826846225869"
    }
  }
}
```

---

OPERATIONAL_CONSTRAINTS[Type,Rules]:
  ALWAYS,"check for existing config in ~/.claude/aig.json before asking for credentials | offer to keep or change existing values for API key, workspace, and portfolio | collect all configuration in order (API key → workspace → portfolio → portfolio IDs → save config → install CLI) | use AskUserQuestion for each credential step | save complete configuration to ~/.claude/aig.json BEFORE installing CLI | install Rally CLI AFTER saving config (Step 6) | use shared ADLC venv at ~/.claude/venvs/ADLC/ (not plugin-specific venv) | check if venv exists before creating | if venv exists, install Rally CLI into existing venv | use pip install -e for editable installation | verify CLI installation with --help flag | confirm successful setup with CLI path and config summary | hide API key in output (show only first/last chars)"
  NEVER,"expose the full API key in output | save invalid/empty values | skip user confirmation | modify existing portfolios without user consent | proceed to next step if previous credential step failed | fail the entire setup if CLI installation fails (config is still valid) | create plugin-specific venv (always use shared ~/.claude/venvs/ADLC/)"
