"""
Unit tests for rally_api.team_workflows module.

Tests team-specific workflows including:
- Creating features with administrative tasks
- Stage task creation and assignment
- Parallel task creation
- Administrative Tasks story template
"""

import pytest
from unittest.mock import MagicMock, patch, call
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from rally_api import team_workflows


class TestCreateFeatureWithStages:
    """Tests for create_feature_with_stages workflow."""

    def test_create_feature_basic(self):
        """Test creating a feature with basic fields."""
        mock_api = MagicMock()
        mock_api.project_ref = None
        mock_api.create_object.return_value = {
            "FormattedID": "F12345",
            "Name": "Test Feature",
            "_ref": "https://rally.../feature/12345"
        }

        result = team_workflows.create_feature_with_stages(
            api=mock_api,
            name="Test Feature",
            parent_ref="https://rally.../capability/100"
        )

        # Should create feature
        assert result["feature"] is not None
        assert result["feature"]["FormattedID"] == "F12345"

        # Verify feature creation call
        feature_call = mock_api.create_object.call_args_list[0]
        assert feature_call[0][0] == "portfolioitem/feature"
        assert feature_call[0][1]["Name"] == "Test Feature"
        assert feature_call[0][1]["State"] == "Funnel"  # Default state

    def test_create_feature_with_description(self):
        """Test creating feature with description."""
        mock_api = MagicMock()
        mock_api.project_ref = None
        mock_api.create_object.return_value = {
            "_ref": "https://rally.../feature/12345"
        }

        team_workflows.create_feature_with_stages(
            api=mock_api,
            name="Test Feature",
            parent_ref="https://rally.../capability/100",
            description="<p>Test description</p>"
        )

        # Verify description was included
        feature_call = mock_api.create_object.call_args_list[0]
        assert "Description" in feature_call[0][1]
        assert "Test description" in feature_call[0][1]["Description"]

    def test_create_feature_with_custom_state(self):
        """Test creating feature with custom state."""
        mock_api = MagicMock()
        mock_api.project_ref = None
        mock_api.create_object.return_value = {
            "_ref": "https://rally.../feature/12345"
        }

        team_workflows.create_feature_with_stages(
            api=mock_api,
            name="Test Feature",
            parent_ref="https://rally.../capability/100",
            state="Backlog"
        )

        # Verify custom state
        feature_call = mock_api.create_object.call_args_list[0]
        assert feature_call[0][1]["State"] == "Backlog"

    def test_create_feature_with_owner(self):
        """Test creating feature with owner assigned."""
        mock_api = MagicMock()
        mock_api.project_ref = None
        mock_api.create_object.return_value = {
            "_ref": "https://rally.../feature/12345"
        }

        team_workflows.create_feature_with_stages(
            api=mock_api,
            name="Test Feature",
            parent_ref="https://rally.../capability/100",
            owner_ref="https://rally.../user/999"
        )

        # Verify owner assignment
        feature_call = mock_api.create_object.call_args_list[0]
        assert feature_call[0][1]["Owner"] == "https://rally.../user/999"

    def test_create_feature_with_project_ref(self):
        """Test creating feature with project reference."""
        mock_api = MagicMock()
        mock_api.project_ref = "https://rally.../project/500"
        mock_api.create_object.return_value = {
            "_ref": "https://rally.../feature/12345"
        }

        team_workflows.create_feature_with_stages(
            api=mock_api,
            name="Test Feature",
            parent_ref="https://rally.../capability/100"
        )

        # Verify project reference included
        feature_call = mock_api.create_object.call_args_list[0]
        assert feature_call[0][1]["Project"] == "https://rally.../project/500"

    def test_creates_administrative_tasks_story(self):
        """Test creates Administrative Tasks story with correct fields."""
        mock_api = MagicMock()
        mock_api.project_ref = None
        mock_api.create_object.side_effect = [
            {"_ref": "https://rally.../feature/12345"},  # Feature
            {"_ref": "https://rally.../story/67890"},     # Admin story
        ] + [{"_ref": f"https://rally.../task/{i}"} for i in range(8)]  # Tasks

        result = team_workflows.create_feature_with_stages(
            api=mock_api,
            name="Test Feature",
            parent_ref="https://rally.../capability/100"
        )

        # Find the admin story creation call (second call)
        admin_story_call = None
        for call in mock_api.create_object.call_args_list:
            if call[0][0] == "hierarchicalrequirement":
                admin_story_call = call
                break

        assert admin_story_call is not None
        admin_fields = admin_story_call[0][1]

        assert admin_fields["Name"] == "Administrative Tasks"
        assert admin_fields["ScheduleState"] == "Defined"
        assert admin_fields["PlanEstimate"] == 0
        assert "As a" in admin_fields["Description"]
        assert "team member" in admin_fields["Description"]

    def test_creates_all_stage_tasks(self):
        """Test creates all 8 stage tasks."""
        mock_api = MagicMock()
        mock_api.project_ref = None
        mock_api.create_object.side_effect = [
            {"_ref": "https://rally.../feature/12345"},
            {"_ref": "https://rally.../story/67890"},
        ] + [{"_ref": f"https://rally.../task/{i}"} for i in range(8)]

        result = team_workflows.create_feature_with_stages(
            api=mock_api,
            name="Test Feature",
            parent_ref="https://rally.../capability/100"
        )

        # Should have 8 stage tasks
        assert len(result["tasks"]) == 8

        # Verify stage task names
        task_names = list(result["tasks"].keys())
        assert any("[Stage 1.1]" in name for name in task_names)
        assert any("[Stage 1.2]" in name for name in task_names)
        assert any("[Stage 1.3]" in name for name in task_names)
        assert any("[Stage 2.1]" in name for name in task_names)
        assert any("[Stage 2.2]" in name for name in task_names)
        assert any("[Stage 4.1]" in name for name in task_names)
        assert any("[Stage 4.2]" in name for name in task_names)
        assert any("[Stage 4.3]" in name for name in task_names)

    def test_stage_tasks_have_specific_estimates(self):
        """Test stage tasks have specific hour estimates."""
        mock_api = MagicMock()
        mock_api.project_ref = None
        mock_api.create_object.side_effect = [
            {"_ref": "https://rally.../feature/12345"},
            {"_ref": "https://rally.../story/67890"},
        ] + [{"_ref": f"https://rally.../task/{i}"} for i in range(8)]

        team_workflows.create_feature_with_stages(
            api=mock_api,
            name="Test Feature",
            parent_ref="https://rally.../capability/100"
        )

        # Check all task creation calls - should have specific estimates
        task_calls = [call for call in mock_api.create_object.call_args_list
                     if call[0][0] == "task"]

        # Expected estimates: 1, 3, 4, 1, 1, 1, 1, 4 hours
        expected_estimates = [1, 3, 4, 1, 1, 1, 1, 4]
        actual_estimates = [task_call[0][1]["Estimate"] for task_call in task_calls]

        assert len(actual_estimates) == 8
        assert sorted(actual_estimates) == sorted(expected_estimates)

    def test_stage_tasks_in_defined_state(self):
        """Test all stage tasks start in Defined state."""
        mock_api = MagicMock()
        mock_api.project_ref = None
        mock_api.create_object.side_effect = [
            {"_ref": "https://rally.../feature/12345"},
            {"_ref": "https://rally.../story/67890"},
        ] + [{"_ref": f"https://rally.../task/{i}"} for i in range(8)]

        team_workflows.create_feature_with_stages(
            api=mock_api,
            name="Test Feature",
            parent_ref="https://rally.../capability/100"
        )

        # Check all task creation calls
        task_calls = [call for call in mock_api.create_object.call_args_list
                     if call[0][0] == "task"]

        for task_call in task_calls:
            task_fields = task_call[0][1]
            assert task_fields["State"] == "Defined"

    def test_stage_21_assigned_to_mike(self):
        """Test Stage 2.1 task is assigned to Mike when ref provided."""
        mock_api = MagicMock()
        mock_api.project_ref = None
        mock_api.create_object.side_effect = [
            {"_ref": "https://rally.../feature/12345"},
            {"_ref": "https://rally.../story/67890"},
        ] + [{"_ref": f"https://rally.../task/{i}"} for i in range(8)]

        mike_ref = "https://rally.../user/mike123"

        team_workflows.create_feature_with_stages(
            api=mock_api,
            name="Test Feature",
            parent_ref="https://rally.../capability/100",
            mike_ref=mike_ref
        )

        # Find Stage 2.1 task call
        task_calls = [call for call in mock_api.create_object.call_args_list
                     if call[0][0] == "task"]

        stage_21_call = None
        for task_call in task_calls:
            if "[Stage 2.1]" in task_call[0][1]["Name"]:
                stage_21_call = task_call
                break

        assert stage_21_call is not None
        assert stage_21_call[0][1]["Owner"] == mike_ref

    def test_specific_stages_assigned_to_mike(self):
        """Test Stage 2.1, 2.2, and 4.1 are assigned to Mike."""
        mock_api = MagicMock()
        mock_api.project_ref = None
        mock_api.create_object.side_effect = [
            {"_ref": "https://rally.../feature/12345"},
            {"_ref": "https://rally.../story/67890"},
        ] + [{"_ref": f"https://rally.../task/{i}"} for i in range(8)]

        mike_ref = "https://rally.../user/mike123"

        team_workflows.create_feature_with_stages(
            api=mock_api,
            name="Test Feature",
            parent_ref="https://rally.../capability/100",
            mike_ref=mike_ref
        )

        # Check that Stage 2.1, 2.2, and 4.1 are assigned to Mike
        task_calls = [call for call in mock_api.create_object.call_args_list
                     if call[0][0] == "task"]

        mike_stages = ["[Stage 2.1]", "[Stage 2.2]", "[Stage 4.1]"]
        mike_assigned_count = 0

        for task_call in task_calls:
            task_name = task_call[0][1]["Name"]
            has_owner = "Owner" in task_call[0][1]

            if any(stage in task_name for stage in mike_stages):
                assert has_owner, f"{task_name} should have Owner assigned"
                assert task_call[0][1]["Owner"] == mike_ref
                mike_assigned_count += 1
            else:
                # Other stages should not have Owner assigned
                assert not has_owner or task_call[0][1].get("Owner") != mike_ref

        assert mike_assigned_count == 3, "Expected 3 tasks assigned to Mike"

    def test_tasks_linked_to_admin_story(self):
        """Test all stage tasks are linked to Administrative Tasks story."""
        mock_api = MagicMock()
        mock_api.project_ref = None
        admin_story_ref = "https://rally.../story/67890"
        mock_api.create_object.side_effect = [
            {"_ref": "https://rally.../feature/12345"},
            {"_ref": admin_story_ref},
        ] + [{"_ref": f"https://rally.../task/{i}"} for i in range(8)]

        team_workflows.create_feature_with_stages(
            api=mock_api,
            name="Test Feature",
            parent_ref="https://rally.../capability/100"
        )

        # Check all task creation calls link to admin story
        task_calls = [call for call in mock_api.create_object.call_args_list
                     if call[0][0] == "task"]

        for task_call in task_calls:
            assert task_call[0][1]["WorkProduct"] == admin_story_ref

    def test_admin_story_linked_to_feature(self):
        """Test Administrative Tasks story is linked to the feature."""
        mock_api = MagicMock()
        mock_api.project_ref = None
        feature_ref = "https://rally.../feature/12345"
        mock_api.create_object.side_effect = [
            {"_ref": feature_ref},
            {"_ref": "https://rally.../story/67890"},
        ] + [{"_ref": f"https://rally.../task/{i}"} for i in range(8)]

        team_workflows.create_feature_with_stages(
            api=mock_api,
            name="Test Feature",
            parent_ref="https://rally.../capability/100"
        )

        # Find admin story creation call
        admin_story_call = None
        for call in mock_api.create_object.call_args_list:
            if call[0][0] == "hierarchicalrequirement":
                admin_story_call = call
                break

        assert admin_story_call is not None
        assert admin_story_call[0][1]["PortfolioItem"] == feature_ref

    def test_returns_complete_result_structure(self):
        """Test function returns complete result with feature, story, and tasks."""
        mock_api = MagicMock()
        mock_api.project_ref = None
        mock_api.create_object.side_effect = [
            {"FormattedID": "F12345", "_ref": "https://rally.../feature/12345"},
            {"FormattedID": "US67890", "_ref": "https://rally.../story/67890"},
        ] + [{"FormattedID": f"TA{i}", "_ref": f"https://rally.../task/{i}"}
             for i in range(100, 108)]

        result = team_workflows.create_feature_with_stages(
            api=mock_api,
            name="Test Feature",
            parent_ref="https://rally.../capability/100"
        )

        assert "feature" in result
        assert "admin_story" in result
        assert "tasks" in result
        assert result["feature"]["FormattedID"] == "F12345"
        assert result["admin_story"]["FormattedID"] == "US67890"
        assert len(result["tasks"]) == 8

    def test_stage_tasks_have_descriptions(self):
        """Test all stage tasks have descriptive content."""
        mock_api = MagicMock()
        mock_api.project_ref = None
        mock_api.create_object.side_effect = [
            {"_ref": "https://rally.../feature/12345"},
            {"_ref": "https://rally.../story/67890"},
        ] + [{"_ref": f"https://rally.../task/{i}"} for i in range(8)]

        team_workflows.create_feature_with_stages(
            api=mock_api,
            name="Test Feature",
            parent_ref="https://rally.../capability/100"
        )

        # Check all task creation calls have descriptions
        task_calls = [call for call in mock_api.create_object.call_args_list
                     if call[0][0] == "task"]

        for task_call in task_calls:
            task_fields = task_call[0][1]
            assert "Description" in task_fields
            assert len(task_fields["Description"]) > 0
            assert "<p>" in task_fields["Description"]  # HTML formatted

    def test_parallel_task_creation(self):
        """Test tasks are created in parallel using ThreadPoolExecutor."""
        mock_api = MagicMock()
        mock_api.project_ref = None

        # Create return values for feature, admin story, and tasks
        feature_ref = {"_ref": "https://rally.../feature/12345"}
        story_ref = {"_ref": "https://rally.../story/67890"}
        task_refs = [{"_ref": f"https://rally.../task/{i}"} for i in range(8)]

        mock_api.create_object.side_effect = [feature_ref, story_ref] + task_refs

        with patch('rally_api.team_workflows.ThreadPoolExecutor') as mock_executor_class:
            with patch('rally_api.team_workflows.as_completed') as mock_as_completed:
                mock_executor_instance = MagicMock()
                mock_executor_class.return_value.__enter__.return_value = mock_executor_instance

                # Mock futures for tasks
                mock_futures = []
                for i in range(8):
                    future = MagicMock()
                    future.result.return_value = task_refs[i]
                    mock_futures.append(future)

                # Mock submit to return futures
                mock_executor_instance.submit.side_effect = mock_futures

                # Mock as_completed to return the futures
                mock_as_completed.return_value = mock_futures

                result = team_workflows.create_feature_with_stages(
                    api=mock_api,
                    name="Test Feature",
                    parent_ref="https://rally.../capability/100"
                )

                # Verify ThreadPoolExecutor was used
                mock_executor_class.assert_called_once()
                # Verify tasks were submitted
                assert mock_executor_instance.submit.call_count == 8
                # Verify result contains tasks
                assert "tasks" in result
                assert len(result["tasks"]) == 8
