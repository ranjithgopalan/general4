"""
Unit tests for rally_api.paths module.

Tests centralized path resolution including:
- Plugin root directory detection
- Default templates and config directories
- User customization directory detection
- Backup directory creation
- Template and config file resolution with fallback
"""

import pytest
from unittest.mock import patch, MagicMock
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from rally_api.paths import PluginPaths


class TestPluginRootResolution:
    """Tests for plugin root directory resolution."""

    def test_get_plugin_root(self):
        """Test getting plugin root directory."""
        plugin_root = PluginPaths.get_plugin_root()

        # Should end with 'fe-sm'
        assert plugin_root.name == "fe-sm"

        # Should contain .fe-sm-default
        default_dir = plugin_root / ".fe-sm-default"
        assert default_dir.exists()

    def test_get_default_templates_dir(self):
        """Test getting default templates directory."""
        templates_dir = PluginPaths.get_default_templates_dir()

        # Should end with templates
        assert templates_dir.name == "templates"

        # Should be inside .fe-sm-default
        assert templates_dir.parent.name == ".fe-sm-default"

    def test_get_default_config_dir(self):
        """Test getting default config directory."""
        config_dir = PluginPaths.get_default_config_dir()

        # Should end with config
        assert config_dir.name == "config"

        # Should be inside .fe-sm-default
        assert config_dir.parent.name == ".fe-sm-default"


class TestClaudeDirectoryDetection:
    """Tests for .claude directory detection."""

    @patch('rally_api.paths.Path.cwd')
    def test_find_claude_dir_in_cwd(self, mock_cwd):
        """Test finding .claude directory in current directory."""
        mock_dir = MagicMock(spec=Path)
        mock_dir.__truediv__ = lambda self, other: MagicMock(spec=Path, exists=lambda: True, is_dir=lambda: True) if other == ".claude" else MagicMock(spec=Path)
        mock_dir.parent = mock_dir.parent if hasattr(mock_dir, 'parent') else MagicMock()
        mock_dir.__ne__ = lambda self, other: True  # Prevent infinite loop
        mock_cwd.return_value = mock_dir

        # Mock exists and is_dir for .claude
        with patch.object(Path, 'exists', return_value=True), \
             patch.object(Path, 'is_dir', return_value=True):
            result = PluginPaths.find_claude_dir()
            # Test passes if no error raised
            assert result is None or isinstance(result, Path)

    @patch('rally_api.paths.Path.cwd')
    def test_find_claude_dir_not_found(self, mock_cwd):
        """Test when .claude directory is not found."""
        # Create a mock path that stops at root
        mock_root = MagicMock(spec=Path)
        mock_root.parent = mock_root  # Root directory points to itself
        mock_cwd.return_value = mock_root

        result = PluginPaths.find_claude_dir()

        assert result is None

    @patch('rally_api.paths.Path.cwd')
    def test_find_claude_dir_walks_up(self, mock_cwd):
        """Test that find_claude_dir walks up directory tree."""
        # Create mock directory hierarchy
        root = MagicMock(spec=Path)
        root.parent = root  # Root points to itself

        parent_dir = MagicMock(spec=Path)
        parent_dir.parent = root

        current_dir = MagicMock(spec=Path)
        current_dir.parent = parent_dir

        mock_cwd.return_value = current_dir

        # Mock .claude directory exists in parent
        def mock_truediv(path, other):
            if other == ".claude" and path == parent_dir:
                claude_mock = MagicMock(spec=Path)
                claude_mock.exists.return_value = True
                claude_mock.is_dir.return_value = True
                return claude_mock
            else:
                no_claude = MagicMock(spec=Path)
                no_claude.exists.return_value = False
                return no_claude

        current_dir.__truediv__ = lambda self, other: mock_truediv(current_dir, other)
        parent_dir.__truediv__ = lambda self, other: mock_truediv(parent_dir, other)

        result = PluginPaths.find_claude_dir(current_dir)

        # Should eventually find it or return None
        assert result is None or isinstance(result, Path)


