"""
Unit tests for rally_api.config module.

Tests configuration management including:
- Personal API config (~/.claude/aig.json)
- Project team config (.claude/.fe-sm/config/team.json)
- Project settings config (.claude/.fe-sm/config/settings.json)
- Team member lookup and management
- Configuration loading and defaults
"""

import pytest
from unittest.mock import MagicMock, patch, mock_open
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from rally_api import config


class TestPersonalConfig:
    """Tests for personal API configuration (~/.claude/aig.json)."""

    @patch('pathlib.Path.exists')
    @patch('builtins.open', new_callable=mock_open, read_data='{"rally": {"api_key": "test_key"}}')
    def test_load_api_key_success(self, mock_file, mock_exists):
        """Test loading existing API key."""
        mock_exists.return_value = True

        api_key = config.load_api_key()

        assert api_key == "test_key"

    @patch('pathlib.Path.exists')
    def test_load_api_key_file_not_found(self, mock_exists):
        """Test loading API key when file doesn't exist raises clear error."""
        mock_exists.return_value = False

        with pytest.raises(config.RallyAPIKeyError) as exc_info:
            config.load_api_key()

        assert "aig.json" in str(exc_info.value) or "not found" in str(exc_info.value)

    @patch('pathlib.Path.exists')
    @patch('builtins.open', new_callable=mock_open, read_data='{"rally": {}}')
    def test_load_api_key_missing(self, mock_file, mock_exists):
        """Test loading API key when not in config."""
        mock_exists.return_value = True

        with pytest.raises(config.RallyAPIKeyError) as exc_info:
            config.load_api_key()

        assert "API key" in str(exc_info.value)


class TestTeamConfig:
    """Tests for project team configuration (.claude/.fe-sm/config/team.json)."""

    @patch('rally_api.config.PluginPaths.find_config')
    def test_load_team_config_creates_empty(self, mock_find):
        """Test loading team config returns empty when not found."""
        mock_find.return_value = None

        result = config._load_team_config()

        assert result == {"members": []}

    @patch('rally_api.config.PluginPaths.find_config')
    @patch('builtins.open', new_callable=mock_open, read_data='{"members": [{"name": "Chris", "rally_username": "chris@test.com"}]}')
    def test_load_team_config_existing(self, mock_file, mock_find):
        """Test loading existing team config."""
        mock_find.return_value = Path("/test/.claude/.fe-sm/config/team.json")

        result = config._load_team_config()

        assert len(result["members"]) == 1
        assert result["members"][0]["name"] == "Chris"

    def test_get_team_members(self):
        """Test getting all team members."""
        with patch('rally_api.config._load_team_config') as mock_load:
            mock_load.return_value = {
                "members": [
                    {"name": "Chris", "rally_username": "chris@test.com"},
                    {"name": "John", "rally_username": "john@test.com"}
                ]
            }

            members = config.get_team_members()

            assert len(members) == 2
            assert members[0]["name"] == "Chris"

    def test_find_team_member_by_exact_username(self):
        """Test finding team member by exact Rally username."""
        with patch('rally_api.config.get_team_members') as mock_get:
            mock_get.return_value = [
                {"name": "Chris", "rally_username": "christopher.le@aig.com"},
                {"name": "John", "rally_username": "john.doe@aig.com"}
            ]

            member = config.find_team_member("christopher.le@aig.com")

            assert member is not None
            assert member["name"] == "Chris"

    def test_find_team_member_by_exact_name(self):
        """Test finding team member by exact name."""
        with patch('rally_api.config.get_team_members') as mock_get:
            mock_get.return_value = [
                {"name": "Chris", "rally_username": "chris@test.com"},
                {"name": "John", "rally_username": "john@test.com"}
            ]

            member = config.find_team_member("Chris")

            assert member is not None
            assert member["rally_username"] == "chris@test.com"

    def test_find_team_member_by_partial_name(self):
        """Test finding team member by partial name match."""
        with patch('rally_api.config.get_team_members') as mock_get:
            mock_get.return_value = [
                {"name": "Christopher Le", "rally_username": "chris@test.com"}
            ]

            member = config.find_team_member("chris")

            assert member is not None
            assert member["name"] == "Christopher Le"

    def test_find_team_member_not_found(self):
        """Test finding non-existent team member returns None."""
        with patch('rally_api.config.get_team_members') as mock_get:
            mock_get.return_value = [
                {"name": "Chris", "rally_username": "chris@test.com"}
            ]

            member = config.find_team_member("nonexistent")

            assert member is None

    def test_get_member_estimate_multiplier_exists(self):
        """Test getting estimation multiplier for team member."""
        with patch('rally_api.config.find_team_member') as mock_find:
            mock_find.return_value = {
                "name": "Chris",
                "rally_username": "chris@test.com",
                "estimate_multiplier": 0.6
            }

            multiplier = config.get_member_estimate_multiplier("Chris")

            assert multiplier == 0.6

    def test_get_member_estimate_multiplier_default(self):
        """Test getting estimation multiplier defaults to None when not set."""
        with patch('rally_api.config.find_team_member') as mock_find:
            mock_find.return_value = {
                "name": "Chris",
                "rally_username": "chris@test.com"
            }

            multiplier = config.get_member_estimate_multiplier("Chris")

            assert multiplier is None

    def test_get_member_estimate_multiplier_member_not_found(self):
        """Test getting estimation multiplier when member doesn't exist."""
        with patch('rally_api.config.find_team_member') as mock_find:
            mock_find.return_value = None

            multiplier = config.get_member_estimate_multiplier("nonexistent")

            assert multiplier is None

    @patch('rally_api.config.PluginPaths.get_user_config_dir')
    @patch('builtins.open', new_callable=mock_open)
    def test_add_team_member_success(self, mock_file, mock_get_dir):
        """Test adding a new team member."""
        mock_get_dir.return_value = Path("/test/.claude/.fe-sm/config")

        with patch('rally_api.config._load_team_config') as mock_load:
            mock_load.return_value = {"members": []}

            result = config.add_team_member(
                name="Chris",
                rally_username="chris@test.com",
                role="Developer",
                estimate_multiplier=0.6
            )

            assert result["name"] == "Chris"
            assert result["rally_username"] == "chris@test.com"
            assert result["estimate_multiplier"] == 0.6

    def test_add_team_member_duplicate(self):
        """Test adding duplicate team member raises error."""
        with patch('rally_api.config.get_team_members') as mock_get:
            mock_get.return_value = [
                {"name": "Chris", "rally_username": "chris@test.com"}
            ]

            with pytest.raises(ValueError) as exc_info:
                config.add_team_member(
                    name="Chris",
                    rally_username="chris@test.com"
                )

            assert "already exists" in str(exc_info.value).lower()


