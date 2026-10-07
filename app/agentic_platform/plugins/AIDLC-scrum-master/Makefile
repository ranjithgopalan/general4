.PHONY: test-api test-agent test-agent-all test-all test-all-including-slow

# Test rally-api skill unit tests
test-api:
	@echo "Running rally-api unit tests..."
	cd skills/rally-api/scripts && python -m pytest __tests__/ -v

# Test agent tests in test/ directory (skip slow integration tests)
test-agent:
	@echo "Running agent tests (skipping slow integration tests)..."
	cd test && python -m pytest -v -m "not slow" --timeout=90 --timeout-method=thread

# Test agent tests including slow integration tests
test-agent-all:
	@echo "Running all agent tests including slow integration tests..."
	cd test && python -m pytest -v --timeout=90 --timeout-method=thread

# Run all tests (skip slow integration tests)
test-all: test-api test-agent
	@echo "All tests completed (slow integration tests skipped)"

# Run all tests including slow integration tests
test-all-including-slow: test-api test-agent-all
	@echo "All tests completed including slow integration tests"
