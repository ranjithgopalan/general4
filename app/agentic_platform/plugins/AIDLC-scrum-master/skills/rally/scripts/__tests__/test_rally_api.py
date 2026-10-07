"""
Tests for Rally API Client

Uses mocking to avoid actual API calls.
Run with: pytest test_rally_api.py -v
"""

import json
import pytest
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
from rally_api import (
    RallyAPI,
    RALLY_BASE_URL,
    RallyAPIKeyError,
    save_api_key,
    get_portfolio_ids,
    CONFIG_PATH,
    create_feature_with_stages,
)
from rally_api.config import load_api_key as _load_api_key, _load_config


@pytest.fixture
def api():
    """Create a RallyAPI instance with mocked session."""
    with patch('rally_api.client.requests.Session') as mock_session_class:
        mock_session = MagicMock()
        mock_session_class.return_value = mock_session
        api = RallyAPI("test_api_key")
        api.session = mock_session
        api.workspace_ref = "https://rally1.rallydev.com/slm/webservice/v2.0/workspace/12345"
        api.project_ref = "https://rally1.rallydev.com/slm/webservice/v2.0/project/67890"
        return api


@pytest.fixture
def temp_config_dir(tmp_path):
    """Create a temporary config directory."""
    config_dir = tmp_path / ".claude"
    config_dir.mkdir()
    return config_dir


class TestAPIKeyConfiguration:
    """Tests for API key loading and saving."""

    def test_load_api_key_missing_file(self, tmp_path):
        """Test error when config file doesn't exist."""
        with patch('rally_api.CONFIG_PATH', tmp_path / "nonexistent" / "aig.json"):
            with pytest.raises(RallyAPIKeyError) as exc_info:
                _load_api_key()
            assert "not found" in str(exc_info.value)

    def test_load_api_key_missing_key(self, temp_config_dir):
        """Test error when config exists but key is missing."""
        config_file = temp_config_dir / "aig.json"
        config_file.write_text('{"rally": {"other_key": "value"}}', encoding="utf-8")

        with patch('rally_api.CONFIG_PATH', config_file):
            with pytest.raises(RallyAPIKeyError) as exc_info:
                _load_api_key()
            assert "not found" in str(exc_info.value)

    def test_load_api_key_missing_rally_section(self, temp_config_dir):
        """Test error when rally section is missing."""
        config_file = temp_config_dir / "aig.json"
        config_file.write_text('{"other_section": {}}', encoding="utf-8")

        with patch('rally_api.CONFIG_PATH', config_file):
            with pytest.raises(RallyAPIKeyError) as exc_info:
                _load_api_key()
            assert "not found" in str(exc_info.value)

    def test_load_api_key_invalid_json(self, temp_config_dir):
        """Test error when config file has invalid JSON."""
        config_file = temp_config_dir / "aig.json"
        config_file.write_text('not valid json', encoding="utf-8")

        with patch('rally_api.CONFIG_PATH', config_file):
            with pytest.raises(RallyAPIKeyError) as exc_info:
                _load_api_key()
            assert "Invalid JSON" in str(exc_info.value)

    def test_load_api_key_success(self, temp_config_dir):
        """Test successful API key loading with nested format."""
        config_file = temp_config_dir / "aig.json"
        config_file.write_text('{"rally": {"api_key": "test_key_123"}}', encoding="utf-8")

        with patch('rally_api.CONFIG_PATH', config_file):
            key = _load_api_key()
            assert key == "test_key_123"

    def test_save_api_key_new_file(self, temp_config_dir):
        """Test saving API key to new file."""
        config_file = temp_config_dir / "aig.json"

        with patch('rally_api.CONFIG_PATH', config_file):
            save_api_key("new_test_key")

        saved = json.loads(config_file.read_text(encoding="utf-8"))
        assert saved["rally"]["api_key"] == "new_test_key"

    def test_save_api_key_preserves_existing(self, temp_config_dir):
        """Test that saving API key preserves other config values."""
        config_file = temp_config_dir / "aig.json"
        config_file.write_text('{"other_setting": "keep_me", "rally": {"portfolio_ids": {}}}', encoding="utf-8")

        with patch('rally_api.CONFIG_PATH', config_file):
            save_api_key("new_key")

        saved = json.loads(config_file.read_text(encoding="utf-8"))
        assert saved["rally"]["api_key"] == "new_key"
        assert saved["other_setting"] == "keep_me"
        assert "portfolio_ids" in saved["rally"]

    def test_save_api_key_creates_directory(self, tmp_path):
        """Test that save_api_key creates parent directory if needed."""
        config_file = tmp_path / "new_dir" / ".claude" / "aig.json"

        with patch('rally_api.CONFIG_PATH', config_file):
            save_api_key("test_key")

        assert config_file.exists()
        saved = json.loads(config_file.read_text(encoding="utf-8"))
        assert saved["rally"]["api_key"] == "test_key"

    def test_get_portfolio_ids_success(self, temp_config_dir):
        """Test loading portfolio IDs from config."""
        config_file = temp_config_dir / "aig.json"
        config_file.write_text(json.dumps({
            "rally": {
                "api_key": "test",
                "portfolio_ids": {
                    "forward_engineering": "123",
                    "genlite": "456"
                }
            }
        }), encoding="utf-8")

        with patch('rally_api.CONFIG_PATH', config_file):
            ids = get_portfolio_ids()
            assert ids["forward_engineering"] == "123"
            assert ids["genlite"] == "456"

    def test_get_portfolio_ids_missing(self, temp_config_dir):
        """Test portfolio IDs returns empty dict when not configured."""
        config_file = temp_config_dir / "aig.json"
        config_file.write_text('{"rally": {"api_key": "test"}}', encoding="utf-8")

        with patch('rally_api.CONFIG_PATH', config_file):
            ids = get_portfolio_ids()
            assert ids == {}

    def test_get_portfolio_ids_no_config_file(self, tmp_path):
        """Test portfolio IDs returns empty dict when config file missing."""
        with patch('rally_api.CONFIG_PATH', tmp_path / "nonexistent.json"):
            ids = get_portfolio_ids()
            assert ids == {}


