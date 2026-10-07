#!/usr/bin/env python3
"""
Rally Work Item Template Loader

Loads and processes Rally work item templates with variable substitution.
Supports custom templates in .claude/.fe-sm/templates/ with fallback to defaults.
"""

import os
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, Any

from .paths import PluginPaths


class TemplateLoader:
    """Load and process Rally work item templates."""

    # Map work item types to template filenames
    TEMPLATE_MAP = {
        "epic": "epic.md",
        "capability": "capability.md",
        "feature": "feature.md",
        "story": "story.md",
        "user_story": "story.md",
        "hierarchicalrequirement": "story.md",
        "task": "task.md",
        "defect": "defect.md",
        "administrative_tasks": "administrative-tasks.md",
    }

    def __init__(self):
        """Initialize template loader with default and custom template paths."""
        # Default templates in plugin
        self.plugin_dir = PluginPaths.get_plugin_root()
        self.default_templates_dir = PluginPaths.get_default_templates_dir()

        # Custom templates in project
        self.custom_templates_dir = PluginPaths.get_user_templates_dir()

    def get_template_path(self, work_item_type: str) -> Optional[Path]:
        """
        Get template path for a work item type.
        Checks custom templates first, then falls back to defaults.

        Args:
            work_item_type: Type of work item (epic, story, task, etc.)

        Returns:
            Path to template file, or None if not found
        """
        # Normalize work item type
        work_item_type = work_item_type.lower()

        # Get template filename
        template_filename = self.TEMPLATE_MAP.get(work_item_type)
        if not template_filename:
            return None

        # Check custom templates first
        if self.custom_templates_dir:
            custom_path = self.custom_templates_dir / template_filename
            if custom_path.exists():
                return custom_path

        # Fall back to default templates
        default_path = self.default_templates_dir / template_filename
        if default_path.exists():
            return default_path

        return None

    def load_template(self, work_item_type: str) -> Optional[str]:
        """
        Load template content for a work item type.

        Args:
            work_item_type: Type of work item (epic, story, task, etc.)

        Returns:
            Template content as string, or None if not found
        """
        template_path = self.get_template_path(work_item_type)
        if not template_path:
            return None

        try:
            with open(template_path, "r", encoding="utf-8") as f:
                content = f.read()

            # Remove YAML frontmatter if present
            content = self._strip_frontmatter(content)

            # Remove HTML comments
            content = self._strip_comments(content)

            return content.strip()

        except Exception as e:
            print(f"Error loading template {template_path}: {e}")
            return None

    def _strip_frontmatter(self, content: str) -> str:
        """Remove YAML frontmatter from template content."""
        # Match YAML frontmatter: ---\n...\n---
        pattern = r"^---\s*\n.*?\n---\s*\n"
        return re.sub(pattern, "", content, flags=re.DOTALL)

    def _strip_comments(self, content: str) -> str:
        """Remove HTML comments from template content."""
        # Match HTML comments: <!-- ... -->
        pattern = r"<!--.*?-->"
        return re.sub(pattern, "", content, flags=re.DOTALL)

    def render_template(
        self, work_item_type: str, variables: Dict[str, Any]
    ) -> Optional[str]:
        """
        Load and render a template with variable substitution.

        Args:
            work_item_type: Type of work item (epic, story, task, etc.)
            variables: Dictionary of variables to substitute

        Returns:
            Rendered template content, or None if template not found
        """
        template = self.load_template(work_item_type)
        if not template:
            return None

        # Add default variables
        if "date" not in variables:
            variables["date"] = datetime.now().strftime("%Y-%m-%d")

        # Substitute variables
        rendered = template
        for key, value in variables.items():
            placeholder = f"{{{{{key}}}}}"
            rendered = rendered.replace(placeholder, str(value))

        return rendered.strip()

    def list_available_templates(self) -> Dict[str, str]:
        """
        List all available templates with their sources.

        Returns:
            Dictionary mapping work item type to template source (custom/default)
        """
        templates = {}

        for work_item_type in self.TEMPLATE_MAP.keys():
            template_path = self.get_template_path(work_item_type)
            if template_path:
                if (
                    self.custom_templates_dir
                    and template_path.parent == self.custom_templates_dir
                ):
                    templates[work_item_type] = "custom"
                else:
                    templates[work_item_type] = "default"

        return templates

    def has_custom_templates(self) -> bool:
        """Check if custom templates directory exists."""
        return self.custom_templates_dir is not None

    def get_custom_templates_dir(self) -> Optional[Path]:
        """Get path to custom templates directory."""
        return self.custom_templates_dir

    def get_default_templates_dir(self) -> Path:
        """Get path to default templates directory."""
        return self.default_templates_dir


