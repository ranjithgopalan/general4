# Product Owner/Scrum Master Plugin

AI-powered Rally integration for sprint planning, story creation, and team workflow automation.

## Overview

This plugin provides natural language Rally integration for Product Owners and Scrum Masters. Query work items, create stories, manage sprints, and track progress using conversational commands.

**📖 [Complete User Guide](docs/AIDLC-scrum-master-user-guide.md)** - Installation, usage, examples, and customization

## Quick Start

1. **Install and configure:**
   ```bash
   /AIDLC-scrum-master:setup
   ```

2. **Use natural language:**
   ```
   "Show me stories for F12345"
   "Create a 5-point story under F12345 called 'Implement OAuth login'"
   "Assign all unassigned stories to chris"
   ```

See the [User Guide](docs/AIDLC-scrum-master-user-guide.md) for complete documentation.

## Directory Structure

```
AIDLC-scrum-master/
├── .claude-plugin/
│   └── plugin.json                    # Plugin manifest
├── skills/
│   ├── rally/                         # Rally API integration skill
│   │   ├── SKILL.md                   # Skill definition and CLI reference
│   │   ├── references/                # API documentation and examples
│   │   └── scripts/                   # Rally CLI implementation
│   │       ├── rally_api/             # Python Rally API client
│   │       └── __tests__/             # Unit tests
│   └── setup/                         # Setup wizard skill
│       └── SKILL.md                   # Setup wizard definition
├── .fe-sm-default/                    # Default configurations
│   ├── config/
│   │   ├── rules.md                   # Story point rules, validation, conventions
│   │   ├── settings.json              # Estimation settings, validation rules
│   │   └── team.json                  # Team member template
│   └── templates/                     # Default work item templates
│       ├── story.md                   # User story template
│       ├── task.md                    # Task template
│       ├── epic.md, capability.md, feature.md
│       └── defect.md, administrative-tasks.md
├── hook-handlers/                     # Claude Code lifecycle hooks
│   └── session_start.js               # Session initialization
├── docs/
│   └── AIDLC-scrum-master-user-guide.md  # Complete user documentation
├── CHANGELOG.md                       # Version history
└── README.md                          # This file
```

## Project-Level Configuration

When you use the plugin, it creates project-specific configuration in your codebase:

```
.claude/
└── rally/
    ├── team.json              # Team members with estimate multipliers
    ├── settings.json          # Project-specific settings (optional)
    └── rules.md               # Project-specific rules (optional)
    └── backups/               # Automatic backups of Rally changes

.claude/.fe-sm-templates/      # Custom templates (optional)
    ├── story.md
    ├── task.md
    └── ...
```

## Key Features

### Zero-Config Setup
- Automatic detection when Rally CLI is not installed
- Auto-invokes setup wizard when needed
- One-time configuration stored securely
- No manual intervention required for subsequent uses

### Natural Language Interface
- Query work items: "Show me stories for F12345"
- Create stories: "Create a 5-point story under F116904 called 'OAuth integration'"
- Manage sprints: "Move all telemetry stories to iteration 2026.PI1.Iteration2"
- Update status: "Mark US773876 as completed"

### Automated Story Point Estimation
- Three-factor complexity analysis (technical, integration, risk)
- Fibonacci mapping with detailed rationale
- Team member velocity multipliers
- Stores rationale in Rally Notes field for transparency

### Safety & Backup
- **User Approval Required**: ALL Rally updates require explicit user approval via interactive prompt
- **Preview Changes**: Shows current vs new values before any update
- **No Automatic Updates**: Never modifies Rally without user confirmation
- **Automatic Backups**: All UPDATE/DELETE operations backed up to `.claude/rally/backups/`
- **One-Command Restore**: Easy restoration from backup if needed
- **Read-Only Operations**: Query and validation operations don't require approval

### Customization
- **Templates**: Customize HTML format for all work item types
- **Rules**: Adjust story point thresholds and complexity weights
- **Team Config**: Track team members with estimate multipliers
- **Validation**: Configure required fields and max story points

### BRD/TDD Integration
- Parse Business Requirements Documents into Rally hierarchy
- Extract user stories from Technical Design Documents
- Auto-generate acceptance criteria and estimates
- Create complete Epic → Capability → Feature → Story structures

See the [User Guide](docs/AIDLC-scrum-master-user-guide.md) for complete feature documentation.

## Configuration Files

### User-Level (Created by setup wizard)
- `~/.claude/aig.json` - Rally API credentials and workspace
- `~/.claude/venvs/ADLC/` - Shared Python environment with Rally CLI

### Project-Level (Created as needed)
- `.claude/rally/team.json` - Team members and estimate multipliers
- `.claude/rally/settings.json` - Story point settings and validation rules (optional)
- `.claude/rally/rules.md` - Story point calculation rules and conventions (optional)
- `.claude/.fe-sm-templates/` - Custom work item templates (optional)

## Rally CLI

The Rally CLI is automatically installed by the setup wizard:

**Location:**
- Windows (Git Bash): `~/.claude/venvs/ADLC/Scripts/rally`
- macOS/Linux: `~/.claude/venvs/ADLC/bin/rally`

**Auto-Setup:**
When you use the rally skill without having the CLI installed, it automatically detects the missing CLI and invokes the setup wizard. You don't need to manually run setup unless you want to reconfigure.

**CLI Reference:**
```bash
rally get_features
rally find_by_formatted_id F12345
rally create_user_story <feature-ref> "Story name" "user" "want" "benefit" '["criteria"]'
```

See `skills/rally/SKILL.md` for complete CLI command reference.

## Development

### Running Tests
```bash
cd skills/rally/scripts
pip install -r requirements.txt
pip install -r __tests__/requirements.txt
pytest __tests__/ -v
```

### Test Coverage
```bash
pytest __tests__/ --cov=rally_api --cov-report=html
```

## Documentation

- **[User Guide](docs/AIDLC-scrum-master-user-guide.md)** - Complete usage documentation
- **[CLI Reference](skills/rally/SKILL.md)** - Rally CLI command reference
- **[Change Log](CHANGELOG.md)** - Version history

## Version

**Current Version:** 1.1.0
**Last Updated:** February 16, 2026