class TestRulesConfig:
    """Tests for project settings configuration (.claude/.fe-sm/config/settings.json)."""

    @patch('rally_api.config.PluginPaths.find_config')
    def test_load_settings_config_uses_defaults(self, mock_find):
        """Test loading settings config returns defaults when file doesn't exist."""
        mock_find.return_value = None

        result = config._load_settings_config()

        assert "story_points" in result
        assert "validation_rules" in result
        assert result["story_points"]["max_points"] == 7

    @patch('rally_api.config.PluginPaths.find_config')
    @patch('builtins.open', new_callable=mock_open, read_data='{"story_points": {"max_points": 5}}')
    def test_load_settings_config_merges_with_defaults(self, mock_file, mock_find):
        """Test loading settings config merges user settings with defaults."""
        mock_find.return_value = Path("/test/.claude/.fe-sm/config/settings.json")

        result = config._load_settings_config()

        assert result["story_points"]["max_points"] == 5
        assert "validation_rules" in result  # Default preserved

    def test_get_project_rules(self):
        """Test getting all project rules."""
        with patch('rally_api.config._load_settings_config') as mock_load:
            mock_load.return_value = {
                "story_points": {"max_points": 7},
                "validation_rules": {"require_acceptance_criteria": True}
            }

            rules = config.get_project_rules()

            assert "story_points" in rules
            assert rules["story_points"]["max_points"] == 7

    def test_get_rule_exists(self):
        """Test getting specific rule value."""
        with patch('rally_api.config.get_project_rules') as mock_get:
            mock_get.return_value = {
                "story_points": {"max_points": 7, "calculation_method": "skill_based"}
            }

            value = config.get_rule("story_points", "max_points")

            assert value == 7

    def test_get_rule_with_default(self):
        """Test getting non-existent rule returns default."""
        with patch('rally_api.config.get_project_rules') as mock_get:
            mock_get.return_value = {
                "story_points": {}
            }

            value = config.get_rule("story_points", "nonexistent", default=10)

            assert value == 10

    def test_get_rule_section_not_found(self):
        """Test getting rule from non-existent section returns default."""
        with patch('rally_api.config.get_project_rules') as mock_get:
            mock_get.return_value = {}

            value = config.get_rule("nonexistent_section", "key", default="default_value")

            assert value == "default_value"


