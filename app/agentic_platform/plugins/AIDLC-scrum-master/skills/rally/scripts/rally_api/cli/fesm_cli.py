#!/usr/bin/env python3
"""
FE-SM CLI - Unified command-line interface for fe-sm plugin setup and configuration.

Commands:
    install     - Interactive setup wizard: gets API key, tests it, saves to ~/.claude/aig.json
    setup       - Verify API key exists in ~/.claude/aig.json, guide user if not
    customize   - Copy templates and settings to .fe-sm (skip existing files)
    reset       - Copy templates and settings to .fe-sm (overwrite all files)

Usage:
    python fesm_cli.py install
    python fesm_cli.py setup
    python fesm_cli.py customize
    python fesm_cli.py reset
"""

import json
import sys
from pathlib import Path
from typing import Dict, Optional

# Use relative imports since we're inside the rally_api package
try:
    from .. import RallyAPI, RallyAPIKeyError
    from ..config import CONFIG_PATH, API_KEY_URL, save_api_key
    from ..template_loader import copy_default_templates, copy_config_files
    from ..paths import PluginPaths
except ImportError as e:
    print(f"Error: Could not import required modules: {e}", file=sys.stderr)
    sys.exit(1)


# ==============================================================================
# Configuration Management
# ==============================================================================


def check_config() -> Dict:
    """Check if Rally is configured in ~/.claude/aig.json."""
    if not CONFIG_PATH.exists():
        return {
            "configured": False,
            "error": "Config file not found",
            "path": str(CONFIG_PATH)
        }

    try:
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            config = json.load(f)

        rally_config = config.get('rally', {})
        api_key = rally_config.get('api_key', '')

        return {
            "configured": bool(api_key),
            "has_api_key": bool(api_key),
            "has_workspace": bool(rally_config.get('default_workspace')),
            "has_project": bool(rally_config.get('last_project')),
            "api_key_preview": ('*' * 20 + api_key[-4:]) if len(api_key) > 4 else '****',
            "workspace": rally_config.get('default_workspace'),
            "path": str(CONFIG_PATH)
        }

    except json.JSONDecodeError:
        return {
            "configured": False,
            "error": "Invalid JSON in config file",
            "path": str(CONFIG_PATH)
        }
    except Exception as e:
        return {
            "configured": False,
            "error": str(e),
            "path": str(CONFIG_PATH)
        }


def validate_api_key(api_key: str) -> Dict:
    """
    Validate API key format and test it by querying workspaces.

    Args:
        api_key: The Rally API key to validate

    Returns:
        Dict with validation results
    """
    # Basic format validation
    if not api_key.startswith('_'):
        return {
            "valid": False,
            "error": "API key must start with underscore (_)"
        }

    if len(api_key) < 20:
        return {
            "valid": False,
            "error": "API key is too short (must be at least 20 characters)"
        }

    if ' ' in api_key:
        return {
            "valid": False,
            "error": "API key cannot contain spaces"
        }

    # Test by querying workspaces
    try:
        api = RallyAPI(api_key=api_key)
        workspaces = api.get_workspaces()

        return {
            "valid": True,
            "workspaces": [
                {
                    "ObjectID": ws["ObjectID"],
                    "Name": ws["Name"],
                    "_ref": ws["_ref"]
                }
                for ws in workspaces
            ]
        }

    except RallyAPIKeyError as e:
        return {
            "valid": False,
            "error": f"API key authentication failed: {str(e)}"
        }
    except Exception as e:
        return {
            "valid": False,
            "error": f"Unexpected error: {str(e)}"
        }


# ==============================================================================
# Rally API Helper Functions (for command files to import)
# ==============================================================================


def get_workspaces(api_key: Optional[str] = None) -> Dict:
    """
    Get all workspaces accessible to the user.

    Args:
        api_key: Optional API key. If not provided, loads from config.

    Returns:
        Dict with workspaces list or error
    """
    try:
        if api_key:
            api = RallyAPI(api_key=api_key)
        else:
            api = RallyAPI()

        workspaces = api.get_workspaces()

        return {
            "success": True,
            "workspaces": [
                {
                    "ObjectID": ws["ObjectID"],
                    "Name": ws["Name"],
                    "_ref": ws["_ref"]
                }
                for ws in workspaces
            ]
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }


def get_parent_projects(workspace_ref: str, api_key: Optional[str] = None) -> Dict:
    """
    Get all parent projects (no parent) in a workspace.

    Args:
        workspace_ref: Workspace reference URL
        api_key: Optional API key. If not provided, loads from config.

    Returns:
        Dict with parent projects list or error
    """
    try:
        if api_key:
            api = RallyAPI(api_key=api_key)
        else:
            api = RallyAPI()

        params = {
            'workspace': workspace_ref,
            'fetch': 'Name,ObjectID,_ref,State,Parent,Children',
            'query': '((State = "Open") AND (Parent = null))',
            'pagesize': 200
        }

        response = api.session.get(f"{api.base_url}/project", params=params)
        result = response.json()
        projects = result.get('QueryResult', {}).get('Results', [])

        return {
            "success": True,
            "projects": [
                {
                    "ObjectID": p["ObjectID"],
                    "Name": p["Name"],
                    "_ref": p["_ref"],
                    "ChildCount": p.get("Children", {}).get("Count", 0)
                }
                for p in projects
            ]
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }


def get_child_projects(workspace_ref: str, parent_id: str, api_key: Optional[str] = None) -> Dict:
    """
    Get all child projects under a parent project.

    Args:
        workspace_ref: Workspace reference URL
        parent_id: Parent project ObjectID
        api_key: Optional API key. If not provided, loads from config.

    Returns:
        Dict with child projects list or error
    """
    try:
        if api_key:
            api = RallyAPI(api_key=api_key)
        else:
            api = RallyAPI()

        params = {
            'workspace': workspace_ref,
            'fetch': 'Name,ObjectID,_ref,State,Parent',
            'query': f'((State = "Open") AND (Parent.ObjectID = {parent_id}))',
            'pagesize': 200
        }

        response = api.session.get(f"{api.base_url}/project", params=params)
        result = response.json()
        projects = result.get('QueryResult', {}).get('Results', [])

        return {
            "success": True,
            "projects": [
                {
                    "ObjectID": p["ObjectID"],
                    "Name": p["Name"],
                    "_ref": p["_ref"]
                }
                for p in projects
            ]
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }


def save_project_config(
    api_key: str,
    workspace_id: str,
    workspace_name: str,
    workspace_ref: str,
    parent_project_id: str,
    parent_project_name: str,
    parent_project_ref: str,
    project_id: str,
    project_name: str,
    project_ref: str,
    default_workspace: Optional[str] = None,
    default_portfolio: Optional[str] = None,
    portfolio_ids: Optional[Dict] = None
) -> Dict:
    """
    Save complete Rally configuration to ~/.claude/aig.json.

    Args:
        api_key: Rally API key
        workspace_id: Workspace ObjectID
        workspace_name: Workspace name
        workspace_ref: Workspace reference URL
        parent_project_id: Parent project ObjectID
        parent_project_name: Parent project name
        parent_project_ref: Parent project reference URL
        project_id: Child project ObjectID
        project_name: Child project name
        project_ref: Child project reference URL
        default_workspace: Default workspace name (optional)
        default_portfolio: Default portfolio (optional)
        portfolio_ids: Portfolio ID mappings (optional)

    Returns:
        Dict with success status
    """
    try:
        # Ensure directory exists
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)

        # Load existing config or create new
        config = {}
        if CONFIG_PATH.exists():
            with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
                config = json.load(f)

        # Update Rally section with complete hierarchy
        rally_config = {
            "api_key": api_key,
            "last_project": {
                "workspace_id": workspace_id,
                "workspace_name": workspace_name,
                "workspace_ref": workspace_ref,
                "parent_project_id": parent_project_id,
                "parent_project_name": parent_project_name,
                "parent_project_ref": parent_project_ref,
                "project_id": project_id,
                "project_name": project_name,
                "project_ref": project_ref
            }
        }

        # Add optional fields if provided
        if default_workspace:
            rally_config["default_workspace"] = default_workspace
        if default_portfolio:
            rally_config["default_portfolio"] = default_portfolio
        if portfolio_ids:
            rally_config["portfolio_ids"] = portfolio_ids

        config["rally"] = rally_config

        # Save with pretty formatting
        with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=2)

        return {
            "success": True,
            "path": str(CONFIG_PATH),
            "message": "Configuration saved successfully"
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }


def update_project_hierarchy(
    workspace_id: str,
    workspace_name: str,
    workspace_ref: str,
    parent_project_id: str,
    parent_project_name: str,
    parent_project_ref: str,
    project_id: str,
    project_name: str,
    project_ref: str
) -> Dict:
    """
    Update only the project hierarchy in existing config.

    Args:
        workspace_id: Workspace ObjectID
        workspace_name: Workspace name
        workspace_ref: Workspace reference URL
        parent_project_id: Parent project ObjectID
        parent_project_name: Parent project name
        parent_project_ref: Parent project reference URL
        project_id: Child project ObjectID
        project_name: Child project name
        project_ref: Child project reference URL

    Returns:
        Dict with success status
    """
    try:
        if not CONFIG_PATH.exists():
            return {
                "success": False,
                "error": "Config file not found. Run /fesm-install first."
            }

        # Load existing config
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            config = json.load(f)

        if 'rally' not in config:
            return {
                "success": False,
                "error": "Rally configuration not found. Run /fesm-install first."
            }

        # Update last_project only
        config['rally']['last_project'] = {
            'workspace_id': workspace_id,
            'workspace_name': workspace_name,
            'workspace_ref': workspace_ref,
            'parent_project_id': parent_project_id,
            'parent_project_name': parent_project_name,
            'parent_project_ref': parent_project_ref,
            'project_id': project_id,
            'project_name': project_name,
            'project_ref': project_ref
        }

        # Save config
        with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=2)

        return {
            "success": True,
            "path": str(CONFIG_PATH),
            "message": "Project hierarchy updated successfully"
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }


# ==============================================================================
# Template and Config Customization
# ==============================================================================


def customize_templates(reset: bool = False) -> Dict:
    """
    Copy templates and config files to .fe-sm directory.

    Args:
        reset: If True, overwrite existing files. If False, skip existing files.

    Returns:
        Dict with operation results
    """
    results = {
        "config_files": {},
        "templates": {},
        "errors": []
    }

    # Find or create .claude directory
    claude_dir = PluginPaths.find_claude_dir()
    if not claude_dir:
        claude_dir = Path.cwd() / ".claude"

    # Create target directories
    config_dir = claude_dir / ".fe-sm" / "config"
    templates_dir = claude_dir / ".fe-sm" / "templates"

    config_dir.mkdir(parents=True, exist_ok=True)
    templates_dir.mkdir(parents=True, exist_ok=True)

    # Copy configuration files
    try:
        results["config_files"] = copy_config_files(config_dir, reset=reset)
    except Exception as e:
        results["errors"].append(f"Error copying config files: {str(e)}")

    # Copy work item templates
    try:
        results["templates"] = copy_default_templates(templates_dir, reset=reset)
    except Exception as e:
        results["errors"].append(f"Error copying templates: {str(e)}")

    # Update .gitignore
    try:
        gitignore_result = update_gitignore()
        results["gitignore"] = gitignore_result
    except Exception as e:
        results["errors"].append(f"Error updating .gitignore: {str(e)}")

    return results


def update_gitignore() -> Dict:
    """
    Add .claude/.fe-sm/backups and .claude/.fe-sm/config/aig.json to .gitignore.

    Returns:
        Dict with status and added entries
    """
    gitignore_path = Path.cwd() / ".gitignore"
    entries_to_add = [".claude/.fe-sm/backups/", ".claude/.fe-sm/config/aig.json"]

    # Read existing gitignore
    if gitignore_path.exists():
        gitignore_content = gitignore_path.read_text(encoding="utf-8")
    else:
        gitignore_content = ""

    # Find entries that need to be added
    added = []
    for entry in entries_to_add:
        if entry not in gitignore_content:
            added.append(entry)

    # Add entries if needed
    if added:
        # Add newline before entries if file doesn't end with newline
        if gitignore_content and not gitignore_content.endswith("\n"):
            gitignore_content += "\n"
        gitignore_content += "\n# Rally fe-sm plugin (auto-generated)\n"
        for entry in added:
            gitignore_content += f"{entry}\n"
        gitignore_path.write_text(gitignore_content, encoding="utf-8")
        return {"status": "updated", "added": added}
    else:
        return {"status": "already_present", "added": []}


