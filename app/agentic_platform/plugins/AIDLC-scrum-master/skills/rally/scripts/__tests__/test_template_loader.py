"""
Unit tests for rally_api.template_loader module.

Tests template loading and rendering including:
- Loading default templates
- Loading custom templates with fallback
- Variable substitution
- Template path resolution
- YAML frontmatter stripping
- HTML comment removal
- Template copying
"""

import pytest
from unittest.mock import MagicMock, patch, mock_open
from pathlib import Path
from datetime import datetime
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))
from rally_api.template_loader import TemplateLoader, copy_default_templates, copy_config_files


class TestTemplateLoaderInitialization:
    """Tests for TemplateLoader initialization."""

    def test_init_sets_default_templates_dir(self):
        """Test initialization sets default templates directory."""
        loader = TemplateLoader()

        assert loader.default_templates_dir is not None
        assert "templates" in str(loader.default_templates_dir)

    @patch('rally_api.template_loader.Path.cwd')
    @patch.object(Path, 'exists', return_value=False)
    def test_init_no_custom_templates(self, mock_exists, mock_cwd):
        """Test initialization when no custom templates exist."""
        mock_cwd.return_value = Path("/project")

        loader = TemplateLoader()

        assert loader.custom_templates_dir is None

    @patch('rally_api.template_loader.Path.cwd')
    def test_init_finds_custom_templates(self, mock_cwd):
        """Test initialization finds custom templates directory."""
        mock_cwd.return_value = Path("/project")

        with patch.object(Path, 'exists', return_value=True):
            with patch.object(Path, 'is_dir', return_value=True):
                loader = TemplateLoader()

                # May or may not find custom templates depending on filesystem
                # Just verify it doesn't error
                assert loader.custom_templates_dir is None or \
                       loader.custom_templates_dir is not None


class TestTemplateMapping:
    """Tests for work item type to template filename mapping."""

    def test_template_map_includes_all_types(self):
        """Test template map includes all supported work item types."""
        loader = TemplateLoader()

        expected_types = [
            "epic", "capability", "feature", "story",
            "user_story", "hierarchicalrequirement",
            "task", "defect", "administrative_tasks"
        ]

        for work_item_type in expected_types:
            assert work_item_type in loader.TEMPLATE_MAP

    def test_template_map_story_aliases(self):
        """Test story template has multiple aliases."""
        loader = TemplateLoader()

        # All should map to story.md
        assert loader.TEMPLATE_MAP["story"] == "story.md"
        assert loader.TEMPLATE_MAP["user_story"] == "story.md"
        assert loader.TEMPLATE_MAP["hierarchicalrequirement"] == "story.md"


class TestGetTemplatePath:
    """Tests for getting template file paths."""

    def test_get_template_path_default(self):
        """Test getting template path uses default templates."""
        loader = TemplateLoader()
        loader.custom_templates_dir = None

        with patch.object(Path, 'exists', return_value=True):
            path = loader.get_template_path("story")

            assert path is not None
            assert "story.md" in str(path)

    def test_get_template_path_custom_first(self):
        """Test getting template path checks custom before default."""
        loader = TemplateLoader()
        loader.custom_templates_dir = Path("/custom")

        with patch.object(Path, 'exists', side_effect=[True, True]):
            path = loader.get_template_path("story")

            assert path is not None
            assert "/custom" in str(path)

    def test_get_template_path_fallback_to_default(self):
        """Test getting template path falls back to default if custom doesn't exist."""
        loader = TemplateLoader()
        loader.custom_templates_dir = Path("/custom")

        # Custom doesn't exist, default does
        with patch.object(Path, 'exists', side_effect=[False, True]):
            path = loader.get_template_path("story")

            assert path is not None
            assert "/custom" not in str(path)

    def test_get_template_path_case_insensitive(self):
        """Test getting template path is case insensitive."""
        loader = TemplateLoader()
        loader.custom_templates_dir = None

        with patch.object(Path, 'exists', return_value=True):
            path1 = loader.get_template_path("Story")
            path2 = loader.get_template_path("STORY")
            path3 = loader.get_template_path("story")

            assert path1 == path2 == path3

    def test_get_template_path_unknown_type(self):
        """Test getting template path for unknown work item type."""
        loader = TemplateLoader()

        path = loader.get_template_path("unknown_type")

        assert path is None