class TestPodsConfig:
    """Tests for pods field in team configuration."""

    @patch('pathlib.Path.mkdir')
    @patch('rally_api.config.PluginPaths.find_claude_dir')
    @patch('builtins.open', new_callable=mock_open)
    def test_add_team_member_with_pods(self, mock_file, mock_find_dir, mock_mkdir):
        """Test adding a member with pods field saves correctly."""
        mock_find_dir.return_value = Path("/test/.claude")

        with patch('rally_api.config._load_team_config') as mock_load, \
             patch('rally_api.config.find_team_member') as mock_find:
            mock_load.return_value = {"members": []}
            mock_find.return_value = None

            result = config.add_team_member(
                name="Dave",
                rally_username="dave@test.com",
                role="Developer",
                pods=["POD 1", "POD 2"],
            )

            assert result["pods"] == ["POD 1", "POD 2"]

    @patch('pathlib.Path.mkdir')
    @patch('rally_api.config.PluginPaths.find_claude_dir')
    @patch('builtins.open', new_callable=mock_open)
    def test_add_team_member_without_pods_backward_compatible(self, mock_file, mock_find_dir, mock_mkdir):
        """Test adding a member without pods doesn't include pods key."""
        mock_find_dir.return_value = Path("/test/.claude")

        with patch('rally_api.config._load_team_config') as mock_load, \
             patch('rally_api.config.find_team_member') as mock_find:
            mock_load.return_value = {"members": []}
            mock_find.return_value = None

            result = config.add_team_member(
                name="Eve",
                rally_username="eve@test.com",
            )

            assert "pods" not in result

    def test_get_member_pods_exists(self):
        """Test getting pods for a member who has pods."""
        with patch('rally_api.config.find_team_member') as mock_find:
            mock_find.return_value = {
                "name": "Alice",
                "pods": ["POD 1", "POD 2"],
            }

            pods = config.get_member_pods("Alice")

            assert pods == ["POD 1", "POD 2"]

    def test_get_member_pods_none(self):
        """Test getting pods for a member without pods returns empty list."""
        with patch('rally_api.config.find_team_member') as mock_find:
            mock_find.return_value = {
                "name": "Bob",
            }

            pods = config.get_member_pods("Bob")

            assert pods == []

    def test_get_member_pods_not_found(self):
        """Test getting pods for non-existent member returns empty list."""
        with patch('rally_api.config.find_team_member') as mock_find:
            mock_find.return_value = None

            pods = config.get_member_pods("nobody")

            assert pods == []

    def test_get_team_members_by_pod(self):
        """Test filtering members by pod."""
        with patch('rally_api.config.get_team_members') as mock_get:
            mock_get.return_value = [
                {"name": "Alice", "pods": ["POD 1"]},
                {"name": "Bob", "pods": ["POD 2"]},
                {"name": "Carol", "pods": ["POD 1", "POD 2"]},
            ]

            result = config.get_team_members_by_pod("POD 1")

            assert len(result) == 2
            names = [m["name"] for m in result]
            assert "Alice" in names
            assert "Carol" in names

    def test_get_team_members_by_pod_case_insensitive(self):
        """Test pod filtering is case-insensitive."""
        with patch('rally_api.config.get_team_members') as mock_get:
            mock_get.return_value = [
                {"name": "Alice", "pods": ["POD 1"]},
            ]

            result = config.get_team_members_by_pod("pod 1")

            assert len(result) == 1
            assert result[0]["name"] == "Alice"

    def test_get_all_pods(self):
        """Test getting deduplicated sorted list of all pods."""
        with patch('rally_api.config.get_team_members') as mock_get:
            mock_get.return_value = [
                {"name": "Alice", "pods": ["POD 2", "POD 1"]},
                {"name": "Bob", "pods": ["POD 1"]},
                {"name": "Carol"},
            ]

            result = config.get_all_pods()

            assert result == ["POD 1", "POD 2"]

    def test_get_all_pods_empty(self):
        """Test getting pods when no members have pods."""
        with patch('rally_api.config.get_team_members') as mock_get:
            mock_get.return_value = [
                {"name": "Alice"},
                {"name": "Bob"},
            ]

            result = config.get_all_pods()

            assert result == []

    @patch('pathlib.Path.mkdir')
    @patch('rally_api.config.PluginPaths.find_claude_dir')
    @patch('builtins.open', new_callable=mock_open)
    def test_update_team_member_pods(self, mock_file, mock_find_dir, mock_mkdir):
        """Test updating a member's pods field."""
        mock_find_dir.return_value = Path("/test/.claude")

        with patch('rally_api.config._load_team_config') as mock_load:
            mock_load.return_value = {
                "members": [
                    {"name": "Alice", "rally_username": "alice@test.com", "pods": ["POD 1"]}
                ]
            }

            result = config.update_team_member("Alice", pods=["POD 1", "POD 2"])

            assert result is not None
            assert result["pods"] == ["POD 1", "POD 2"]

    def test_update_team_member_not_found(self):
        """Test updating non-existent member returns None."""
        with patch('rally_api.config._load_team_config') as mock_load:
            mock_load.return_value = {"members": []}

            result = config.update_team_member("nobody", role="Dev")

            assert result is None

    def test_update_team_member_invalid_field(self):
        """Test updating with invalid field raises ValueError."""
        with patch('rally_api.config._load_team_config') as mock_load:
            mock_load.return_value = {"members": []}

            with pytest.raises(ValueError, match="Invalid fields"):
                config.update_team_member("Alice", invalid_field="value")


class TestConfigPaths:
    """Tests for configuration path constants."""

    def test_config_path_is_home_directory(self):
        """Test API config path points to home directory."""
        assert config.CONFIG_PATH.parts[-2:] == ('.claude', 'aig.json') or \
               str(config.CONFIG_PATH).endswith('.claude/aig.json')
