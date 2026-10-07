#!/usr/bin/env python3
"""
Pytest test suite for Rally API queries.

Tests real-world Rally query patterns including read-only operations,
hierarchy queries, iteration queries, and error handling.
"""

import pytest
from test_helpers import ClaudeTestHelper


@pytest.fixture
def helper():
    """Fixture providing ClaudeTestHelper instance."""
    return ClaudeTestHelper()


@pytest.mark.slow
class TestRallyReadQueries:
    """Test class for basic Rally read-only queries."""

    def test_query_initiative_details(self, helper):
        """Test querying initiative details by FormattedID.

        Query: Get initiative I978 details including description and state
        """
        query = (
            "Query Rally for initiative I978 using rally_cli.py. "
            "Get its FormattedID, Name, and State and show me the results."
        )

        # Assert query succeeds
        helper.assert_query_success(query)

        # Verify execution details
        result = helper.get_last_result()
        assert result is not None
        assert result['turn_count'] > 0, "Should have at least one turn"
        assert not result['has_error'], "Should complete without errors"

        # Should use Bash or Skill tool to query Rally
        assert len(result['tool_uses']) > 0, "Should use tools to query Rally"

    def test_query_iteration_stories(self, helper):
        """Test querying stories in a specific iteration.

        Query: List all user stories in iteration 2026.PI1.Iteration1
        """
        query = (
            "Query Rally using rally_cli.py to list all user stories "
            "in iteration 2026.PI1.Iteration1. Show their FormattedIDs, Names, and ScheduleStates."
        )

        # Assert query succeeds without errors
        helper.assert_no_errors(query)

        # Verify tool usage
        result = helper.get_last_result()
        assert result is not None
        assert result['turn_count'] > 0

        # Verify tools were used (should use Bash to run rally_cli.py or Skill)
        tool_uses = result['tool_uses']
        assert any(tool in tool_uses for tool in ['Bash', 'Skill']), \
            f"Should use Bash or Skill tool, got: {tool_uses}"

    def test_query_epic_hierarchy(self, helper):
        """Test querying portfolio hierarchy for an epic.

        Query: Get hierarchy structure for epic E4574
        """
        query = (
            "Query Rally for epic E4574 using rally_cli.py to get its hierarchy. "
            "Show the capabilities, features, and story counts."
        )

        success = helper.run_query(query, verbose=True)

        # Assert successful execution
        assert success, "Query should complete successfully"

        # Verify execution metrics
        result = helper.get_last_result()
        assert result is not None
        assert result['turn_count'] >= 1, "Should execute at least one turn"
        assert result['errors'] == [], f"Should have no errors, got: {result['errors']}"

        # Verify Rally API was queried
        assert len(result['tool_uses']) > 0, "Should use tools to query Rally API"


@pytest.mark.slow
class TestItemDetails:
    """Test querying details of specific Rally items."""

    def test_get_initiative_description(self, helper):
        """Get description of initiative I978.

        Historical query: "Get the description in Rally ticket I978"
        """
        query = "Get the description of Rally initiative I978"
        helper.assert_query_success(query)

        result = helper.get_last_result()
        assert len(result['tool_uses']) > 0, "Should use tools to query Rally"

    def test_get_user_story_details(self, helper):
        """Get details of a specific user story.

        Historical query: "what is US774981? show me"
        """
        query = "Query Rally for user story US774981 and show me its details"
        helper.assert_query_success(query)

        result = helper.get_last_result()
        assert result['turn_count'] > 0