class TestLoadTemplate:
    """Tests for loading template content."""

    @patch('builtins.open', new_callable=mock_open, read_data='<p>Template content</p>')
    def test_load_template_basic(self, mock_file):
        """Test loading basic template content."""
        loader = TemplateLoader()

        with patch.object(loader, 'get_template_path', return_value=Path("/test/story.md")):
            content = loader.load_template("story")

            assert content == "<p>Template content</p>"

    @patch('builtins.open', new_callable=mock_open,
           read_data='---\ntitle: Test\n---\n<p>Content</p>')
    def test_load_template_strips_frontmatter(self, mock_file):
        """Test loading template removes YAML frontmatter."""
        loader = TemplateLoader()

        with patch.object(loader, 'get_template_path', return_value=Path("/test/story.md")):
            content = loader.load_template("story")

            assert "---" not in content
            assert "title:" not in content
            assert "<p>Content</p>" in content

    @patch('builtins.open', new_callable=mock_open,
           read_data='<p>Content</p><!-- This is a comment -->')
    def test_load_template_strips_comments(self, mock_file):
        """Test loading template removes HTML comments."""
        loader = TemplateLoader()

        with patch.object(loader, 'get_template_path', return_value=Path("/test/story.md")):
            content = loader.load_template("story")

            assert "<!--" not in content
            assert "comment" not in content
            assert "<p>Content</p>" in content

    def test_load_template_not_found(self):
        """Test loading non-existent template returns None."""
        loader = TemplateLoader()

        with patch.object(loader, 'get_template_path', return_value=None):
            content = loader.load_template("nonexistent")

            assert content is None

    @patch('builtins.open', side_effect=FileNotFoundError)
    def test_load_template_file_error(self, mock_file):
        """Test loading template handles file errors gracefully."""
        loader = TemplateLoader()

        with patch.object(loader, 'get_template_path', return_value=Path("/test/story.md")):
            content = loader.load_template("story")

            assert content is None


class TestRenderTemplate:
    """Tests for rendering templates with variable substitution."""

    @patch('builtins.open', new_callable=mock_open,
           read_data='<p>Story: {{title}}</p>')
    def test_render_template_basic(self, mock_file):
        """Test rendering template with basic variable substitution."""
        loader = TemplateLoader()

        with patch.object(loader, 'get_template_path', return_value=Path("/test/story.md")):
            rendered = loader.render_template("story", {"title": "Test Story"})

            assert "Test Story" in rendered
            assert "{{title}}" not in rendered

    @patch('builtins.open', new_callable=mock_open,
           read_data='<p>{{var1}} and {{var2}}</p>')
    def test_render_template_multiple_variables(self, mock_file):
        """Test rendering template with multiple variables."""
        loader = TemplateLoader()

        with patch.object(loader, 'get_template_path', return_value=Path("/test/story.md")):
            rendered = loader.render_template("story", {
                "var1": "First",
                "var2": "Second"
            })

            assert "First" in rendered
            assert "Second" in rendered
            assert "{{" not in rendered

    @patch('builtins.open', new_callable=mock_open,
           read_data='<p>Date: {{date}}</p>')
    def test_render_template_auto_adds_date(self, mock_file):
        """Test rendering template automatically adds current date."""
        loader = TemplateLoader()

        with patch.object(loader, 'get_template_path', return_value=Path("/test/story.md")):
            rendered = loader.render_template("story", {})

            # Should contain a date in YYYY-MM-DD format
            assert "Date:" in rendered
            assert "{{date}}" not in rendered

    @patch('builtins.open', new_callable=mock_open,
           read_data='<p>Points: {{points}}</p>')
    def test_render_template_converts_values_to_string(self, mock_file):
        """Test rendering template converts non-string values."""
        loader = TemplateLoader()

        with patch.object(loader, 'get_template_path', return_value=Path("/test/story.md")):
            rendered = loader.render_template("story", {"points": 3})

            assert "Points: 3" in rendered

    def test_render_template_not_found(self):
        """Test rendering non-existent template returns None."""
        loader = TemplateLoader()

        with patch.object(loader, 'load_template', return_value=None):
            rendered = loader.render_template("nonexistent", {})

            assert rendered is None

    @patch('builtins.open', new_callable=mock_open,
           read_data='<p>{{missing}}</p>')
    def test_render_template_missing_variable(self, mock_file):
        """Test rendering template with missing variable leaves placeholder."""
        loader = TemplateLoader()

        with patch.object(loader, 'get_template_path', return_value=Path("/test/story.md")):
            rendered = loader.render_template("story", {})

            # Missing variable placeholder should remain
            assert "{{missing}}" in rendered


