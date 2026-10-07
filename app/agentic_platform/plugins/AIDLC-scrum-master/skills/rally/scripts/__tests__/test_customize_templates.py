"""
Unit tests for fesm_cli template customization functions.

Tests template and configuration customization including:
- Directory creation
- Config file copying
- Template copying
- Symlink creation
- Gitignore updates
"""

import pytest
from unittest.mock import MagicMock, patch, mock_open
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))
import fesm_cli


class TestUpdateGitignore:
    """Tests for updating .gitignore."""

    @patch.object(Path, "exists", return_value=False)
    @patch.object(Path, "write_text")
    def test_update_gitignore_create_new(self, mock_write, mock_exists):
        """Test updating gitignore creates new file if doesn't exist."""
        with patch.object(Path, "cwd", return_value=Path("/test")):
            result = fesm_cli.update_gitignore()

            assert result["status"] == "updated"
            assert len(result["added"]) == 2
            assert ".claude/.fe-sm/backups/" in result["added"]
            assert ".claude/.fe-sm/config/aig.json" in result["added"]

    @patch.object(Path, "exists", return_value=True)
    @patch.object(
        Path,
        "read_text",
        return_value=".claude/.fe-sm/backups/\n.claude/.fe-sm/config/aig.json\n",
    )
    def test_update_gitignore_already_present(self, mock_read, mock_exists):
        """Test updating gitignore when entries already exist."""
        with patch.object(Path, "cwd", return_value=Path("/test")):
            result = fesm_cli.update_gitignore()

            assert result["status"] == "already_present"
            assert len(result["added"]) == 0

    @patch.object(Path, "exists", return_value=True)
    @patch.object(Path, "read_text", return_value=".claude/.fe-sm/backups/\n")
    @patch.object(Path, "write_text")
    def test_update_gitignore_partial(self, mock_write, mock_read, mock_exists):
        """Test updating gitignore adds only missing entries."""
        with patch.object(Path, "cwd", return_value=Path("/test")):
            result = fesm_cli.update_gitignore()

            assert result["status"] == "updated"
            assert len(result["added"]) == 1
            assert ".claude/.fe-sm/config/aig.json" in result["added"]


class TestCustomizeTemplates:
    """Tests for main customize_templates function."""

    @patch("fesm_cli.update_gitignore")
    @patch("fesm_cli.copy_default_templates")
    @patch("fesm_cli.copy_config_files")
    @patch("fesm_cli.PluginPaths.find_claude_dir")
    @patch.object(Path, "mkdir")
    def test_customize_templates_all(
        self,
        mock_mkdir,
        mock_find_claude,
        mock_copy_config,
        mock_copy_templates,
        mock_gitignore,
    ):
        """Test full customization with all steps."""
        mock_find_claude.return_value = Path("/test/.claude")
        mock_copy_config.return_value = {
            "team.json": "copied",
            "settings.json": "copied",
            "rules.md": "copied",
        }
        mock_copy_templates.return_value = {
            "epic.md": "copied",
            "story.md": "copied",
        }
        mock_gitignore.return_value = {
            "status": "updated",
            "added": [".claude/.fe-sm/backups/"],
        }

        result = fesm_cli.customize_templates()

        assert "config_files" in result
        assert "templates" in result
        assert "gitignore" in result
        assert len(result["errors"]) == 0

    @patch("fesm_cli.update_gitignore")
    @patch("fesm_cli.copy_default_templates")
    @patch("fesm_cli.copy_config_files")
    @patch("fesm_cli.PluginPaths.find_claude_dir")
    @patch.object(Path, "mkdir")
    def test_customize_templates_with_reset(
        self,
        mock_mkdir,
        mock_find_claude,
        mock_copy_config,
        mock_copy_templates,
        mock_gitignore,
    ):
        """Test customization with reset flag."""
        mock_find_claude.return_value = Path("/test/.claude")
        mock_copy_config.return_value = {
            "team.json": "reset",
            "settings.json": "reset",
        }
        mock_copy_templates.return_value = {"epic.md": "reset", "story.md": "reset"}
        mock_gitignore.return_value = {"status": "updated", "added": []}

        result = fesm_cli.customize_templates(reset=True)

        # Verify reset flag was passed
        assert mock_copy_config.call_count == 1
        assert mock_copy_config.call_args[1]["reset"] is True

        assert mock_copy_templates.call_count == 1
        assert mock_copy_templates.call_args[1]["reset"] is True

    @patch("fesm_cli.update_gitignore")
    @patch("fesm_cli.copy_default_templates")
    @patch("fesm_cli.copy_config_files")
    @patch("fesm_cli.PluginPaths.find_claude_dir")
    @patch.object(Path, "mkdir")
    def test_customize_templates_no_claude_dir(
        self,
        mock_mkdir,
        mock_find_claude,
        mock_copy_config,
        mock_copy_templates,
        mock_gitignore,
    ):
        """Test customization creates .claude dir if not found."""
        mock_find_claude.return_value = None
        mock_copy_config.return_value = {}
        mock_copy_templates.return_value = {}
        mock_gitignore.return_value = {"status": "already_present", "added": []}

        with patch.object(Path, "cwd", return_value=Path("/test")):
            result = fesm_cli.customize_templates()

            assert "config_files" in result
            assert "templates" in result
            mock_mkdir.assert_called()
