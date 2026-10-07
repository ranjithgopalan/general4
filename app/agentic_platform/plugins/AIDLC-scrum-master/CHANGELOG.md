# AIDLC-scrum-master Changelog

All notable changes to the AIDLC-scrum-master (Forward Engineering Scrum Master) plugin will be documented in this
file.

## **1.1.1** - Portfolio Item State Fix & CLI-First Constraint (2026-02-18)

### Fixed

- **Portfolio item State field**: `create_initiative`, `create_epic`, `create_capability`, `create_feature`, and
  `create_theme` now automatically resolve state names (e.g. `"Funnel"`, `"Discovering"`) to Rally `_ref` URLs before
  creating the object. Rally's API requires a reference URL, not a plain string — passing a string caused
  `RallyValidationError` on every portfolio item create operation.
- **Initiative default state**: Changed default from `"Funnel"` (invalid for Initiatives) to `"Discovering"`, which is
  the correct initial state in the Initiative workflow (`Discovering → Developing → Measuring → Done`).

### Added

- **`_resolve_state_ref` method**: Internal helper on `RallyAPI` that queries `PortfolioItemState` by type name,
  caches results per session, and returns the `_ref` URL for a given state name. Accepts existing URLs unchanged.
- **CLI-First Constraint in SKILL.md**: Explicit rule requiring all Rally operations to go through the `rally` CLI
  command. Prohibits falling back to direct Python imports or inline `python -c` scripts when a CLI command fails.
- **`--help` flag support in CLI**: `rally --help` and `rally <method> --help` now print usage/method docs cleanly.
  `rally` with no args exits with code 1; `rally --help` exits with code 0.

### Documentation

- Updated `references/reference.md` SAFe Portfolio Item Workflow States section to document Initiative states
  (`Discovering / Developing / Measuring / Done`) separately from Epic/Capability/Feature states
  (`Funnel → Review → Analysis → Backlog → Implementation → Done`).
- Updated `references/quick-start.md` and SKILL.md Windows path guidance: always use full path
  `~/.claude/venvs/ADLC/Scripts/rally` for every rally command.

## **1.1.0** - Auto-Setup & Rally Skill Doc Fixes (2026-02-16)

### Features

- **Auto-Setup Detection**: Rally skill now automatically detects when Rally CLI is not installed
  - Checks for Rally CLI availability before executing any rally commands
  - Automatically invokes the setup wizard if CLI is not found
  - Provides clear user feedback during the setup process
  - Continues with rally commands only after successful setup
  - Zero-config experience for new users

### Fixed

- Fixed Rally skill reference docs to use CLI syntax instead of deprecated Python API heredoc patterns
- Fixed SKILL.md bloat (812 → 136 lines) by extracting content into reference files
- Fixed reference.md, examples.md, operations.md, and quick-start.md to use `rally` CLI commands
- Fixed missing workflow docs by extracting workflows.md and update-safety.md from SKILL.md
- Fixed approval template to use hierarchical tree display with nested parent → children support

### Documentation

- Updated rally skill reference with prerequisites check section
- Added auto-setup feature to README Key Features section
- Updated Rally CLI section with auto-setup information

## **1.0.0** - Production Release (2026-02-10)

### Major Release

This is the first production-ready release of the Product Owner/Scrum Master Plugin, featuring comprehensive Rally integration, natural language queries, and automated workflow management.

### Documentation

- **Complete User Guide**: Added comprehensive 130+ page user guide with categorized sections:
  - Getting Started (Quick Start, Installation, Basic Usage, Safety Features)
  - Configuration & Customization (Templates, Rules & Settings, Team Management)
  - Planning & Estimation (BRD to Planning, TDD to Stories with auto-estimation)
  - Sprint Management (Story Progress, Capacity Planning, Sprint Rebalancing)
  - Advanced Topics (Smart name resolution, hierarchy validation, backup/restore)
  - Real-World Examples (5 detailed scenarios from actual AI COE sprints)

- **README Overhaul**: Simplified README to focus on directory structure and technical reference
  - Clear directory structure with file descriptions
  - Configuration file locations (user-level vs project-level)
  - Rally CLI reference and location
  - Removed personal directory references for portability

### Features

- **Natural Language Interface**: Query and modify Rally using conversational commands
  - "Show me stories for F12345"
  - "Create a 5-point story under F116904 called 'OAuth integration'"
  - "Move all telemetry stories to iteration 2026.PI1.Iteration2"

- **Story Point Estimation**: Three-factor complexity analysis with detailed rationale
  - Technical complexity, integration requirements, risk/uncertainty (1-5 scale)
  - Fibonacci mapping with customizable thresholds
  - Team member velocity multipliers
  - Rationale stored in Rally Notes field for transparency

- **Safety & Backup**: Multi-layer protection for all Rally operations
  - Preview before every change (tree structure visualization)
  - Automatic backups for UPDATE/DELETE operations
  - Explicit confirmation required for all changes
  - One-command restore from backup