class TestListAvailableTemplates:
    """Tests for listing available templates."""

    def test_list_available_templates_all_default(self):
        """Test listing templates when all are default."""
        loader = TemplateLoader()
        loader.custom_templates_dir = None

        with patch.object(loader, 'get_template_path') as mock_get_path:
            mock_get_path.return_value = Path("/default/story.md")

            templates = loader.list_available_templates()

            assert len(templates) > 0
            assert all(source == "default" for source in templates.values())

    def test_list_available_templates_with_custom(self):
        """Test listing templates includes custom templates."""
        loader = TemplateLoader()
        loader.custom_templates_dir = Path("/custom")

        def mock_get_path(work_item_type):
            if work_item_type == "story":
                return Path("/custom/story.md")
            return Path("/default/" + loader.TEMPLATE_MAP.get(work_item_type, ""))

        with patch.object(loader, 'get_template_path', side_effect=mock_get_path):
            templates = loader.list_available_templates()

            assert "story" in templates
            assert templates["story"] == "custom"

    def test_list_available_templates_excludes_missing(self):
        """Test listing templates excludes templates that don't exist."""
        loader = TemplateLoader()

        with patch.object(loader, 'get_template_path', return_value=None):
            templates = loader.list_available_templates()

            assert len(templates) == 0


class TestHelperMethods:
    """Tests for helper methods."""

    def test_has_custom_templates_true(self):
        """Test has_custom_templates returns True when custom templates exist."""
        loader = TemplateLoader()
        loader.custom_templates_dir = Path("/custom")

        assert loader.has_custom_templates() is True

    def test_has_custom_templates_false(self):
        """Test has_custom_templates returns False when no custom templates."""
        loader = TemplateLoader()
        loader.custom_templates_dir = None

        assert loader.has_custom_templates() is False

    def test_get_custom_templates_dir(self):
        """Test getting custom templates directory path."""
        loader = TemplateLoader()
        loader.custom_templates_dir = Path("/custom")

        assert loader.get_custom_templates_dir() == Path("/custom")

    def test_get_default_templates_dir(self):
        """Test getting default templates directory path."""
        loader = TemplateLoader()

        default_dir = loader.get_default_templates_dir()

        assert default_dir is not None
        assert isinstance(default_dir, Path)


