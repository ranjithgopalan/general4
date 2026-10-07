# fe-sm Plugin Tests

Test suite for the fe-sm plugin Rally API integration using Claude CLI and pytest.

## Test Structure

### Test Helpers (`test_helpers.py`)

Core testing utilities for Claude CLI integration:

```python
from test_helpers import ClaudeTestHelper

helper = ClaudeTestHelper()

# Run query and check success
success = helper.run_query("Query Rally item I978")

# Assert query succeeds
helper.assert_query_success("Query Rally item I978")

# Assert no errors occurred
helper.assert_no_errors("List stories in iteration 2026.PI1.Iteration1")

# Assert specific tool was used
helper.assert_tool_used("Use rally-api skill", "Skill")

# Get detailed results
result = helper.get_last_result()
# Returns: {turn_count, has_error, errors, tool_uses, final_result, consecutive_errors}
```

**Key Features:**
- Tracks consecutive errors (fails after 4 in a row)
- Allows Claude to recover from mistakes
- Streams turn-by-turn output with ⏺ markers
- Detects tool usage and verifies execution

### Pytest Test Suite

#### `test_rally_known_items.py` ⭐ **NEW**

**Tests with Known Expected Results** - Based on real user query history with validated answers:

This test suite uses real Rally items that were frequently queried in `.claude/history.jsonl`, with expected results validated against actual Rally data.

**Test Data:** `rally_test_data.json` - Contains known Rally items and expected owners

**Test Classes:**
1. **TestKnownFeatureOwners** - Verify owner queries return expected results
   - `test_f117572_owner_dhananjay` - F117572 → Dhananjay Agrawat ✓
   - `test_f119972_owner_unassigned` - F119972 → null (unassigned) ✓
   - `test_f117399_owner_manoranjan` - F117399 → manoranjan n ✓
   - `test_f116904_owner_senthil` - F116904 → senthilkumar b ✓

2. **TestFeatureDetails** - Get complete feature information
   - Verifies name, state, owner match expected values

3. **TestMultipleFeatures** - Query multiple features at once

4. **TestNaturalLanguageQueries** - Various ways to ask about owners
   - "who is assigned to rally F117572"
   - "who owns F117572"
   - "what is the owner of feature F117572"
   - "who is working on F117572"

5. **TestDataConsistency** - Validates test data matches current Rally state

**Example:**
```bash
# Run tests with known expected results
pytest test/test_rally_known_items.py -v -s

# Run specific owner test
pytest test/test_rally_known_items.py::TestKnownFeatureOwners::test_f117572_owner_dhananjay -v
```

**Updating Test Data:**
When Rally data changes, update `rally_test_data.json` with new expected results.

---

#### `test_rally_queries.py`

General Rally query patterns - 22 tests covering various query types:

**Test Classes:**
1. **TestRallyReadQueries** - Basic read-only queries (initiative details, iteration stories, epic hierarchy)
2. **TestItemDetails** - Get description/details (I978, US774981)
3. **TestIterationQueries** - Find stories in iteration with filters
4. **TestHierarchyQueries** - Get stories/tasks under initiative/epic/feature (I937, E4588, F116216)
5. **TestEstimateValidation** - Verify estimates follow pattern (task = plan * 8)
6. **TestWorkspaceProjectQueries** - Find where a story lives
7. **TestRecentActivity** - Check what was done in last N sprints
8. **TestFieldQueries** - Get specific Rally fields (actual dev end date, owner)
9. **TestSearchQueries** - Find stories by keyword/criteria
10. **TestRallyToolUsage** - Verify correct tools are used (Bash, Skill)
11. **TestRallyErrorHandling** - Graceful handling of non-existent items

**Real Rally IDs used:**
- I940: Tier 1 Plugins (Developing)
- I978: Tier 2 Plugins (Discovering)
- I937: Tier 3 Plugins (Discovering)
- E4574: [Support] ADLC Plugin Bundle (Done)
- E4588: [Tier 1] AIDLC-axis Enhancements (Implementing)

## Running Tests

### Install Dependencies

```bash
pip install pytest
```

### Run Tests

