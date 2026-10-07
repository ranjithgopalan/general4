"""
Centralized path resolution for fe-sm plugin.
Handles both plugin bundled resources and user customizations.

Fallback Logic:
- Templates: .claude/.fe-sm/templates/ → .fe-sm-default/templates/
- Config: .claude/.fe-sm/config/ → .fe-sm-default/config/
- Backups: .claude/.fe-sm/backups/ (user level only)
"""

from pathlib import Path
from typing import Optional


class PluginPaths:
    """Centralized path management for fe-sm plugin."""

    @staticmethod
    def get_plugin_root() -> Path:
        """
        Get the fe-sm plugin root directory.

        From this file's location:
        rally_api/paths.py → rally_api (parent) → scripts (parent) →
        rally-api (parent) → skills (parent) → fe-sm (parent)

        Returns:
            Path to fe-sm plugin root directory
        """
        return Path(__file__).parent.parent.parent.parent.parent

    @staticmethod
    def get_default_templates_dir() -> Path:
        """Get plugin's default templates directory."""
        return PluginPaths.get_plugin_root() / ".fe-sm-default" / "templates"

    @staticmethod
    def get_default_config_dir() -> Path:
        """Get plugin's default config directory."""
        return PluginPaths.get_plugin_root() / ".fe-sm-default" / "config"

    @staticmethod
    def find_claude_dir(start_from: Optional[Path] = None) -> Optional[Path]:
        """
        Find .claude directory by walking up from start_from.

        Args:
            start_from: Starting directory (defaults to cwd())

        Returns:
            Path to .claude if found, None otherwise
        """
        current = start_from or Path.cwd()

        while current != current.parent:
            claude_dir = current / ".claude"
            if claude_dir.exists() and claude_dir.is_dir():
                return claude_dir
            current = current.parent

        return None

    @staticmethod
    def get_user_fe_sm_dir() -> Optional[Path]:
        """
        Get user's .claude/.fe-sm directory.

        Returns:
            Path to .claude/.fe-sm if .claude exists, None otherwise
        """
        claude_dir = PluginPaths.find_claude_dir()
        if claude_dir:
            return claude_dir / ".fe-sm"
        return None

    @staticmethod
    def get_user_templates_dir() -> Optional[Path]:
        """
        Get user's custom templates directory.

        Returns:
            Path to .claude/.fe-sm/templates if it exists, None otherwise
        """
        fe_sm_dir = PluginPaths.get_user_fe_sm_dir()
        if fe_sm_dir:
            templates_dir = fe_sm_dir / "templates"
            if templates_dir.exists() and templates_dir.is_dir():
                return templates_dir
        return None

    @staticmethod
    def get_user_config_dir() -> Optional[Path]:
        """
        Get user's custom config directory.

        Returns:
            Path to .claude/.fe-sm/config if it exists, None otherwise
        """
        fe_sm_dir = PluginPaths.get_user_fe_sm_dir()
        if fe_sm_dir:
            config_dir = fe_sm_dir / "config"
            if config_dir.exists() and config_dir.is_dir():
                return config_dir
        return None

    @staticmethod
    def get_backup_dir() -> Path:
        """
        Get backup directory, creating if needed.
        Always creates in .claude/.fe-sm/backups/ at user level.

        Returns:
            Path to backup directory
        """
        claude_dir = PluginPaths.find_claude_dir()
        if claude_dir:
            backup_dir = claude_dir / ".fe-sm" / "backups"
        else:
            # If no .claude found, create in current directory
            backup_dir = Path.cwd() / ".claude" / ".fe-sm" / "backups"

        backup_dir.mkdir(parents=True, exist_ok=True)
        return backup_dir

    @staticmethod
    def find_template(template_name: str) -> Optional[Path]:
        """
        Find a template file with fallback logic.

        Priority:
        1. User custom: .claude/.fe-sm/templates/{template_name}
        2. Plugin default: .fe-sm-default/templates/{template_name}

        Args:
            template_name: Name of template file (e.g., "story.md")

        Returns:
            Path to template if found, None otherwise
        """
        # Try user custom templates
        user_templates = PluginPaths.get_user_templates_dir()
        if user_templates:
            user_template = user_templates / template_name
            if user_template.exists() and user_template.is_file():
                return user_template

        # Fall back to plugin default
        default_template = PluginPaths.get_default_templates_dir() / template_name
        if default_template.exists() and default_template.is_file():
            return default_template

        return None

    @staticmethod
    def find_config(config_name: str) -> Optional[Path]:
        """
        Find a config file with fallback logic.

        Priority:
        1. User custom: .claude/.fe-sm/config/{config_name}
        2. Plugin default: .fe-sm-default/config/{config_name}

        Args:
            config_name: Name of config file (e.g., "team.json", "settings.json")

        Returns:
            Path to config if found, None otherwise
        """
        # Try user custom config
        user_config_dir = PluginPaths.get_user_config_dir()
        if user_config_dir:
            user_config = user_config_dir / config_name
            if user_config.exists() and user_config.is_file():
                return user_config

        # Fall back to plugin default
        default_config = PluginPaths.get_default_config_dir() / config_name
        if default_config.exists() and default_config.is_file():
            return default_config

        return None

    @staticmethod
    def validate_plugin_resources() -> None:
        """
        Validate that plugin resources are where expected.

        Raises:
            RuntimeError: If required plugin resources are missing
        """
        plugin_root = PluginPaths.get_plugin_root()
        required_dirs = [
            (plugin_root / ".fe-sm-default" / "templates", "templates"),
            (plugin_root / ".fe-sm-default" / "config", "config"),
        ]

        missing = []
        for dir_path, name in required_dirs:
            if not dir_path.exists():
                missing.append(f"  - {name}: {dir_path}")

        if missing:
            raise RuntimeError(
                f"Plugin resources not found:\n" + "\n".join(missing) + "\n"
                f"Expected plugin at: {plugin_root}\n"
                f"This usually means the plugin was not installed correctly."
            )