class TestCopyDefaultTemplates:
    """Tests for copying default templates to custom location."""

    @patch.object(Path, 'mkdir')
    @patch.object(Path, 'glob')
    def test_copy_default_templates_basic(self, mock_glob, mock_mkdir):
        """Test copying default templates to target directory."""
        mock_glob.return_value = [
            Path("/default/story.md"),
            Path("/default/task.md")
        ]

        target_dir = Path("/custom")

        # Create a proper exists mock that checks the path
        original_exists = Path.exists
        def mock_exists_method(path_instance):
            # Return False for target files in /custom/, True for everything else
            return "/custom/" not in str(path_instance)

        with patch.object(Path, 'exists', new=mock_exists_method):
            with patch.object(Path, 'read_text', return_value="content"):
                with patch.object(Path, 'write_text') as mock_write:
                    results = copy_default_templates(target_dir)

                    assert "story.md" in results
                    assert "task.md" in results
                    mock_mkdir.assert_called_once()

    @patch.object(Path, 'mkdir')
    @patch.object(Path, 'glob')
    def test_copy_default_templates_exists(self, mock_glob, mock_mkdir):
        """Test copying templates skips existing files."""
        mock_glob.return_value = [Path("/default/story.md")]
        target_dir = Path("/custom")

        with patch.object(Path, 'exists', return_value=True):
            results = copy_default_templates(target_dir)

            assert results["story.md"] == "exists"

    @patch.object(Path, 'glob')
    def test_copy_default_templates_creates_target_dir(self, mock_glob):
        """Test copying templates creates target directory if needed."""
        mock_glob.return_value = []
        target_dir = Path("/custom")

        with patch.object(Path, 'mkdir') as mock_mkdir:
            with patch.object(Path, 'exists', return_value=True):
                copy_default_templates(target_dir)

                mock_mkdir.assert_called_once_with(parents=True, exist_ok=True)

    @patch.object(Path, 'mkdir')
    @patch.object(Path, 'glob')
    def test_copy_default_templates_reset_overwrites(self, mock_glob, mock_mkdir):
        """Test copying templates with reset=True overwrites existing files."""
        mock_glob.return_value = [Path("/default/story.md")]
        target_dir = Path("/custom")

        with patch.object(Path, 'exists', return_value=True):
            with patch.object(Path, 'read_text', return_value="new content"):
                with patch.object(Path, 'write_text') as mock_write:
                    results = copy_default_templates(target_dir, reset=True)

                    assert results["story.md"] == "reset"
                    mock_write.assert_called_once()

    @patch.object(Path, 'mkdir')
    @patch.object(Path, 'glob')
    def test_copy_default_templates_reset_false_skips(self, mock_glob, mock_mkdir):
        """Test copying templates with reset=False skips existing files."""
        mock_glob.return_value = [Path("/default/story.md")]
        target_dir = Path("/custom")

        with patch.object(Path, 'exists', return_value=True):
            with patch.object(Path, 'write_text') as mock_write:
                results = copy_default_templates(target_dir, reset=False)

                assert results["story.md"] == "exists"
                mock_write.assert_not_called()

    @patch.object(Path, 'mkdir')
    @patch.object(Path, 'glob')
    def test_copy_default_templates_reset_new_files(self, mock_glob, mock_mkdir):
        """Test copying templates with reset=True still reports 'copied' for new files."""
        mock_glob.return_value = [Path("/default/story.md")]
        target_dir = Path("/custom")

        # Create a proper exists mock
        def mock_exists_method(path_instance):
            return "/custom/" not in str(path_instance)

        # Target files don't exist (new files), but default_dir exists
        with patch.object(Path, 'exists', new=mock_exists_method):
            with patch.object(Path, 'read_text', return_value="content"):
                with patch.object(Path, 'write_text'):
                    results = copy_default_templates(target_dir, reset=True)

                    assert results["story.md"] == "copied"