- **Customization System**: Project-specific configuration
  - **Templates**: HTML work item templates (story, task, epic, capability, feature, defect)
  - **Rules**: Story point thresholds, complexity weights, writing guidelines
  - **Settings**: Validation rules, max points, hours per point
  - **Team Config**: Team members with estimate multipliers

- **BRD/TDD Integration**: Transform documents into Rally hierarchies
  - Parse Business Requirements Documents into Epic → Capability → Feature structures
  - Extract user stories from Technical Design Documents
  - Auto-generate acceptance criteria and estimates
  - Interactive refinement workflow

### Testing

- **Comprehensive Test Suite**: 195 unit tests across 8 modules with 100% passing status
  - Backup system (21 tests)
  - Configuration management (22 tests)
  - Template customization (6 tests)
  - Exception handling (28 tests)
  - HTML/date parsing (37 tests)
  - Path resolution (20 tests)
  - Team workflows (16 tests)
  - Template loading (45 tests)

### Configuration

- **Setup Wizard**: Interactive `/AIDLC-scrum-master:setup` command
  - Installs Rally CLI in shared Python venv (`~/.claude/venvs/ADLC/`)
  - Collects Rally API key and workspace configuration
  - Saves to `~/.claude/aig.json`

- **Project-Level Configuration**: Optional customization per codebase
  - `.claude/rally/team.json` - Team members and velocity multipliers
  - `.claude/rally/settings.json` - Story point settings and validation rules
  - `.claude/rally/rules.md` - Estimation rules and conventions
  - `.claude/.fe-sm-templates/` - Custom work item templates

### Breaking Changes

- Plugin renamed from "FE-SM" to "Product Owner/Scrum Master Plugin" in documentation
- Command prefix changed from `/fesm-*` to `/AIDLC-scrum-master:*`
- Directory structure updated: `skills/rally-api/` → `skills/rally/`

### Migration Guide

- Run `/AIDLC-scrum-master:setup` to reconfigure Rally credentials
- Update any scripts referencing `/fesm-*` commands to use `/AIDLC-scrum-master:*`
- Custom templates in `.claude/.fe-sm-templates/` remain compatible

---

## **0.9.6** - Story Point Estimation Rationale (2026-02-09)

### Added

- **US789694**: Story point estimation rationale generation for transparency and team education
  - Added detailed rationale with complexity factor breakdown, key drivers (2-3 bullets), assumptions (1-2 bullets), and recommendations (Accept/Adjust/Decompose)
  - Enhanced CLI output during story generation to display rationale in "📝 Rationale:" section
  - Auto-populate Rally story Notes field with markdown-formatted rationale when auto-estimation is enabled
  - Added 11 new unit tests for rationale generation logic
  - Added integration test for end-to-end Notes field population

### Changed

- Enhanced `EstimationResult` dataclass with `key_drivers`, `assumptions`, and `rationale_action` fields
- Updated `format_estimation_result()` to include rationale section in CLI output
- Modified `bulk_create_stories()` to generate and store rationale during auto-estimation
- Enhanced estimation logging to show recommendation and primary key driver

---

## **0.9.5-dev.2** - Plugin Manifest and Installation Fix (2026-02-06)

### Bug Fixes

- Fixed plugin.json path references to match actual directory structure
- Updated all skills to use directory format (fesm-install, fesm-set-project, fesm-customize, setup)

### Added

- New setup skill with installation documentation

### Changed

- Improved fesm-install workflow with better API key guidance
- Added check for existing API key before showing retrieval instructions
- Enhanced user experience with clearer prompts and instructions

---

## **0.9.0** - (2026-01-29)

### Documentation

- **docs**: Add comprehensive documentation for telemetry, governance, and enterprise deployment strategies
- **docs**: Add AI PO/Scrum Master requirements reference document

## **0.8.0** - (2026-01-27)

### Added

- **feat(rally-api)**: Add data integrity validation and comprehensive reference
  - New `validate_hierarchy()` and `validate_story_hierarchy()` methods
  - Automatic hierarchy validation on `create_user_story()` and `create_task()` (enabled by default)
  - Complete parent chain enforcement (Story → Feature → Capability → Initiative)
  - New `data-integrity.md` - 800+ line comprehensive reference covering hierarchy, release/iteration alignment, parent-child inheritance, and required fields

### Changed

- **BREAKING**: `create_user_story()` and `create_task()` now validate hierarchy by default. Use `validate_hierarchy=False` to skip (not recommended)
- Enhanced Rally API documentation with directory structure and data integrity sections
- Updated all guides with cross-references to data-integrity.md

### Fixed

- **chore**: Remove estimate_multiplier for team member from team.json

## **0.7.0** - (2026-01-23)

### Fixed

- **fix**: Resolve merge conflicts in Makefile, SKILL.md, and story template

## **0.6.0** - (2026-01-22)

### Added

- **feat**: Add template rendering methods to Rally API client

### Changed

- **refactor**: Update Rally templates with HTML placeholders

## **0.5.0** - (2026-01-22)

### Added