class TestRallyAPIInit:
    """Tests for RallyAPI initialization."""

    def test_init_with_api_key(self):
        """Test initialization with explicit API key."""
        with patch('rally_api.requests.Session'):
            api = RallyAPI("test_key")
            assert api.api_key == "test_key"

    def test_init_loads_from_config(self, temp_config_dir):
        """Test initialization loads API key from config file."""
        config_file = temp_config_dir / "aig.json"
        config_file.write_text('{"rally": {"api_key": "config_key"}}', encoding="utf-8")

        with patch('rally_api.CONFIG_PATH', config_file):
            with patch('rally_api.requests.Session'):
                api = RallyAPI()
                assert api.api_key == "config_key"

    def test_init_raises_when_no_key(self, tmp_path):
        """Test initialization raises error when no key available."""
        with patch('rally_api.CONFIG_PATH', tmp_path / "nonexistent.json"):
            with patch('rally_api.requests.Session'):
                with pytest.raises(RallyAPIKeyError):
                    RallyAPI()


class TestQueryMethods:
    """Tests for query methods."""

    def test_query_basic(self, api):
        """Test basic query."""
        api.session.get.return_value.json.return_value = {
            "QueryResult": {
                "Results": [
                    {"Name": "Test Story", "FormattedID": "US12345"}
                ]
            }
        }

        results = api.query("hierarchicalrequirement")

        assert len(results) == 1
        assert results[0]["Name"] == "Test Story"

    def test_query_with_filter(self, api):
        """Test query with filter."""
        api.session.get.return_value.json.return_value = {
            "QueryResult": {"Results": []}
        }

        api.query(
            "hierarchicalrequirement",
            query='(Name contains "test")',
            fetch="Name,FormattedID"
        )

        call_args = api.session.get.call_args
        assert 'query' in call_args[1]['params']

    def test_get_user_stories(self, api):
        """Test get_user_stories method."""
        api.session.get.return_value.json.return_value = {
            "QueryResult": {
                "Results": [
                    {"Name": "Story 1", "FormattedID": "US001"},
                    {"Name": "Story 2", "FormattedID": "US002"},
                ]
            }
        }

        stories = api.get_user_stories()

        assert len(stories) == 2

    def test_get_features(self, api):
        """Test get_features method."""
        api.session.get.return_value.json.return_value = {
            "QueryResult": {
                "Results": [
                    {"Name": "Feature 1", "FormattedID": "F001"}
                ]
            }
        }

        features = api.get_features()

        assert len(features) == 1
        assert features[0]["FormattedID"] == "F001"

    def test_get_iterations(self, api):
        """Test get_iterations method."""
        api.session.get.return_value.json.return_value = {
            "QueryResult": {
                "Results": [
                    {"Name": "Sprint 1", "StartDate": "2025-01-01"}
                ]
            }
        }

        iterations = api.get_iterations()

        assert len(iterations) == 1

    def test_get_tasks(self, api):
        """Test get_tasks method."""
        api.session.get.return_value.json.return_value = {
            "QueryResult": {
                "Results": [
                    {"Name": "Task 1", "State": "Defined"}
                ]
            }
        }

        tasks = api.get_tasks()

        assert len(tasks) == 1

    def test_get_defects(self, api):
        """Test get_defects method."""
        api.session.get.return_value.json.return_value = {
            "QueryResult": {
                "Results": [
                    {"Name": "Bug 1", "State": "Open"}
                ]
            }
        }

        defects = api.get_defects()

        assert len(defects) == 1