@pytest.mark.slow
class TestIterationQueries:
    """Test querying stories by iteration."""

    def test_query_iteration_stories(self, helper):
        """List all stories in a specific iteration.

        Historical query: "in the forward engineering portfolio find all the user stories in iteration 2025.PI5.Iteration5"
        """
        query = (
            "Query Rally to find all user stories in iteration 2025.PI5.Iteration5 "
            "in the Forward Engineering portfolio"
        )
        helper.assert_no_errors(query)

        result = helper.get_last_result()
        assert len(result['tool_uses']) > 0

    def test_query_iteration_with_task_estimate_filter(self, helper):
        """Find stories with task estimate > 0 but actuals = 0.

        Historical query: "find all the user stories in iteration 2025.PI5.Iteration5 that have a task estimate > 0 but actuals is 0"
        """
        query = (
            "Query Rally for user stories in iteration 2025.PI5.Iteration5 "
            "where task estimate is greater than 0 but actuals is 0"
        )
        success = helper.run_query(query, verbose=True)

        # This might not find results, but should execute without crashing
        result = helper.get_last_result()
        assert result is not None


@pytest.mark.slow
class TestHierarchyQueries:
    """Test querying portfolio hierarchy (initiatives, epics, features)."""

    def test_get_stories_under_initiative(self, helper):
        """Get all stories under an initiative.

        Historical query: "check all the user stories and tasks under I937"
        """
        query = "Query Rally to get all user stories and tasks under initiative I937"
        helper.assert_query_success(query)

        result = helper.get_last_result()
        assert len(result['tool_uses']) > 0

    def test_get_stories_under_epic(self, helper):
        """Get stories under a specific epic for current sprint.

        Historical query: "find all the stories under E4588 for this sprint and show it to me as a list"
        """
        query = "Query Rally to find all user stories under epic E4588 for the current sprint"
        success = helper.run_query(query, verbose=True)

        result = helper.get_last_result()
        assert result is not None

    def test_check_feature_has_stories(self, helper):
        """Check if a feature has stories.

        Historical query: "what are you talking about F116216 has stories"
        """
        query = "Query Rally to check if feature F116216 has any user stories"
        helper.assert_no_errors(query)

        result = helper.get_last_result()
        assert result['turn_count'] > 0


@pytest.mark.slow
class TestEstimateValidation:
    """Test queries that validate estimates are correct."""

    def test_check_estimate_pattern(self, helper):
        """Check that stories follow estimate pattern (task estimate = plan estimate * 8).

        Historical query: "check all the user stories and tasks under I937 -- they should have a plan estimate (0 is allowed)
        and if its >0 task estimate should be plan estimate *8 -- show me a list of non-stage stories"
        """
        query = (
            "Query Rally for all user stories under I937 and check if they follow the pattern: "
            "if plan estimate > 0, then task estimate should equal plan estimate * 8. "
            "Show stories that don't follow this pattern."
        )
        success = helper.run_query(query, verbose=True)

        # Complex query - just verify it executes
        result = helper.get_last_result()
        assert result is not None


@pytest.mark.slow
class TestWorkspaceProjectQueries:
    """Test queries about workspace and project locations."""

    def test_find_story_workspace(self, helper):
        """Find which workspace a story is in.

        Historical query: "what rally workspace is US774969 in ?"
        """
        query = "Query Rally to find which workspace user story US774969 is in"
        helper.assert_no_errors(query)

        result = helper.get_last_result()
        assert result['turn_count'] > 0

    def test_find_story_workspace_project_portfolio(self, helper):
        """Find workspace, project, and portfolio for a story.

        Historical query: "what is the workspace, project, and portfolio where this user story is found US772464"
        """
        query = "Query Rally to find the workspace, project, and portfolio for user story US772464"
        success = helper.run_query(query, verbose=True)

        result = helper.get_last_result()
        assert result is not None


@pytest.mark.slow
class TestRecentActivity:
    """Test queries about recent sprint activity."""

    def test_check_recent_sprints(self, helper):
        """Check what was done in recent sprints.

        Historical query: "go to rally and check what we've recently done in the last two sprints"
        """
        query = "Query Rally to show what was completed in the last two sprints"
        success = helper.run_query(query, verbose=True)

        # This is a broad query - just verify execution
        result = helper.get_last_result()
        assert result is not None