# ==============================================================================
# Command Implementations
# ==============================================================================


def cmd_install():
    """
    Interactive setup wizard to install and configure Rally API.

    This command is meant to be called from the fesm-install.md command file,
    which handles the interactive prompts and user questions via AskUserQuestion.

    This function provides the Python utilities needed by that command:
    - API key validation
    - Workspace queries
    - Config file management
    """
    print("Error: This command should be called from /fesm-install", file=sys.stderr)
    print("The install command requires interactive prompts that are handled by Claude Code.", file=sys.stderr)
    print("", file=sys.stderr)
    print("To set up Rally API, please run: /fesm-install", file=sys.stderr)
    return 1


def cmd_setup():
    """
    Verify Rally API is configured and provide guidance if not.

    Returns:
        Exit code (0 = configured, 1 = not configured)
    """
    result = check_config()

    # Add setup guidance to JSON output
    if not result["configured"]:
        result["setup_instructions"] = {
            "message": "Rally API is not configured",
            "steps": [
                f"1. Go to: {API_KEY_URL}",
                "2. Click 'Create New API Key'",
                "3. Copy the generated API key (starts with underscore)",
                "4. Paste it in this chat, and I'll save it for you"
            ],
            "api_key_url": API_KEY_URL,
            "config_path": str(CONFIG_PATH)
        }
    else:
        result["status"] = "configured"
        result["message"] = "Rally API is configured and ready to use"

    print(json.dumps(result, indent=2))
    return 0 if result["configured"] else 1


def cmd_customize():
    """
    Copy templates and config files to .fe-sm (skip existing files).

    Returns:
        Exit code (0 = success, 1 = errors)
    """
    results = customize_templates(reset=False)

    # Format as JSON output
    output = {
        "success": len(results["errors"]) == 0,
        "operation": "customize",
        "message": "Copied templates and config files to .claude/.fe-sm/ (existing files preserved)",
        "config_files": results.get("config_files", {}),
        "templates": results.get("templates", {}),
        "gitignore": results.get("gitignore", {}),
        "errors": results.get("errors", []),
        "next_steps": [
            "Edit config files in .claude/.fe-sm/config/",
            "Edit templates in .claude/.fe-sm/templates/",
            "The plugin will now use your custom configurations"
        ]
    }

    print(json.dumps(output, indent=2))
    return 0 if output["success"] else 1


def cmd_reset():
    """
    Copy templates and config files to .fe-sm (overwrite all files).

    Returns:
        Exit code (0 = success, 1 = errors)
    """
    results = customize_templates(reset=True)

    # Format as JSON output
    output = {
        "success": len(results["errors"]) == 0,
        "operation": "reset",
        "message": "Reset templates and config files in .claude/.fe-sm/ (all files overwritten)",
        "config_files": results.get("config_files", {}),
        "templates": results.get("templates", {}),
        "gitignore": results.get("gitignore", {}),
        "errors": results.get("errors", []),
        "next_steps": [
            "Templates and config files have been reset to defaults",
            "You can now edit them in .claude/.fe-sm/"
        ]
    }

    print(json.dumps(output, indent=2))
    return 0 if output["success"] else 1


# ==============================================================================
# Main Entry Point
# ==============================================================================


def main():
    """Main entry point for CLI."""
    if len(sys.argv) < 2:
        print(__doc__, file=sys.stderr)
        return 1

    command = sys.argv[1].lower()

    commands = {
        "install": cmd_install,
        "setup": cmd_setup,
        "customize": cmd_customize,
        "reset": cmd_reset,
    }

    if command not in commands:
        print(f"Error: Unknown command: {command}", file=sys.stderr)
        print(__doc__, file=sys.stderr)
        return 1

    # Execute command
    try:
        return commands[command]()
    except KeyboardInterrupt:
        print("\n\nOperation cancelled by user.", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