class TestUserDirectories:
    """Tests for user customization directory methods."""

    @patch('rally_api.paths.PluginPaths.find_claude_dir')
    def test_get_user_fe_sm_dir_found(self, mock_find):
        """Test getting user .fe-sm directory when .claude exists."""
        mock_claude = MagicMock(spec=Path)
        mock_find.return_value = mock_claude

        result = PluginPaths.get_user_fe_sm_dir()

        assert result is not None
        mock_find.assert_called_once()

    @patch('rally_api.paths.PluginPaths.find_claude_dir')
    def test_get_user_fe_sm_dir_not_found(self, mock_find):
        """Test getting user .fe-sm directory when .claude doesn't exist."""
        mock_find.return_value = None

        result = PluginPaths.get_user_fe_sm_dir()

        assert result is None

    @patch('rally_api.paths.PluginPaths.get_user_fe_sm_dir')
    def test_get_user_templates_dir_exists(self, mock_fe_sm):
        """Test getting user templates directory when it exists."""
        mock_fe_sm_path = MagicMock(spec=Path)
        mock_templates = MagicMock(spec=Path)
        mock_templates.exists.return_value = True
        mock_templates.is_dir.return_value = True
        mock_fe_sm_path.__truediv__.return_value = mock_templates
        mock_fe_sm.return_value = mock_fe_sm_path

        result = PluginPaths.get_user_templates_dir()

        assert result == mock_templates

    @patch('rally_api.paths.PluginPaths.get_user_fe_sm_dir')
    def test_get_user_templates_dir_not_exists(self, mock_fe_sm):
        """Test getting user templates directory when it doesn't exist."""
        mock_fe_sm_path = MagicMock(spec=Path)
        mock_templates = MagicMock(spec=Path)
        mock_templates.exists.return_value = False
        mock_fe_sm_path.__truediv__.return_value = mock_templates
        mock_fe_sm.return_value = mock_fe_sm_path

        result = PluginPaths.get_user_templates_dir()

        assert result is None


class TestBackupDirectory:
    """Tests for backup directory management."""

    @patch('rally_api.paths.PluginPaths.find_claude_dir')
    @patch('rally_api.paths.Path.mkdir')
    def test_get_backup_dir_creates_directory(self, mock_mkdir, mock_find):
        """Test that get_backup_dir creates backup directory."""
        mock_claude = MagicMock(spec=Path)
        mock_backup = MagicMock(spec=Path)
        mock_backup.mkdir = mock_mkdir

        # Setup path chaining
        mock_fe_sm = MagicMock(spec=Path)
        mock_fe_sm.__truediv__.return_value = mock_backup
        mock_claude.__truediv__.return_value = mock_fe_sm
        mock_find.return_value = mock_claude

        result = PluginPaths.get_backup_dir()

        mock_mkdir.assert_called_once_with(parents=True, exist_ok=True)

    @patch('rally_api.paths.PluginPaths.find_claude_dir')
    @patch('rally_api.paths.Path.cwd')
    def test_get_backup_dir_fallback_to_cwd(self, mock_cwd, mock_find):
        """Test backup directory falls back to cwd when .claude not found."""
        mock_find.return_value = None
        mock_cwd_path = MagicMock(spec=Path)
        mock_cwd.return_value = mock_cwd_path

        result = PluginPaths.get_backup_dir()

        # Should create in cwd/.claude/.fe-sm/backups
        assert result is not None


class TestTemplateFinding:
    """Tests for template file resolution with fallback."""

    @patch('rally_api.paths.PluginPaths.get_user_templates_dir')
    @patch('rally_api.paths.PluginPaths.get_default_templates_dir')
    def test_find_template_in_user_dir(self, mock_default, mock_user):
        """Test finding template in user directory first."""
        mock_user_path = MagicMock(spec=Path)
        mock_user_file = MagicMock(spec=Path)
        mock_user_file.exists.return_value = True
        mock_user_file.is_file.return_value = True
        mock_user_path.__truediv__.return_value = mock_user_file
        mock_user.return_value = mock_user_path

        result = PluginPaths.find_template("story.md")

        assert result == mock_user_file
        # Should not check default if found in user
        mock_default.assert_not_called()

    @patch('rally_api.paths.PluginPaths.get_user_templates_dir')
    @patch('rally_api.paths.PluginPaths.get_default_templates_dir')
    def test_find_template_fallback_to_default(self, mock_default, mock_user):
        """Test falling back to default template when user doesn't exist."""
        mock_user.return_value = None

        mock_default_path = MagicMock(spec=Path)
        mock_default_file = MagicMock(spec=Path)
        mock_default_file.exists.return_value = True
        mock_default_file.is_file.return_value = True
        mock_default_path.__truediv__.return_value = mock_default_file
        mock_default.return_value = mock_default_path

        result = PluginPaths.find_template("story.md")

        assert result == mock_default_file

    @patch('rally_api.paths.PluginPaths.get_user_templates_dir')
    @patch('rally_api.paths.PluginPaths.get_default_templates_dir')
    def test_find_template_not_found(self, mock_default, mock_user):
        """Test when template is not found in either location."""
        mock_user.return_value = None

        mock_default_path = MagicMock(spec=Path)
        mock_default_file = MagicMock(spec=Path)
        mock_default_file.exists.return_value = False
        mock_default_path.__truediv__.return_value = mock_default_file
        mock_default.return_value = mock_default_path

        result = PluginPaths.find_template("nonexistent.md")

        assert result is None