class TestGetObject:
    """Tests for get_object method."""

    def test_get_object_story(self, api):
        """Test getting a user story."""
        api.session.get.return_value.json.return_value = {
            "HierarchicalRequirement": {
                "Name": "Test Story",
                "FormattedID": "US12345"
            }
        }
        api.session.get.return_value.raise_for_status = Mock()

        result = api.get_object("https://rally1.rallydev.com/.../hierarchicalrequirement/12345")

        assert result["Name"] == "Test Story"

    def test_get_object_feature(self, api):
        """Test getting a feature."""
        api.session.get.return_value.json.return_value = {
            "Feature": {
                "Name": "Test Feature",
                "FormattedID": "F12345"
            }
        }
        api.session.get.return_value.raise_for_status = Mock()

        result = api.get_object("https://rally1.rallydev.com/.../portfolioitem/feature/12345")

        assert result["Name"] == "Test Feature"


class TestCreateMethods:
    """Tests for create methods."""

    def test_create_object(self, api):
        """Test create_object method."""
        api.session.post.return_value.json.return_value = {
            "CreateResult": {
                "Object": {
                    "Name": "New Story",
                    "FormattedID": "US99999",
                    "_ref": "https://rally1.rallydev.com/.../hierarchicalrequirement/99999"
                },
                "Errors": []
            }
        }
        api.session.post.return_value.raise_for_status = Mock()

        result = api.create_object("hierarchicalrequirement", {"Name": "New Story"})

        assert result["FormattedID"] == "US99999"

    def test_create_feature(self, api):
        """Test create_feature method."""
        api.session.post.return_value.json.return_value = {
            "CreateResult": {
                "Object": {
                    "Name": "New Feature",
                    "FormattedID": "F99999",
                    "_ref": "https://rally1.rallydev.com/.../portfolioitem/feature/99999"
                },
                "Errors": []
            }
        }
        api.session.post.return_value.raise_for_status = Mock()

        result = api.create_feature(
            name="New Feature",
            parent_ref="https://rally1.rallydev.com/.../portfolioitem/capability/123"
        )

        assert result["FormattedID"] == "F99999"

    def test_create_user_story(self, api):
        """Test create_user_story method."""
        # Mock for get_object (feature details)
        api.session.get.return_value.json.return_value = {
            "Feature": {
                "Owner": {"_ref": "https://rally1.rallydev.com/.../user/123"},
                "Release": {"_ref": "https://rally1.rallydev.com/.../release/456"}
            }
        }
        api.session.get.return_value.raise_for_status = Mock()

        # Mock for create
        api.session.post.return_value.json.return_value = {
            "CreateResult": {
                "Object": {
                    "Name": "New Story",
                    "FormattedID": "US99999",
                    "_ref": "https://rally1.rallydev.com/.../hierarchicalrequirement/99999"
                },
                "Errors": []
            }
        }
        api.session.post.return_value.raise_for_status = Mock()

        result = api.create_user_story(
            feature_ref="https://rally1.rallydev.com/.../portfolioitem/feature/123",
            name="Test Story",
            role="developer",
            want="to test the API",
            so_that="I can verify it works",
            acceptance_criteria=["Criterion 1", "Criterion 2"]
        )

        assert result["FormattedID"] == "US99999"


