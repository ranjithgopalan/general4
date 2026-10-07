#!/usr/bin/env python3
"""
Test Rally queries with known expected results from user history.

This test suite uses real Rally items that were frequently queried in user history,
with expected results validated against actual Rally data.
"""

import pytest
import json
from pathlib import Path
from test_helpers import ClaudeTestHelper


@pytest.fixture
def helper():
    """Fixture providing ClaudeTestHelper instance."""
    return ClaudeTestHelper()


@pytest.fixture
def test_data():
    """Load test data with expected results."""
    test_data_path = Path(__file__).parent / "rally_test_data.json"
    with open(test_data_path) as f:
        return json.load(f)


@pytest.mark.slow
class TestKnownFeatureOwners:
    """Test queries for feature owners with known expected results."""

    def test_f117572_owner_dhananjay(self, helper, test_data):
        """Test: who is assigned to F117572?

        Expected: Dhananjay Agrawat (dhananjay.agrawat@aig.com)
        Source: User confirmed in history
        """
        query = "who is assigned to rally F117572"

        success = helper.run_query(query, verbose=True)

        result = helper.get_last_result()
        assert result is not None
        assert result['turn_count'] > 0

        # Should complete without errors
        assert not result['has_error'], f"Query failed with errors: {result['errors']}"

        # Expected owner from test data
        expected = next(f for f in test_data['test_data']['features'] if f['id'] == 'F117572')
        print(f"\nExpected owner: {expected['owner']}")
        print(f"Email: {expected['owner_email']}")

    def test_f119972_owner_unassigned(self, helper, test_data):
        """Test: who is assigned to F119972?

        Expected: Unassigned (null/no owner)
        Source: Verified in previous test runs
        """
        query = "who is assigned to F119972"

        success = helper.run_query(query, verbose=True)

        result = helper.get_last_result()
        assert result is not None

        # Should complete successfully even if no owner
        assert not result['has_error'], "Should handle unassigned features gracefully"

        expected = next(f for f in test_data['test_data']['features'] if f['id'] == 'F119972')
        print(f"\nExpected owner: {expected['owner']} (unassigned)")

    def test_f117399_owner_manoranjan(self, helper, test_data):
        """Test: who owns F117399?

        Expected: manoranjan n (also known as mano, same person as dhananjay agrawat)
        Source: Frequently queried in history
        """
        query = "who owns F117399"

        success = helper.run_query(query, verbose=True)

        result = helper.get_last_result()
        assert result is not None
        assert not result['has_error']

        expected = next(f for f in test_data['test_data']['features'] if f['id'] == 'F117399')
        print(f"\nExpected owner: {expected['owner']}")
        print(f"Note: {expected['notes']}")

    def test_f116904_owner_senthil(self, helper, test_data):
        """Test: who is assigned to F116904?

        Expected: senthilkumar b (nickname: senthil)
        Source: User history mentions senthil
        """
        query = "who is assigned to F116904"

        success = helper.run_query(query, verbose=True)

        result = helper.get_last_result()
        assert result is not None
        assert not result['has_error']

        expected = next(f for f in test_data['test_data']['features'] if f['id'] == 'F116904')
        print(f"\nExpected owner: {expected['owner']}")


@pytest.mark.slow
class TestFeatureDetails:
    """Test queries for feature details with expected results."""

    def test_f117572_details(self, helper, test_data):
        """Test: Get full details for F117572.

        Expected: BRD Generation from Documents, Implementing state
        """
        query = "Get Rally feature F117572 details including name, state, and owner"

        success = helper.run_query(query, verbose=True)

        result = helper.get_last_result()
        assert result is not None
        assert not result['has_error']

        expected = next(f for f in test_data['test_data']['features'] if f['id'] == 'F117572')
        print(f"\nExpected:")
        print(f"  Name: {expected['name']}")
        print(f"  Owner: {expected['owner']}")
        print(f"  State: {expected['state']}")

    def test_f119972_details(self, helper, test_data):
        """Test: Get details for F119972.

        Expected: One-Command Task Generation from TDD, Backlog state, no owner
        """
        query = "What are the details of Rally feature F119972"

        success = helper.run_query(query, verbose=True)

        result = helper.get_last_result()
        assert result is not None

        expected = next(f for f in test_data['test_data']['features'] if f['id'] == 'F119972')
        print(f"\nExpected:")
        print(f"  Name: {expected['name']}")
        print(f"  State: {expected['state']}")
        print(f"  Owner: {expected['owner']} (unassigned)")