```bash
# From fe-sm directory - runs ALL tests
make test-agent

# Or directly from test directory
cd plugins/fe-sm/test

# Run all tests
pytest -v

# Run with live streaming output
pytest -v -s

# Run specific test class
pytest test_rally_queries.py::TestItemDetails -v

# Run specific test
pytest test_rally_queries.py::TestRallyReadQueries::test_query_initiative_details -v
```

## Test Output

### Pytest Output

**With -v flag (summary):**
```bash
$ pytest test_rally_queries.py -v

test_rally_queries.py::TestRallyReadQueries::test_query_initiative_details PASSED
test_rally_queries.py::TestRallyReadQueries::test_query_iteration_stories PASSED
test_rally_queries.py::TestRallyReadQueries::test_query_epic_hierarchy PASSED
test_rally_queries.py::TestItemDetails::test_get_initiative_description PASSED
test_rally_queries.py::TestItemDetails::test_get_user_story_details PASSED
...
test_rally_queries.py::TestRallyErrorHandling::test_invalid_iteration_name PASSED

================= 22 passed in 180s =================
```

**With -v -s flags (live streaming with turn-by-turn output):**
```bash
$ pytest test_rally_queries.py::TestRallyReadQueries::test_query_initiative_details -v -s

Query: Query Rally for initiative I978 using rally_cli.py...
================================================================================

⏺ Turn 1: I'll query Rally for initiative I978...
  → Using tool: Bash
  ✗ ERROR in tool result
    Exit code 2
python: can't open file...
  (Consecutive errors: 1/4)

⏺ Turn 2: Let me find the rally_cli.py file first...
  → Using tool: Glob
  ✓ Tool output: ../skills/rally-api/scripts/rally_cli.py...
  (Recovered from errors)

⏺ Turn 3: Now querying Rally for I978...
  → Using tool: Bash
  ✓ Tool output: {"FormattedID": "I978", "Name": "Tier 2 Plugins"...

================================================================================
Final result (success):
Initiative I978: "Tier 2 Plugins", State: "Discovering"

✓ SUCCESS: Query completed without errors
  Turns: 3
  Tools used: Bash, Glob, Bash

test_rally_queries.py::TestRallyReadQueries::test_query_initiative_details PASSED
```


## Test Helper API

### `ClaudeTestHelper` Class

| Method | Description | Returns |
|--------|-------------|---------|
| `run_query(query, verbose)` | Run Claude query and check for errors | `bool` |
| `assert_query_success(query, message)` | Assert query succeeds | Raises `AssertionError` |
| `assert_no_errors(query)` | Assert no errors occurred | Raises `AssertionError` |
| `assert_tool_used(query, tool_name)` | Assert specific tool was used | Raises `AssertionError` |
| `get_last_result()` | Get analysis from last query | `Dict` |

### Result Dictionary

```python
{
    "turn_count": 3,              # Number of turns executed
    "has_error": False,           # Whether test failed (4+ consecutive errors or final failure)
    "errors": [],                 # List of all error messages encountered
    "tool_uses": ["Bash"],        # List of tools used
    "final_result": "...",        # Claude's final response text
    "consecutive_errors": 0       # Consecutive errors at end (fails at 4)
}
```

**Error Handling:**
- Collects all errors but allows Claude to recover
- Fails only if 4 consecutive errors occur
- Resets counter on successful tool use

## Requirements

- Claude Code CLI installed (`claude` command available)
- Rally API configured (run `/fesm-install` first)
- fe-sm plugin installed at marketplace
- Python 3.7+
- pytest (`pip install pytest`)
- ANTHROPIC_API_KEY environment variable (set in test helper)

## Troubleshooting

**Tests timeout:**
```bash
# Increase pytest timeout
pytest test_rally_read_queries.py --timeout=120
```

**Rally API not configured:**
```
ERROR: Rally API key not found
```
Solution: Run `/fesm-install` to configure Rally API credentials

**Claude CLI not found:**
```
ERROR: Command failed with return code 127
```
Solution: Install Claude Code CLI or update `claude_path` in `ClaudeTestHelper.__init__()`

**API Key error:**
```
ERROR: ANTHROPIC_API_KEY not set
```
Solution: Update the API key in `test_helpers.py` or set environment variable