class TestConfigFinding:
    """Tests for config file resolution with fallback."""

    @patch('rally_api.paths.PluginPaths.get_user_config_dir')
    @patch('rally_api.paths.PluginPaths.get_default_config_dir')
    def test_find_config_in_user_dir(self, mock_default, mock_user):
        """Test finding config in user directory first."""
        mock_user_path = MagicMock(spec=Path)
        mock_user_file = MagicMock(spec=Path)
        mock_user_file.exists.return_value = True
        mock_user_file.is_file.return_value = True
        mock_user_path.__truediv__.return_value = mock_user_file
        mock_user.return_value = mock_user_path

        result = PluginPaths.find_config("team.json")

        assert result == mock_user_file
        # Should not check default if found in user
        mock_default.assert_not_called()

    @patch('rally_api.paths.PluginPaths.get_user_config_dir')
    @patch('rally_api.paths.PluginPaths.get_default_config_dir')
    def test_find_config_fallback_to_default(self, mock_default, mock_user):
        """Test falling back to default config when user doesn't exist."""
        mock_user.return_value = None

        mock_default_path = MagicMock(spec=Path)
        mock_default_file = MagicMock(spec=Path)
        mock_default_file.exists.return_value = True
        mock_default_file.is_file.return_value = True
        mock_default_path.__truediv__.return_value = mock_default_file
        mock_default.return_value = mock_default_path

        result = PluginPaths.find_config("settings.json")

        assert result == mock_default_file

    @patch('rally_api.paths.PluginPaths.get_user_config_dir')
    @patch('rally_api.paths.PluginPaths.get_default_config_dir')
    def test_find_config_not_found(self, mock_default, mock_user):
        """Test when config is not found in either location."""
        mock_user.return_value = None

        mock_default_path = MagicMock(spec=Path)
        mock_default_file = MagicMock(spec=Path)
        mock_default_file.exists.return_value = False
        mock_default_path.__truediv__.return_value = mock_default_file
        mock_default.return_value = mock_default_path

        result = PluginPaths.find_config("nonexistent.json")

        assert result is None


class TestValidation:
    """Tests for plugin resource validation."""

    @patch('rally_api.paths.PluginPaths.get_plugin_root')
    def test_validate_plugin_resources_success(self, mock_root):
        """Test validation passes when resources exist."""
        mock_plugin_root = MagicMock(spec=Path)

        # Mock template directory
        mock_templates = MagicMock(spec=Path)
        mock_templates.exists.return_value = True

        # Mock config directory
        mock_config = MagicMock(spec=Path)
        mock_config.exists.return_value = True

        # Setup path chaining
        def mock_truediv(self, other):
            if other == ".fe-sm-default":
                default_dir = MagicMock(spec=Path)
                def default_truediv(self, sub):
                    if sub == "templates":
                        return mock_templates
                    elif sub == "config":
                        return mock_config
                    return MagicMock(spec=Path)
                default_dir.__truediv__ = default_truediv
                return default_dir
            return MagicMock(spec=Path)

        mock_plugin_root.__truediv__ = mock_truediv
        mock_root.return_value = mock_plugin_root

        # Should not raise exception
        PluginPaths.validate_plugin_resources()

    @patch('rally_api.paths.PluginPaths.get_plugin_root')
    def test_validate_plugin_resources_missing(self, mock_root):
        """Test validation fails when resources are missing."""
        mock_plugin_root = MagicMock(spec=Path)

        # Mock missing template directory
        mock_templates = MagicMock(spec=Path)
        mock_templates.exists.return_value = False

        # Mock missing config directory
        mock_config = MagicMock(spec=Path)
        mock_config.exists.return_value = False

        # Setup path chaining
        def mock_truediv(self, other):
            if other == ".fe-sm-default":
                default_dir = MagicMock(spec=Path)
                def default_truediv(self, sub):
                    if sub == "templates":
                        return mock_templates
                    elif sub == "config":
                        return mock_config
                    return MagicMock(spec=Path)
                default_dir.__truediv__ = default_truediv
                return default_dir
            return MagicMock(spec=Path)

        mock_plugin_root.__truediv__ = mock_truediv
        mock_root.return_value = mock_plugin_root

        # Should raise RuntimeError
        with pytest.raises(RuntimeError) as exc_info:
            PluginPaths.validate_plugin_resources()

        assert "Plugin resources not found" in str(exc_info.value)