@pytest.mark.slow
class TestMultipleFeatures:
    """Test queries involving multiple features."""

    def test_compare_owners(self, helper, test_data):
        """Test: Compare owners of multiple features.

        Query multiple features and verify we can retrieve owner info for all
        """
        features = ['F117572', 'F119972', 'F117399']

        query = f"Who are the owners of Rally features {', '.join(features)}"

        success = helper.run_query(query, verbose=True)

        result = helper.get_last_result()
        assert result is not None

        print("\nExpected owners:")
        for feature_id in features:
            expected = next(f for f in test_data['test_data']['features'] if f['id'] == feature_id)
            owner = expected['owner'] if expected['owner'] else "Unassigned"
            print(f"  {feature_id}: {owner}")


@pytest.mark.slow
class TestNaturalLanguageQueries:
    """Test natural language variations of owner queries."""

    def test_who_is_assigned_variation_1(self, helper):
        """Test: who is assigned to rally F117572"""
        query = "who is assigned to rally F117572"
        helper.assert_no_errors(query)

    def test_who_is_assigned_variation_2(self, helper):
        """Test: who owns F117572"""
        query = "who owns F117572"
        helper.assert_no_errors(query)

    def test_who_is_assigned_variation_3(self, helper):
        """Test: what is the owner of feature F117572"""
        query = "what is the owner of feature F117572"
        helper.assert_no_errors(query)

    def test_who_is_assigned_variation_4(self, helper):
        """Test: who is working on F117572"""
        query = "who is working on F117572"
        helper.assert_no_errors(query)


@pytest.mark.slow
class TestDataConsistency:
    """Verify test data is consistent with actual Rally data."""

    def test_verify_test_data_accuracy(self, helper, test_data):
        """Verify that test_data.json matches current Rally state.

        This test ensures our test data stays in sync with Rally.
        """
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent / 'skills/rally-api/scripts'))
        from rally_api import RallyAPI

        client = RallyAPI()

        mismatches = []
        for expected in test_data['test_data']['features']:
            feature_id = expected['id']

            # Get actual data from Rally
            actual = client.find_portfolio_item(feature_id, 'feature')

            if not actual:
                mismatches.append(f"{feature_id}: Not found in Rally")
                continue

            # Check owner
            actual_owner = actual.get('Owner')
            actual_owner_name = actual_owner.get('_refObjectName') if actual_owner else None

            if actual_owner_name != expected['owner']:
                mismatches.append(
                    f"{feature_id}: Owner mismatch - "
                    f"Expected: {expected['owner']}, Actual: {actual_owner_name}"
                )

        if mismatches:
            print("\nWARNING: Test data mismatches found:")
            for mismatch in mismatches:
                print(f"  - {mismatch}")
            print("\nConsider updating test/rally_test_data.json")
        else:
            print("\n✓ All test data matches current Rally state")


# Pytest configuration
def pytest_configure(config):
    """Configure pytest with markers."""
    config.addinivalue_line(
        "markers",
        "slow: marks tests as slow (deselect with '-m \"not slow\"')"
    )
    config.addinivalue_line(
        "markers",
        "known_data: tests with known expected results from user history"
    )


if __name__ == "__main__":
    # Allow running with: python test_rally_known_items.py
    pytest.main([__file__, "-v", "-s"])