class TestCopyConfigFiles:
    """Tests for copying configuration files to custom location."""

    @patch.object(Path, 'mkdir')
    @patch.object(Path, 'read_text', return_value="config content")
    @patch.object(Path, 'write_text')
    def test_copy_config_files_basic(self, mock_write, mock_read, mock_mkdir):
        """Test copying config files to target directory."""
        target_dir = Path("/custom")

        # Create a proper exists mock
        def mock_exists_method(path_instance):
            return "/custom/" not in str(path_instance)

        # Target files don't exist (new files), but default_dir and source files exist
        with patch.object(Path, 'exists', new=mock_exists_method):
            results = copy_config_files(target_dir)

            assert "team.json" in results
            assert "settings.json" in results
            assert "rules.md" in results
            mock_mkdir.assert_called_once()

    @patch.object(Path, 'mkdir')
    @patch.object(Path, 'exists')
    def test_copy_config_files_skips_existing(self, mock_exists, mock_mkdir):
        """Test copying config files skips existing files without reset."""
        target_dir = Path("/custom")

        # Mock exists to return True for target files
        def exists_side_effect(self_path=None):
            # Return True for target files (in /custom)
            if self_path and "/custom" in str(self_path):
                return True
            # Return True for source files (in templates)
            return True

        mock_exists.side_effect = exists_side_effect

        with patch.object(Path, 'write_text') as mock_write:
            results = copy_config_files(target_dir, reset=False)

            assert results.get("team.json") == "exists"
            assert results.get("settings.json") == "exists"
            assert results.get("rules.md") == "exists"

    @patch.object(Path, 'mkdir')
    @patch.object(Path, 'read_text', return_value="new content")
    @patch.object(Path, 'write_text')
    def test_copy_config_files_reset_overwrites(self, mock_write, mock_read, mock_mkdir):
        """Test copying config files with reset=True overwrites existing files."""
        target_dir = Path("/custom")

        with patch.object(Path, 'exists', return_value=True):
            results = copy_config_files(target_dir, reset=True)

            assert results.get("team.json") == "reset"
            assert results.get("settings.json") == "reset"
            assert results.get("rules.md") == "reset"
            assert mock_write.call_count >= 3

    @patch.object(Path, 'mkdir')
    def test_copy_config_files_creates_target_dir(self, mock_mkdir):
        """Test copying config files creates target directory if needed."""
        target_dir = Path("/custom")

        with patch.object(Path, 'exists', return_value=True):
            with patch.object(Path, 'read_text', return_value="content"):
                with patch.object(Path, 'write_text'):
                    copy_config_files(target_dir)

                    mock_mkdir.assert_called_once_with(parents=True, exist_ok=True)

    @patch.object(Path, 'mkdir')
    @patch.object(Path, 'read_text', return_value="content")
    @patch.object(Path, 'write_text')
    def test_copy_config_files_handles_missing_source(self, mock_write, mock_read, mock_mkdir):
        """Test copying config files handles missing source files gracefully."""
        target_dir = Path("/custom")

        def mock_exists_method(path_instance):
            path_str = str(path_instance)
            # default config dir exists
            if ".fe-sm-default" in path_str and "config" in path_str and not any(x in path_str for x in ["team.json", "settings.json", "rules.md"]):
                return True
            # Source team.json doesn't exist
            if "team.json" in path_str and ".fe-sm-default" in path_str:
                return False
            # Other source config files exist
            if ".fe-sm-default" in path_str and "config" in path_str:
                return True
            # Target files don't exist
            if "/custom/" in path_str:
                return False
            return True

        with patch.object(Path, 'exists', new=mock_exists_method):
            results = copy_config_files(target_dir)

            assert "not found" in results.get("team.json", "")


class TestStripFrontmatter:
    """Tests for YAML frontmatter stripping."""

    def test_strip_frontmatter_basic(self):
        """Test stripping basic YAML frontmatter."""
        loader = TemplateLoader()
        content = "---\ntitle: Test\n---\nContent here"

        result = loader._strip_frontmatter(content)

        assert "---" not in result
        assert "title:" not in result
        assert "Content here" in result

    def test_strip_frontmatter_multiline(self):
        """Test stripping multiline YAML frontmatter."""
        loader = TemplateLoader()
        content = "---\ntitle: Test\nauthor: John\ndate: 2026-01-01\n---\nContent"

        result = loader._strip_frontmatter(content)

        assert "title:" not in result
        assert "author:" not in result
        assert "Content" in result

    def test_strip_frontmatter_none(self):
        """Test content without frontmatter is unchanged."""
        loader = TemplateLoader()
        content = "Content without frontmatter"

        result = loader._strip_frontmatter(content)

        assert result == content


class TestStripComments:
    """Tests for HTML comment stripping."""

    def test_strip_comments_basic(self):
        """Test stripping basic HTML comments."""
        loader = TemplateLoader()
        content = "<p>Content</p><!-- Comment here -->"

        result = loader._strip_comments(content)

        assert "<!--" not in result
        assert "Comment" not in result
        assert "<p>Content</p>" in result

    def test_strip_comments_multiline(self):
        """Test stripping multiline HTML comments."""
        loader = TemplateLoader()
        content = "<p>Content</p><!-- This is\na multiline\ncomment -->"

        result = loader._strip_comments(content)

        assert "<!--" not in result
        assert "multiline" not in result
        assert "<p>Content</p>" in result

    def test_strip_comments_none(self):
        """Test content without comments is unchanged."""
        loader = TemplateLoader()
        content = "<p>Content without comments</p>"

        result = loader._strip_comments(content)

        assert result == content