def copy_default_templates(target_dir: Path, reset: bool = False) -> Dict[str, str]:
    """
    Copy default templates to a target directory.

    Args:
        target_dir: Target directory to copy templates to
        reset: If True, overwrite existing files. If False, skip existing files.

    Returns:
        Dictionary mapping template filenames to status (copied/exists/reset/error)
    """
    loader = TemplateLoader()
    default_dir = loader.get_default_templates_dir()

    if not default_dir.exists():
        return {"error": "Default templates directory not found"}

    # Create target directory if it doesn't exist
    target_dir.mkdir(parents=True, exist_ok=True)

    results = {}

    # Copy all template files
    for template_file in default_dir.glob("*.md"):
        target_file = target_dir / template_file.name
        file_existed = target_file.exists()

        if file_existed and not reset:
            results[template_file.name] = "exists"
        else:
            try:
                target_file.write_text(template_file.read_text(encoding="utf-8"))
                if file_existed and reset:
                    results[template_file.name] = "reset"
                else:
                    results[template_file.name] = "copied"
            except Exception as e:
                results[template_file.name] = f"error: {str(e)}"

    return results


def copy_config_files(target_dir: Path, reset: bool = False) -> Dict[str, str]:
    """
    Copy configuration files (team.json, settings.json, rules.md) to target directory.

    Args:
        target_dir: Target directory to copy config files to
        reset: If True, overwrite existing files. If False, skip existing files.

    Returns:
        Dictionary mapping config filenames to status (copied/exists/reset/error)
    """
    # Config files are in .fe-sm-default/config/, not templates/
    default_config_dir = PluginPaths.get_default_config_dir()

    if not default_config_dir.exists():
        return {"error": "Default config directory not found"}

    # Create target directory if it doesn't exist
    target_dir.mkdir(parents=True, exist_ok=True)

    results = {}
    config_files = ["team.json", "settings.json", "rules.md"]

    # Copy each config file
    for filename in config_files:
        source_file = default_config_dir / filename
        target_file = target_dir / filename

        if not source_file.exists():
            results[filename] = "not found in source"
            continue

        file_existed = target_file.exists()

        if file_existed and not reset:
            results[filename] = "exists"
        else:
            try:
                target_file.write_text(source_file.read_text(encoding="utf-8"))
                if file_existed and reset:
                    results[filename] = "reset"
                else:
                    results[filename] = "copied"
            except Exception as e:
                results[filename] = f"error: {str(e)}"

    return results


# Example usage
if __name__ == "__main__":
    loader = TemplateLoader()

    # Test loading a story template
    story_template = loader.load_template("story")
    print("Story template:")
    print(story_template)
    print("\n" + "=" * 80 + "\n")

    # Test rendering with variables
    variables = {
        "title": "Implement user authentication",
        "role": "frontend developer",
        "want": "users to be able to log in securely",
        "benefit": "the application is protected",
        "feature_id": "F12345",
        "points": 3,
    }

    rendered = loader.render_template("story", variables)
    print("Rendered story:")
    print(rendered)
    print("\n" + "=" * 80 + "\n")

    # List available templates
    templates = loader.list_available_templates()
    print("Available templates:")
    for work_item_type, source in templates.items():
        print(f"  {work_item_type}: {source}")
