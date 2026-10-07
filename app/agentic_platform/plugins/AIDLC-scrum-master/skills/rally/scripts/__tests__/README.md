# Rally API Unit Tests

Unit test suite for the Rally API Python library.

## Test Structure

```
__tests__/
├── conftest.py                  # Shared fixtures
├── test_backup.py               # Backup functionality tests (21 tests)
├── test_config.py               # Configuration management tests (22 tests)
├── test_customize_templates.py  # Template customization tests (6 tests)
├── test_exceptions.py           # Custom exception tests (28 tests)
├── test_parsing.py              # HTML/date parsing tests (37 tests)
├── test_paths.py                # Path resolution tests (20 tests)
├── test_team_workflows.py       # Team workflow tests (16 tests)
└── test_template_loader.py      # Template loading tests (45 tests)
```

## Test Coverage

| Module | Test File | Tests | Status |
|--------|-----------|-------|--------|
| Backup System | test_backup.py | 21 | ✅ All passing |
| Configuration | test_config.py | 22 | ✅ All passing |
| Customization | test_customize_templates.py | 6 | ✅ All passing |
| Exceptions | test_exceptions.py | 28 | ✅ All passing |
| Parsing | test_parsing.py | 37 | ✅ All passing |
| Path Resolution | test_paths.py | 20 | ✅ All passing |
| Team Workflows | test_team_workflows.py | 16 | ✅ All passing |
| Template Loader | test_template_loader.py | 45 | ✅ All passing |
| **Total** | **8 files** | **195 tests** | **✅ 100% passing** |

## Running Tests

### Quick Start

```bash
# From plugin root
cd /path/to/plugins/fe-sm
make test-api

# Or from scripts directory
cd skills/rally-api/scripts
python -m pytest __tests__/ -v
```

### Install Dependencies

```bash
pip install pytest pytest-mock faker
```

### Run Specific Tests

```bash
# Run specific test file
pytest __tests__/test_backup.py -v

# Run specific test
pytest __tests__/test_parsing.py::TestHTMLTextExtractor::test_extract_plain_text -v

# Run specific test class
pytest __tests__/test_config.py::TestPersonalConfig -v
```

### Run Tests with Options

```bash
# Detailed output
pytest __tests__/ -vv

# Show print statements
pytest __tests__/ -v -s

# Stop on first failure
pytest __tests__/ -x

# Run with coverage
pytest __tests__/ --cov=rally_api --cov-report=term
```

## Test Fixtures

Shared fixtures in `conftest.py`:

- `sample_story` - Sample user story data
- `sample_stories` - List of sample stories
- `sample_tasks` - Sample task data
- `sample_feature` - Sample feature data
- `sample_epic` - Sample epic data
- `sample_iteration` - Sample iteration data
- `sample_user` - Sample user data
- `sample_release` - Sample release data

## Test Modules

### test_backup.py

Tests for the backup system (`rally_api.backup`):
- List backups
- View backup metadata
- Create backups
- Restore from backups
- Backup file operations

### test_config.py

Tests for configuration management (`rally_api.config`):
- Load API key from config
- Save/load last project
- Workspace configuration
- Settings management
- Config file operations

### test_customize_templates.py

Tests for template customization:
- Copy default templates
- Template customization workflow
- Template file operations

### test_exceptions.py

Tests for custom exception types:
- RallyAPIKeyError
- RallyItemNotFoundError
- RallyValidationError
- Exception message formatting

### test_parsing.py

Tests for HTML and date parsing (`rally_api.parsing`):
- HTML text extraction
- Date parsing from various formats
- Markdown to HTML conversion
- Rally link extraction
- Description format validation

### test_paths.py

Tests for path resolution (`rally_api.paths`):
- Plugin path resolution
- Config directory paths
- Template directory paths
- Backup directory paths
- Cross-platform path handling

### test_team_workflows.py

Tests for team workflow utilities (`rally_api.team_workflows`):
- Stage story creation
- Task distribution
- Team assignment logic
- Parallel task creation

### test_template_loader.py

Tests for template loading (`rally_api.template_loader`):
- Load default templates
- Load custom templates
- Template rendering
- Template fallback logic
- Template variable substitution

## Adding New Tests

When adding new functionality:

1. Create new test file: `test_<module_name>.py`
2. Import fixtures from `conftest.py`
3. Create test class: `Test<ClassName>`
4. Write tests for:
   - Success scenarios
   - Error scenarios (not found, invalid input)
   - Edge cases (empty results, nulls, boundaries)
   - Exception handling

Example:

```python
import pytest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))
from rally_api import RallyAPI

class TestNewFeature:
    def test_success_case(self, sample_story):
        # Test implementation
        pass

    def test_error_case(self):
        # Test error handling
        pass
```

## CI/CD Integration

Add to your CI/CD pipeline:

```yaml
test:
  script:
    - pip install pytest pytest-cov pytest-mock faker
    - cd plugins/fe-sm/skills/rally-api/scripts
    - pytest __tests__/ --cov=rally_api --cov-report=xml
    - coverage_pct=$(pytest --cov=rally_api __tests__/ --cov-report=term | grep TOTAL | awk '{print $4}' | sed 's/%//')
    - if [ "$coverage_pct" -lt 85 ]; then exit 1; fi
```

## Troubleshooting

### Import Errors

If you see `ModuleNotFoundError`:

```bash
# Ensure you're in the scripts directory
cd plugins/fe-sm/skills/rally-api/scripts

# Run with Python path
PYTHONPATH=. pytest __tests__/ -v
```

### Fixture Not Found

Ensure `conftest.py` is in the `__tests__` directory and properly defines fixtures.

### Mock Not Working

Ensure you're patching the correct location where the module is used, not where it's defined:

```python
# Correct - patch where it's imported
@patch('rally_api.backup.RallyAPI')

# Incorrect - patch where it's originally defined
@patch('rally_api.client.RallyAPI')
```

## Coverage Goals

- **Overall Coverage:** 85%+ achieved
- **Critical Paths:** 100% (create, update, backup operations)
- **Error Handling:** 90%+ (all error scenarios covered)
- **Edge Cases:** 80%+ (empty results, not found, nulls)

## Notes

- **rally_cli.py deleted:** CLI wrapper removed (Jan 2026). Tests for CLI commands (query-stories, create-story, etc.) removed.
- **Focus:** Tests now focus on core `rally_api` library functionality only.
- **Pattern:** All tests use direct Python API imports, matching skill documentation pattern.