class TestValidateBeforeCreate:
    """Tests for validate_before_create method."""

    def test_no_duplicates(self, api):
        """Test when no duplicates exist."""
        # Mock query to return empty results
        api.session.get.return_value.json.return_value = {
            "QueryResult": {"Results": []}
        }

        result = api.validate_before_create(
            object_type="hierarchicalrequirement",
            name="Unique Story Name",
            parent_ref="https://rally1.rallydev.com/.../portfolioitem/feature/123"
        )

        assert result["has_duplicate"] is False
        assert len(result["duplicates"]) == 0

    def test_with_duplicates(self, api):
        """Test when duplicates exist."""
        api.session.get.return_value.json.return_value = {
            "QueryResult": {
                "Results": [
                    {"Name": "Duplicate Story", "FormattedID": "US001"}
                ]
            }
        }

        result = api.validate_before_create(
            object_type="hierarchicalrequirement",
            name="Duplicate Story"
        )

        assert result["has_duplicate"] is True
        assert len(result["duplicates"]) == 1


class TestSearchByName:
    """Tests for search_by_name method."""

    def test_contains_search(self, api):
        """Test contains search."""
        api.session.get.return_value.json.return_value = {
            "QueryResult": {
                "Results": [
                    {"Name": "OAuth Login", "FormattedID": "US001"},
                    {"Name": "OAuth Logout", "FormattedID": "US002"},
                ]
            }
        }

        results = api.search_by_name("hierarchicalrequirement", "OAuth")

        assert len(results) == 2

    def test_exact_search(self, api):
        """Test exact search."""
        api.session.get.return_value.json.return_value = {
            "QueryResult": {
                "Results": [
                    {"Name": "OAuth Login", "FormattedID": "US001"}
                ]
            }
        }

        results = api.search_by_name("hierarchicalrequirement", "OAuth Login", exact=True)

        assert len(results) == 1


class TestGetChildren:
    """Tests for get_children method."""

    def test_get_feature_children(self, api):
        """Test getting children of a feature (user stories)."""
        api.session.get.return_value.json.return_value = {
            "QueryResult": {
                "Results": [
                    {"Name": "Story 1", "FormattedID": "US001"},
                    {"Name": "Story 2", "FormattedID": "US002"},
                ]
            }
        }

        children = api.get_children(
            "https://rally1.rallydev.com/.../portfolioitem/feature/123"
        )

        assert len(children) == 2

    def test_get_capability_children(self, api):
        """Test getting children of a capability (features)."""
        api.session.get.return_value.json.return_value = {
            "QueryResult": {
                "Results": [
                    {"Name": "Feature 1", "FormattedID": "F001"}
                ]
            }
        }

        children = api.get_children(
            "https://rally1.rallydev.com/.../portfolioitem/capability/123"
        )

        assert len(children) == 1


class TestMoveStory:
    """Tests for move_story method."""

    def test_move_story_with_inheritance(self, api):
        """Test moving story with Release/Iteration inheritance."""
        # Mock get_object for feature
        api.session.get.return_value.json.return_value = {
            "Feature": {
                "Release": {"_ref": "https://rally1.rallydev.com/.../release/456"},
                "Iteration": {"_ref": "https://rally1.rallydev.com/.../iteration/789"}
            }
        }
        api.session.get.return_value.raise_for_status = Mock()

        # Mock update
        api.session.post.return_value.json.return_value = {
            "OperationResult": {
                "Object": {"FormattedID": "US001"},
                "Errors": []
            }
        }
        api.session.post.return_value.raise_for_status = Mock()

        result = api.move_story(
            story_ref="https://rally1.rallydev.com/.../hierarchicalrequirement/123",
            new_feature_ref="https://rally1.rallydev.com/.../portfolioitem/feature/456"
        )

        # Verify the update was called
        assert api.session.post.called