- **feat**: Add hooks infrastructure - Add session startup hook handler for Rally context initialization, hooks
  configuration, environment loading script, and test helpers. Update plugin configuration and Makefile for hook support.

### Documentation

- **docs**: Update rally-api documentation and tests - Update SKILL.md with enhanced Rally API documentation. Add test
  cases for known items and query functionality.

## [0.4.0-dev.1] - 2026-01-16

### Added

- Makefile with test-api, test-agent, and test-all targets
- Integration test suite for Rally API operations
- test_rally_queries.py for query validation
- test_rally_known_items.py for known item testing
- Test utilities and fixtures
- Rally test data for integration tests

### Fixed

- Path resolution documentation in SKILL.md for marketplace installations
- Test assertions for new path structure
- Path references in test suite for cross-platform compatibility

### Changed

- Improved API client exception handling with better error messages
- Updated documentation for cross-platform path resolution
- Enhanced test suite with proper fixtures and assertions

### Documentation

- Added cross-platform path resolution guidance
- Updated command examples to use PLUGIN_ROOT and PROJECT_ROOT correctly
- Fixed inconsistent path references in backup and workflow guides

## [0.3.0-dev.1] - 2026-01-15

### Added

- Centralized path resolution with PluginPaths class
- Comprehensive unit tests for path resolution (19 test cases)
- New documentation guides (setup-guide, workflow-guide, backups-guide, common-mistakes)
- Automatic release assignment for Rally iterations
- Template customization workflow with fesm-customize command

### Fixed

- Critical bug in template_loader.py (incorrect parent count: 5→4)
- Prevent duplicate backups in Rally operations

### Changed

- Reorganized templates to .fe-sm-default/ structure for clearer separation
- Implemented clean 2-level fallback pattern (user → default)
- Restructured rally-api skill documentation (1917 lines → ~200 lines)
- Removed legacy .fe-sm-templates fallback
- Removed 4 deprecated commands (fesm-deploy-summary, fesm-plan-sprint, fesm-release-notes, fesm-review-feedback)
- Updated all commands for new path structure
- All modules now use centralized PluginPaths infrastructure

### Documentation

- Restructured SKILL.md with progressive disclosure principle
- Added detailed guides for setup, workflow, backups, and troubleshooting
- Expanded API reference documentation
- Updated examples for current patterns

## [0.2.0] - 2026-01-13

### Added

- Initial release of AIDLC-scrum-master plugin
- Refactored from fe plugin into standalone plugin
- 14 rally and sprint management commands with fesm- prefix:
  - `fesm-install` - Rally API setup wizard
  - `fesm-set-project` - Set default workspace/project
  - `fesm-assign` - Assign single story
  - `fesm-bulk-assign` - Bulk assign stories
  - `fesm-create-story` - Create user story
  - `fesm-query-stories` - Query stories
  - `fesm-get-hierarchy` - Get portfolio hierarchy
  - `fesm-update-estimates` - Update estimates
  - `fesm-audit-estimates` - Audit estimates
  - `fesm-set-release-iteration` - Set release/iteration
  - `fesm-plan-sprint` - Sprint planning
  - `fesm-review-feedback` - Sprint review
  - `fesm-deploy-summary` - Deployment summary
  - `fesm-release-notes` - Release notes generation
- 1 skill:
  - `rally-api` - Core Rally API client
- Comprehensive test suite with >80% coverage
- Python API package (rally_api) with modular structure:
  - `client.py` - Rally REST client
  - `config.py` - Configuration management
  - `parsing.py` - Response parsing
  - `team_workflows.py` - Team-specific workflows
  - `exceptions.py` - Custom exceptions
- Documentation:
  - README.md with quick start guide
  - API reference documentation
  - Usage examples
  - Team member reference
  - Story points guidelines

### Changed

- Command prefix changed from `rally-*` and `sprint-*`/`deploy-*` to unified `fesm-*` prefix
- Renamed commands for consistency:
  - `sprint-plan-out` → `fesm-plan-sprint`
  - `sprint-review-feedback` → `fesm-review-feedback`
  - `deploy-email-summary` → `fesm-deploy-summary`
  - `deploy-release-notes` → `fesm-release-notes`

### Notes

- This plugin was extracted from the fe plugin to provide focused Rally and sprint management functionality
- All Rally-related functionality previously in fe plugin is now in AIDLC-scrum-master
- Compatible with Rally WSAPI v2.0
- Requires Python 3.8+ for Python API usage

---

## Migration Guide

If upgrading from fe plugin rally commands:

1. Update command invocations:
   - `/fesm-install` → `/fesm-install`
   - `/fesm-set-project` → `/fesm-set-project`
   - `/fesm-plan-sprint` → `/fesm-plan-sprint`
   - etc.

2. Configuration file remains the same: `~/.claude/aig.json`

3. Python imports update:
   ```python
   # Old
   from plugins.fe.skills.rally_api import ...

   # New
   from plugins.ADLC_scrum_master.skills.rally_api import ...
   ```

4. All functionality remains identical, only naming has changed