@pytest.mark.slow
class TestFieldQueries:
    """Test queries for specific Rally fields."""

    def test_get_story_actual_dev_end_date(self, helper):
        """Get actual dev end date for a story.

        Historical query: "the dates are wrong.. for example US768920 should have been labeled 12/3/25
        which is in the 'actual dev end date' field"
        """
        query = "Query Rally to get the actual dev end date field for user story US768920"
        helper.assert_no_errors(query)

        result = helper.get_last_result()
        assert result['turn_count'] > 0

    def test_check_story_owner_and_tasks(self, helper):
        """Check story owner and tasks.

        Historical query: "Requirements Sign off is mike. and what tasks are under US772313"
        """
        query = "Query Rally to get the owner and all tasks under user story US772313"
        success = helper.run_query(query, verbose=True)

        result = helper.get_last_result()
        assert result is not None


@pytest.mark.slow
class TestSearchQueries:
    """Test search-based Rally queries."""

    def test_find_optimizer_stories(self, helper):
        """Find stories about running optimizer.

        Historical query: "find any stories under I940 that are to Run optimizer on the plugin and list them for me"
        """
        query = "Query Rally to find stories under I940 that mention 'optimizer' in their name or description"
        success = helper.run_query(query, verbose=True)

        result = helper.get_last_result()
        assert result is not None

    def test_find_similar_story(self, helper):
        """Find a similar story under a different epic.

        Historical query: "can you find a similar story it might be under E4564"
        """
        query = "Query Rally to search for stories under epic E4564"
        helper.assert_no_errors(query)

        result = helper.get_last_result()
        assert result['turn_count'] > 0


@pytest.mark.slow
class TestRallyToolUsage:
    """Test that correct tools are being used for Rally queries."""

    def test_bash_tool_for_rally_cli(self, helper):
        """Test that Bash tool is used to execute rally_cli.py scripts."""
        query = "Use rally_cli.py to query stories in iteration 2026.PI1.Iteration1"

        success = helper.run_query(query, verbose=False)
        assert success, "Query should succeed"

        # Verify Bash was used
        helper.assert_tool_used(query, "Bash")

    def test_skill_tool_for_rally_api(self, helper):
        """Test that Skill tool can be used to invoke rally-api skill."""
        query = "Use the /fe-sm:rally-api skill to check Rally API configuration"

        success = helper.run_query(query, verbose=False)

        # Get tools used
        result = helper.get_last_result()
        assert result is not None

        # Either succeeds with Skill, or Claude handles it differently
        # Just verify no execution errors
        assert not result['has_error'], "Should not have execution errors"


@pytest.mark.slow
class TestRallyErrorHandling:
    """Test error handling for Rally queries."""

    def test_nonexistent_item_handling(self, helper):
        """Test querying a non-existent Rally item is handled gracefully."""
        query = "Query Rally item XXXX999999 and show its details"

        # Run query - Claude should handle gracefully (say item not found)
        success = helper.run_query(query, verbose=False)
        result = helper.get_last_result()

        # Should complete (Claude says not found) or fail gracefully
        # Key: no execution errors, just a "not found" response
        assert result is not None
        assert result['turn_count'] > 0, "Should attempt to query"

        # Note: This might succeed (Claude says item not found) or fail
        # depending on whether the query script returns error codes
        # The important part is it doesn't crash

    def test_invalid_iteration_name(self, helper):
        """Test querying with invalid iteration name is handled."""
        query = "Query Rally stories in iteration 'ThisIterationDoesNotExist'"

        success = helper.run_query(query, verbose=False)
        result = helper.get_last_result()

        # Should attempt the query and either:
        # 1. Succeed with "no stories found" message
        # 2. Fail gracefully with error message
        assert result is not None
        assert result['turn_count'] > 0


# Pytest configuration
def pytest_configure(config):
    """Configure pytest with markers."""
    config.addinivalue_line(
        "markers",
        "slow: marks tests as slow (deselect with '-m \"not slow\"')"
    )


if __name__ == "__main__":
    # Allow running with: python test_rally_queries.py
    pytest.main([__file__, "-v"])