class TestCloneItem:
    """Tests for clone_item method."""

    def test_clone_story(self, api):
        """Test cloning a story."""
        # Mock get_object for source
        api.session.get.return_value.json.return_value = {
            "HierarchicalRequirement": {
                "Name": "Original Story",
                "Description": "Test description",
                "ScheduleState": "Completed",
                "PortfolioItem": {"_ref": "https://rally1.rallydev.com/.../portfolioitem/feature/123"}
            }
        }
        api.session.get.return_value.raise_for_status = Mock()

        # Mock create
        api.session.post.return_value.json.return_value = {
            "CreateResult": {
                "Object": {
                    "Name": "Copy of Original Story",
                    "FormattedID": "US999",
                    "_ref": "https://rally1.rallydev.com/.../hierarchicalrequirement/999"
                },
                "Errors": []
            }
        }
        api.session.post.return_value.raise_for_status = Mock()

        result = api.clone_item(
            source_ref="https://rally1.rallydev.com/.../hierarchicalrequirement/123"
        )

        assert "FormattedID" in result

    def test_clone_with_new_name(self, api):
        """Test cloning with custom name."""
        api.session.get.return_value.json.return_value = {
            "HierarchicalRequirement": {
                "Name": "Original",
                "PortfolioItem": {"_ref": "https://rally1.rallydev.com/.../portfolioitem/feature/123"}
            }
        }
        api.session.get.return_value.raise_for_status = Mock()

        api.session.post.return_value.json.return_value = {
            "CreateResult": {
                "Object": {"Name": "Custom Name", "FormattedID": "US999"},
                "Errors": []
            }
        }
        api.session.post.return_value.raise_for_status = Mock()

        result = api.clone_item(
            source_ref="https://rally1.rallydev.com/.../hierarchicalrequirement/123",
            new_name="Custom Name"
        )

        # Check that post was called with the new name
        call_args = api.session.post.call_args
        assert "Custom Name" in str(call_args)


class TestBulkCreateStories:
    """Tests for bulk_create_stories method."""

    def test_bulk_create(self, api):
        """Test bulk creating stories."""
        # Mock get_object for feature
        api.session.get.return_value.json.return_value = {
            "Feature": {
                "Owner": {"_ref": "https://rally1.rallydev.com/.../user/123"},
                "Release": {"_ref": "https://rally1.rallydev.com/.../release/456"},
                "Iteration": {"_ref": "https://rally1.rallydev.com/.../iteration/789"}
            }
        }
        api.session.get.return_value.raise_for_status = Mock()

        # Mock create
        create_count = [0]
        def mock_post(*args, **kwargs):
            create_count[0] += 1
            mock_response = Mock()
            mock_response.json.return_value = {
                "CreateResult": {
                    "Object": {
                        "Name": f"Story {create_count[0]}",
                        "FormattedID": f"US{create_count[0]:03d}"
                    },
                    "Errors": []
                }
            }
            mock_response.raise_for_status = Mock()
            return mock_response

        api.session.post.side_effect = mock_post

        stories = api.bulk_create_stories(
            feature_ref="https://rally1.rallydev.com/.../portfolioitem/feature/123",
            stories=[
                {"name": "Story 1"},
                {"name": "Story 2"},
                {"name": "Story 3"}
            ]
        )

        assert len(stories) == 3


class TestCreateFeatureWithStages:
    """Tests for create_feature_with_stages method."""

    def test_creates_feature_and_stages(self, api):
        """Test that feature and all stages are created."""
        create_count = [0]
        created_items = []

        def mock_post(*args, **kwargs):
            create_count[0] += 1
            mock_response = Mock()

            # Parse the data to get the name
            data = kwargs.get('json', args[1] if len(args) > 1 else {})
            name = "Unknown"
            for key in ['PortfolioItem', 'HierarchicalRequirement', 'Task']:
                if key in data:
                    name = data[key].get('Name', 'Unknown')
                    break

            created_items.append(name)

            mock_response.json.return_value = {
                "CreateResult": {
                    "Object": {
                        "Name": name,
                        "FormattedID": f"ID{create_count[0]:03d}",
                        "_ref": f"https://rally1.rallydev.com/.../object/{create_count[0]}"
                    },
                    "Errors": []
                }
            }
            mock_response.raise_for_status = Mock()
            return mock_response

        api.session.post.side_effect = mock_post

        result = create_feature_with_stages(
            api=api,
            name="Test Feature",
            parent_ref="https://rally1.rallydev.com/.../portfolioitem/capability/123",
            description="Test description"
        )

        # Should have feature + 1 admin story + 8 tasks = 10 creates
        assert result["feature"] is not None
        assert result["admin_story"] is not None
        assert len(result["tasks"]) == 8

        # Verify admin story name
        assert result["admin_story"]["Name"] == "Administrative Tasks"

        # Verify task names
        task_names = [result["tasks"][k]["Name"] for k in result["tasks"]]
        assert any("[Stage 1.1]" in name for name in task_names)
        assert any("[Stage 2.1]" in name for name in task_names)
        assert any("[Stage 4.1]" in name for name in task_names)


