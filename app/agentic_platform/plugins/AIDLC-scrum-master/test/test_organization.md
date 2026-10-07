# Test Organization

## Test Files

### Core Test Files

1. **`test_rally_known_items.py`** ⭐ **PRIMARY TEST SUITE**
   - Tests with **known expected results** from user history
   - Uses `rally_test_data.json` for validation
   - Based on real queries from `.claude/history.jsonl`
   - **Run this first** to verify Owner field fix is working
   - Example: F117572 should return "Dhananjay Agrawat"

2. **`test_rally_queries.py`**
   - General Rally query patterns (22 tests)
   - Tests various query types (iterations, hierarchies, searches)
   - No expected results validation
   - Good for testing query diversity

3. **`test_helpers.py`**
   - Core testing utilities
   - `ClaudeTestHelper` class for running queries
   - Error tracking and recovery logic

### Support Files

4. **`rally_test_data.json`** ⭐ **TEST DATA**
   - Known Rally items with expected results
   - Source of truth for validation
   - Update when Rally data changes

5. **`test_owner_query_fixed.py`**
   - Quick smoke test for Owner field fix
   - Can be deleted after validation

6. **`test_user_query.py`**
   - Single query test from user example
   - Can be deleted after validation

## Test Categories

### ✅ Validated Tests (Known Expected Results)
```bash
pytest test/test_rally_known_items.py -v
```

These tests have **known correct answers** validated against:
- User query history
- Current Rally data
- User confirmations

### 🔍 Pattern Tests (No Validation)
```bash
pytest test/test_rally_queries.py -v
```

These tests verify queries **execute without errors** but don't validate results.

## Running Tests by Priority

### Priority 1: Verify Owner Field Fix
```bash
pytest test/test_rally_known_items.py::TestKnownFeatureOwners -v -s
```

### Priority 2: Test Natural Language Queries
```bash
pytest test/test_rally_known_items.py::TestNaturalLanguageQueries -v -s
```

### Priority 3: Verify Data Consistency
```bash
pytest test/test_rally_known_items.py::TestDataConsistency::test_verify_test_data_accuracy -v
```

### Priority 4: General Query Patterns
```bash
pytest test/test_rally_queries.py -v
```

## Cleanup Tasks

### Files to Keep
- ✅ `test_rally_known_items.py`
- ✅ `test_rally_queries.py`
- ✅ `test_helpers.py`
- ✅ `rally_test_data.json`
- ✅ `README.md`
- ✅ `test_organization.md`

### Files to Remove (After Validation)
- ❌ `test_owner_query_fixed.py` - Replaced by test_rally_known_items.py
- ❌ `test_user_query.py` - Replaced by test_rally_known_items.py

## Updating Test Data

When Rally items change ownership or state:

1. Run the data consistency check:
```bash
pytest test/test_rally_known_items.py::TestDataConsistency -v
```

2. If mismatches found, use this script to get current Rally data:
```python
import sys
sys.path.insert(0, 'skills/rally-api/scripts')
from rally_api import RallyAPI
import json

client = RallyAPI()
items = ['F117572', 'F119972', 'F117399', 'F116904', 'F116902']

results = []
for item_id in items:
    item = client.find_portfolio_item(item_id, 'feature')
    if item:
        owner = item.get('Owner')
        results.append({
            'id': item.get('FormattedID'),
            'name': item.get('Name'),
            'owner': owner.get('_refObjectName') if owner else None,
            'state': item.get('State', {}).get('_refObjectName')
        })

print(json.dumps(results, indent=2))
```

3. Update the `features` array in `rally_test_data.json`

## Test Coverage

### What We Test
- ✅ Owner field is included in responses
- ✅ Natural language owner queries work
- ✅ Multiple query variations
- ✅ Unassigned features handled correctly
- ✅ Known results match expectations

### What We Don't Test Yet
- ⚠️ Email address retrieval (requires extra API call)
- ⚠️ Complex hierarchy queries with owner info
- ⚠️ Owner changes/updates

## Success Criteria

A test suite is healthy when:
1. ✅ `test_rally_known_items.py` all pass (known results match)
2. ✅ `TestDataConsistency` reports no mismatches
3. ✅ Queries complete in < 30 seconds
4. ✅ No timeouts (60s limit)
5. ✅ Recovery from errors works (< 4 consecutive)

## Known Rally Items from History

Based on `.claude/history.jsonl` analysis, these items were most frequently queried:

| Rally ID | Name | Owner | Queries |
|----------|------|-------|---------|
| F117572 | BRD Generation from Documents | Dhananjay Agrawat | User confirmed |
| F119972 | One-Command Task Generation from TDD | Unassigned | User tested |
| F117399 | Requirements Completeness Validator | manoranjan n | 10 queries |
| F116904 | Plugin Usage Telemetry & Analytics | senthilkumar b | 7 queries |
| F116902 | Automated Test Generation & Execution | jagan m | 5 queries |