class TestUpdateObject:
    """Tests for update_object method."""

    def test_update_object(self, api):
        """Test updating an object."""
        api.session.post.return_value.json.return_value = {
            "OperationResult": {
                "Object": {"FormattedID": "US001", "ScheduleState": "In-Progress"},
                "Errors": []
            }
        }
        api.session.post.return_value.raise_for_status = Mock()

        result = api.update_object(
            "https://rally1.rallydev.com/.../hierarchicalrequirement/123",
            {"ScheduleState": "In-Progress"}
        )

        assert api.session.post.called


class TestDeleteObject:
    """Tests for delete_object method."""

    def test_delete_with_backup(self, api):
        """Test delete with backup."""
        # Mock get for backup
        api.session.get.return_value.status_code = 200
        api.session.get.return_value.json.return_value = {
            "HierarchicalRequirement": {
                "Name": "Test",
                "FormattedID": "US001"
            }
        }

        # Mock delete
        api.session.delete.return_value.raise_for_status = Mock()

        # Mock query for attachments
        with patch.object(api, 'query', return_value=[]):
            with patch('os.makedirs'):
                with patch('builtins.open', create=True):
                    api.delete_object(
                        "https://rally1.rallydev.com/.../hierarchicalrequirement/123"
                    )

        assert api.session.delete.called


class TestUploadAttachment:
    """Tests for upload_attachment method."""

    def test_upload_attachment(self, api):
        """Test uploading an attachment."""
        # Mock content create
        api.session.post.return_value.json.side_effect = [
            {
                "CreateResult": {
                    "Object": {"_ref": "https://rally1.rallydev.com/.../attachmentcontent/123"},
                    "Errors": []
                }
            },
            {
                "CreateResult": {
                    "Object": {
                        "Name": "test.html",
                        "_ref": "https://rally1.rallydev.com/.../attachment/456"
                    },
                    "Errors": []
                }
            }
        ]
        api.session.post.return_value.raise_for_status = Mock()

        result = api.upload_attachment(
            artifact_ref="https://rally1.rallydev.com/.../hierarchicalrequirement/123",
            filename="test.html",
            content=b"<html>Test</html>",
            content_type="text/html"
        )

        assert api.session.post.call_count == 2


class TestReleaseForIteration:
    """Tests for find_release_for_iteration and prepare_iteration_and_release methods."""

    def test_find_release_for_iteration_success(self, api):
        """Test finding a release that contains an iteration's date range."""
        # Mock iteration with dates
        iteration = {
            "Name": "2026.PI1.Iteration1",
            "StartDate": "2026-01-13T00:00:00.000Z",
            "EndDate": "2026-01-26T23:59:59.999Z",
            "_ref": "https://rally1.rallydev.com/.../iteration/123"
        }

        # Mock releases query - one release contains this iteration
        api.session.get.return_value.json.return_value = {
            "QueryResult": {
                "Results": [
                    {
                        "Name": "2025.PI5",
                        "ReleaseStartDate": "2025-10-01T00:00:00.000Z",
                        "ReleaseDate": "2025-12-31T23:59:59.999Z",
                        "_ref": "https://rally1.rallydev.com/.../release/100"
                    },
                    {
                        "Name": "2026.PI1",
                        "ReleaseStartDate": "2026-01-01T00:00:00.000Z",
                        "ReleaseDate": "2026-03-31T23:59:59.999Z",
                        "_ref": "https://rally1.rallydev.com/.../release/200"
                    },
                    {
                        "Name": "2026.PI2",
                        "ReleaseStartDate": "2026-04-01T00:00:00.000Z",
                        "ReleaseDate": "2026-06-30T23:59:59.999Z",
                        "_ref": "https://rally1.rallydev.com/.../release/300"
                    }
                ]
            }
        }
        api.session.get.return_value.raise_for_status = Mock()

        release = api.find_release_for_iteration(iteration)

        assert release is not None
        assert release["Name"] == "2026.PI1"
        assert release["_ref"] == "https://rally1.rallydev.com/.../release/200"

    def test_find_release_for_iteration_not_found(self, api):
        """Test when no release contains the iteration."""
        iteration = {
            "Name": "2027.PI1.Iteration1",
            "StartDate": "2027-01-13T00:00:00.000Z",
            "EndDate": "2027-01-26T23:59:59.999Z",
            "_ref": "https://rally1.rallydev.com/.../iteration/123"
        }

        # Mock releases query - no release contains 2027 dates
        api.session.get.return_value.json.return_value = {
            "QueryResult": {
                "Results": [
                    {
                        "Name": "2026.PI1",
                        "ReleaseStartDate": "2026-01-01T00:00:00.000Z",
                        "ReleaseDate": "2026-03-31T23:59:59.999Z",
                        "_ref": "https://rally1.rallydev.com/.../release/200"
                    }
                ]
            }
        }
        api.session.get.return_value.raise_for_status = Mock()

        release = api.find_release_for_iteration(iteration, raise_if_not_found=False)
        assert release is None

        # Test with raise_if_not_found=True
        with pytest.raises(ValueError, match="No release found for iteration"):
            api.find_release_for_iteration(iteration, raise_if_not_found=True)

    def test_find_release_for_iteration_missing_dates(self, api):
        """Test when iteration is missing date fields."""
        iteration = {
            "Name": "2026.PI1.Iteration1",
            "_ref": "https://rally1.rallydev.com/.../iteration/123"
            # Missing StartDate and EndDate
        }

        release = api.find_release_for_iteration(iteration, raise_if_not_found=False)
        assert release is None

        with pytest.raises(ValueError, match="missing StartDate or EndDate"):
            api.find_release_for_iteration(iteration, raise_if_not_found=True)

    def test_prepare_iteration_and_release_auto(self, api):
        """Test prepare_iteration_and_release with auto release detection."""
        # Mock find_iteration
        mock_iteration = {
            "Name": "2026.PI1.Iteration1",
            "StartDate": "2026-01-13T00:00:00.000Z",
            "EndDate": "2026-01-26T23:59:59.999Z",
            "_ref": "https://rally1.rallydev.com/.../iteration/123"
        }

        # Mock find_release_for_iteration
        mock_release = {
            "Name": "2026.PI1",
            "ReleaseStartDate": "2026-01-01T00:00:00.000Z",
            "ReleaseDate": "2026-03-31T23:59:59.999Z",
            "_ref": "https://rally1.rallydev.com/.../release/200"
        }

        with patch.object(api, 'find_iteration', return_value=mock_iteration):
            with patch.object(api, 'find_release_for_iteration', return_value=mock_release):
                fields = api.prepare_iteration_and_release(iteration_name="2026.PI1.Iteration1")

        assert "Iteration" in fields
        assert fields["Iteration"] == mock_iteration["_ref"]
        assert "Release" in fields
        assert fields["Release"] == mock_release["_ref"]

    def test_prepare_iteration_and_release_manual(self, api):
        """Test prepare_iteration_and_release with manual release specification."""
        mock_iteration = {
            "Name": "2026.PI1.Iteration1",
            "_ref": "https://rally1.rallydev.com/.../iteration/123"
        }
        mock_release = {
            "Name": "2026.PI1",
            "_ref": "https://rally1.rallydev.com/.../release/200"
        }

        with patch.object(api, 'find_iteration', return_value=mock_iteration):
            with patch.object(api, 'find_release', return_value=mock_release):
                fields = api.prepare_iteration_and_release(
                    iteration_name="2026.PI1.Iteration1",
                    release_name="2026.PI1",
                    auto_set_release=False
                )

        assert fields["Iteration"] == mock_iteration["_ref"]
        assert fields["Release"] == mock_release["_ref"]

    def test_prepare_iteration_and_release_no_release(self, api):
        """Test prepare_iteration_and_release when no release is found."""
        mock_iteration = {
            "Name": "2027.PI1.Iteration1",
            "StartDate": "2027-01-13T00:00:00.000Z",
            "EndDate": "2027-01-26T23:59:59.999Z",
            "_ref": "https://rally1.rallydev.com/.../iteration/123"
        }

        with patch.object(api, 'find_iteration', return_value=mock_iteration):
            with patch.object(api, 'find_release_for_iteration', return_value=None):
                fields = api.prepare_iteration_and_release(iteration_name="2027.PI1.Iteration1")

        # Should still have Iteration, but no Release
        assert "Iteration" in fields
        assert fields["Iteration"] == mock_iteration["_ref"]
        assert "Release" not in fields

    def test_prepare_iteration_and_release_only_release(self, api):
        """Test prepare_iteration_and_release with only release specified."""
        mock_release = {
            "Name": "2026.PI1",
            "_ref": "https://rally1.rallydev.com/.../release/200"
        }

        with patch.object(api, 'find_release', return_value=mock_release):
            fields = api.prepare_iteration_and_release(release_name="2026.PI1")

        assert "Release" in fields
        assert fields["Release"] == mock_release["_ref"]
        assert "Iteration" not in fields


class TestBulkCreateWithRationaleNotes:
    """Integration tests for US789694: Story point estimation rationale in Rally Notes."""

    def test_bulk_create_with_rationale_notes(self, api):
        """Test that bulk_create_stories populates Notes field with estimation rationale."""
        feature_ref = "https://rally1.rallydev.com/.../portfolioitem/feature/12345"

        # Mock feature fetch
        api.session.get.return_value.json.return_value = {
            "PortfolioItem": {
                "Name": "Test Feature",
                "Owner": {"_ref": "https://rally1.rallydev.com/.../user/111"},
                "Release": {"_ref": "https://rally1.rallydev.com/.../release/222"},
                "Iteration": {"_ref": "https://rally1.rallydev.com/.../iteration/333"}
            }
        }
        api.session.get.return_value.raise_for_status = Mock()

        # Mock story creation
        created_story = {
            "Name": "Test Story",
            "PlanEstimate": 5,
            "Notes": "",
            "_ref": "https://rally1.rallydev.com/.../hierarchicalrequirement/444"
        }
        api.session.post.return_value.json.return_value = {
            "CreateResult": {
                "Object": created_story,
                "Errors": [],
                "Warnings": []
            }
        }
        api.session.post.return_value.raise_for_status = Mock()

        # Create story with auto-estimation enabled
        stories = [
            {
                "name": "Test Story",
                "role": "user",
                "want": "feature with complex algorithm and API integration",
                "so_that": "I can process data efficiently",
                "acceptance_criteria": ["Optimization algorithm implemented", "API integration complete"]
            }
        ]

        # Capture the POST request to verify Notes field
        post_calls = []
        original_post = api.session.post

        def capture_post(*args, **kwargs):
            post_calls.append((args, kwargs))
            return original_post(*args, **kwargs)

        api.session.post = capture_post

        # Execute bulk_create_stories with auto_estimate=True
        result = api.bulk_create_stories(
            feature_ref=feature_ref,
            stories=stories,
            inherit_from_feature=True,
            parallel=False,
            auto_estimate=True,
            use_ai_estimation=True
        )

        # Verify story was created
        assert len(result) == 1
        assert len(post_calls) == 1

        # Extract the fields from the POST call
        post_data = post_calls[0][1]['json']['HierarchicalRequirement']

        # Verify Notes field contains estimation rationale
        assert "Notes" in post_data
        notes = post_data["Notes"]
        assert "## Story Point Estimation Rationale" in notes
        assert "**Estimate:**" in notes
        assert "**Complexity Factors:**" in notes
        assert "**Key Drivers:**" in notes
        assert "**Assumptions:**" in notes
        assert "**Recommendation:**" in notes
        assert "*Generated by GATHER-scrum-master estimation system*" in notes

        # Verify PlanEstimate was set
        assert "PlanEstimate" in post_data
        assert post_data["PlanEstimate"] > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
