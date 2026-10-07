"""Rally API configuration management.

Handles loading and saving configuration from ~/.claude/aig.json including:
- API key management
- Portfolio IDs and nicknames
- Default workspace and project tracking
- Config validation and auto-fix
"""

import json
from pathlib import Path
from typing import Optional, Any

from .exceptions import RallyAPIKeyError
from .paths import PluginPaths


# Configuration paths and constants
CONFIG_PATH = Path.home() / ".claude" / "aig.json"
API_KEY_URL = "https://rally1.rallydev.com/#/826846225869ud/api_key"


def _load_config() -> dict:
    """
    Load Rally configuration from ~/.claude/aig.json.

    Cross-platform compatible (Windows and Mac/Linux).
    - Windows: C:\\Users\\username\\.claude\\aig.json
    - Mac/Linux: /Users/username/.claude/aig.json

    Returns:
        Config dict with 'rally' section

    Raises:
        RallyAPIKeyError: If config file doesn't exist or is invalid
    """
    if not CONFIG_PATH.exists():
        raise RallyAPIKeyError(
            f"Rally configuration not found.\n\n"
            f"To set up your API key:\n"
            f"1. Go to: {API_KEY_URL}\n"
            f"2. Create a new API key\n"
            f"3. Provide the key to Claude Code and it will save it for you"
        )

    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            config = json.load(f)
    except json.JSONDecodeError:
        raise RallyAPIKeyError(
            f"Invalid JSON in {CONFIG_PATH}. Please fix or delete the file."
        )

    return config


def load_api_key() -> str:
    """
    Load Rally API key from ~/.claude/aig.json.

    Config format:
    {
        "rally": {
            "api_key": "your_api_key"
        }
    }

    Returns:
        API key string

    Raises:
        RallyAPIKeyError: If API key is not configured
    """
    config = _load_config()

    rally_config = config.get("rally", {})
    api_key = rally_config.get("api_key")

    if not api_key:
        raise RallyAPIKeyError(
            f"Rally API key not found in {CONFIG_PATH}.\n\n"
            f"Expected format:\n"
            f'{{"rally": {{"api_key": "your_key"}}}}\n\n'
            f"To set up your API key:\n"
            f"1. Go to: {API_KEY_URL}\n"
            f"2. Create a new API key\n"
            f"3. Provide the key to Claude Code and it will save it for you"
        )

    return api_key


def get_portfolio_ids() -> dict:
    """
    Load portfolio IDs from ~/.claude/aig.json.

    Config format:
    {
        "rally": {
            "portfolio_ids": {
                "forward engineering": "839039831411",
                "reverse engineering": "839039833045",
                "genlite": "826846225869"
            }
        }
    }

    Returns:
        Dict of portfolio IDs (nickname -> ID), or empty dict if not configured
    """
    try:
        config = _load_config()
        return config.get("rally", {}).get("portfolio_ids", {})
    except RallyAPIKeyError:
        return {}


def get_default_portfolio_id() -> Optional[str]:
    """
    Get the default portfolio ID from ~/.claude/aig.json.

    The default_portfolio can be either:
    - A nickname (e.g., "forward engineering") that maps to portfolio_ids
    - A direct portfolio ID (e.g., "839039831411")

    Config format:
    {
        "rally": {
            "default_portfolio": "forward engineering",
            "portfolio_ids": {
                "forward engineering": "839039831411",
                ...
            }
        }
    }

    Returns:
        Portfolio ID string, or None if not configured
    """
    try:
        config = _load_config()
        rally_config = config.get("rally", {})
        default = rally_config.get("default_portfolio")

        if not default:
            return None

        # Check if it's a nickname that maps to a portfolio ID
        portfolio_ids = rally_config.get("portfolio_ids", {})
        if default in portfolio_ids:
            return portfolio_ids[default]

        # Otherwise, assume it's a direct portfolio ID
        return default
    except RallyAPIKeyError:
        return None


def get_portfolio_id(nickname_or_id: Optional[str] = None) -> Optional[str]:
    """
    Get a portfolio ID by nickname, or return the default if not specified.

    Args:
        nickname_or_id: Portfolio nickname (e.g., "forward engineering") or direct ID.
                       If None, returns the default portfolio ID.

    Returns:
        Portfolio ID string, or None if not found/configured
    """
    if nickname_or_id is None:
        return get_default_portfolio_id()

    # Check if it's a nickname
    portfolio_ids = get_portfolio_ids()
    if nickname_or_id in portfolio_ids:
        return portfolio_ids[nickname_or_id]

    # Otherwise, assume it's a direct portfolio ID
    return nickname_or_id


def get_default_workspace() -> Optional[str]:
    """
    Get the default workspace name from ~/.claude/aig.json.

    Config format:
    {
        "rally": {
            "default_workspace": "General Insurance Workspace"
        }
    }

    Returns:
        Workspace name string, or None if not configured
    """
    try:
        config = _load_config()
        return config.get("rally", {}).get("default_workspace")
    except RallyAPIKeyError:
        return None


def get_last_project() -> Optional[dict]:
    """
    Get the last used project from ~/.claude/aig.json.

    This provides session continuity - if you worked with a specific project
    in the last session, this will return it so the next session can continue
    in the same project context with the complete workspace and project hierarchy.

    Config format:
    {
        "rally": {
            "last_project": {
                "workspace_id": "62135321742",
                "workspace_name": "General Insurance Workspace",
                "workspace_ref": "https://rally1.rallydev.com/slm/webservice/v2.0/workspace/62135321742",
                "parent_project_id": "000000000000",
                "parent_project_name": "AI Enablement",
                "parent_project_ref": "https://rally1.rallydev.com/slm/webservice/v2.0/project/000000000000",
                "project_id": "839039831411",
                "project_name": "Ai Enablement Forward  Engineering",
                "project_ref": "https://rally1.rallydev.com/slm/webservice/v2.0/project/839039831411"
            }
        }
    }

    Returns:
        Dict with workspace and project hierarchy info, or None if not configured
    """
    try:
        config = _load_config()
        return config.get("rally", {}).get("last_project")
    except RallyAPIKeyError:
        return None


def save_last_project(project_name: str, project_ref: str) -> None:
    """
    Save the last used project to ~/.claude/aig.json for session continuity.

    This is automatically called by the API when a project context is established,
    so the next session can pick up where you left off.

    Args:
        project_name: The project name (e.g., "Ai Enablement Forward  Engineering")
        project_ref: The project reference URL
    """
    # Ensure directory exists
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)

    # Load existing config or create new
    config = {}
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                config = json.load(f)
        except json.JSONDecodeError:
            config = {}

    # Update with last project
    if "rally" not in config:
        config["rally"] = {}
    config["rally"]["last_project"] = {
        "name": project_name,
        "ref": project_ref
    }

    # Write back with UTF-8 encoding for cross-platform compatibility
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)


def save_api_key(api_key: str) -> None:
    """
    Save Rally API key to ~/.claude/aig.json.

    Cross-platform compatible (Windows and Mac/Linux).

    Args:
        api_key: The Rally API key to save
    """
    # Ensure directory exists
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)

    # Load existing config or create new
    config = {}
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                config = json.load(f)
        except json.JSONDecodeError:
            config = {}

    # Update with new key in nested format
    if "rally" not in config:
        config["rally"] = {}
    config["rally"]["api_key"] = api_key

    # Write back with UTF-8 encoding for cross-platform compatibility
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)

    print(f"Rally API key saved to {CONFIG_PATH}")


def validate_config() -> dict:
    """
    Validate the aig.json configuration file.

    Checks for:
    - File exists and is valid JSON
    - rally section exists
    - api_key is present and non-empty
    - portfolio_ids section exists (optional)
    - default_portfolio is set (optional)

    Returns:
        Dict with validation results
    """
    result = {
        "valid": True,
        "exists": False,
        "is_json": False,
        "has_rally": False,
        "has_api_key": False,
        "has_portfolio_ids": False,
        "has_default_portfolio": False,
        "default_portfolio": None,
        "portfolio_ids": [],
        "errors": [],
        "warnings": [],
    }

    # Check if file exists
    if not CONFIG_PATH.exists():
        result["valid"] = False
        result["errors"].append(f"Config file not found: {CONFIG_PATH}")
        return result

    result["exists"] = True

    # Check if file is valid JSON
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            config = json.load(f)
        result["is_json"] = True
    except json.JSONDecodeError as e:
        result["valid"] = False
        result["errors"].append(f"Invalid JSON: {e}")
        return result

    # Check for rally section
    if "rally" not in config:
        result["valid"] = False
        result["errors"].append("Missing 'rally' section")
        return result

    result["has_rally"] = True
    rally_config = config["rally"]

    # Check for api_key
    api_key = rally_config.get("api_key")
    if api_key and isinstance(api_key, str) and len(api_key) > 0:
        result["has_api_key"] = True
    else:
        result["valid"] = False
        result["errors"].append("Missing or empty 'api_key' in rally section")

    # Check for portfolio_ids (optional but recommended)
    portfolio_ids = rally_config.get("portfolio_ids", {})
    if portfolio_ids and isinstance(portfolio_ids, dict) and len(portfolio_ids) > 0:
        result["has_portfolio_ids"] = True
        result["portfolio_ids"] = list(portfolio_ids.keys())
    else:
        result["warnings"].append("Missing 'portfolio_ids' section (optional)")

    # Check for default_portfolio (optional but recommended)
    default_portfolio = rally_config.get("default_portfolio")
    if default_portfolio and isinstance(default_portfolio, str) and len(default_portfolio) > 0:
        result["has_default_portfolio"] = True
        result["default_portfolio"] = default_portfolio

        # Validate that default_portfolio references a valid nickname if portfolio_ids exist
        if result["has_portfolio_ids"] and default_portfolio not in portfolio_ids:
            result["warnings"].append(
                f"default_portfolio '{default_portfolio}' is not in portfolio_ids"
            )
    else:
        result["warnings"].append("Missing 'default_portfolio' (optional)")

    return result


def fix_config(dry_run: bool = True) -> dict:
    """
    Fix common issues in the aig.json configuration file.

    Fixes:
    - Creates file if it doesn't exist
    - Adds rally section if missing
    - Adds empty portfolio_ids section if missing
    - Does NOT add api_key (user must provide)

    Args:
        dry_run: If True, only report what would be fixed without making changes

    Returns:
        Dict with fix results
    """
    result = {
        "validation_before": validate_config(),
        "validation_after": None,
        "fixes_applied": [],
        "dry_run": dry_run,
    }

    # Load existing config or create new
    config = {}
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                config = json.load(f)
        except json.JSONDecodeError:
            result["fixes_applied"].append("Reset invalid JSON to empty config")
            config = {}
    else:
        result["fixes_applied"].append(f"Create config file: {CONFIG_PATH}")

    # Ensure rally section exists
    if "rally" not in config:
        result["fixes_applied"].append("Add 'rally' section")
        config["rally"] = {}

    # Ensure portfolio_ids section exists (empty - user adds their own nicknames)
    if "portfolio_ids" not in config["rally"]:
        result["fixes_applied"].append("Add empty 'portfolio_ids' section")
        config["rally"]["portfolio_ids"] = {}

    # Apply fixes if not dry run
    if not dry_run and result["fixes_applied"]:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2)
        result["validation_after"] = validate_config()

    return result


# ==============================================================================
# Project-Level Configuration (Team and Rules)
# ==============================================================================


def _load_team_config() -> dict:
    """
    Load team configuration with fallback logic.

    Priority:
    1. .claude/.fe-sm/config/team.json (user customization)
    2. .fe-sm-default/config/team.json (plugin default)
    3. Empty structure (ultimate fallback)

    Returns:
        Dict with 'members' list
    """
    team_config_path = PluginPaths.find_config("team.json")

    if team_config_path:
        try:
            with open(team_config_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except json.JSONDecodeError:
            pass  # Fall through to empty

    # Ultimate fallback - empty structure
    return {"members": []}


def _load_settings_config() -> dict:
    """
    Load project settings with fallback logic.

    Priority:
    1. .claude/.fe-sm/config/settings.json (user customization)
    2. .fe-sm-default/config/settings.json (plugin default)
    3. Hardcoded defaults (ultimate fallback)

    Returns:
        Dict with project settings and preferences
    """
    # Hardcoded defaults (ultimate fallback)
    defaults = {
        "story_points": {
            "calculation_method": "skill_based",
            "max_points": 7,
            "base_hours_per_point": 8
        },
        "validation_rules": {
            "require_acceptance_criteria": True,
            "require_tasks_per_story": True,
            "require_owner_assignment": True,
            "require_html_descriptions": True,
            "max_story_points": 7
        },
        "rally_conventions": {
            "stage_story_prefix": "Administrative Tasks",
            "default_release_format": "YYYY.PIX",
            "default_iteration_format": "YYYY.PIX.IterationX",
            "auto_inherit_release_iteration": True
        },
        "templates": {
            "use_custom_templates": True,
            "custom_template_path": "./.claude/.fe-sm/templates",
            "fallback_to_defaults": True
        },
        "workflow": {
            "require_preview_before_create": True,
            "require_backup_before_update": True,
            "auto_create_administrative_tasks": True
        }
    }

    # Find settings config with fallback
    settings_config_path = PluginPaths.find_config("settings.json")

    if settings_config_path:
        try:
            with open(settings_config_path, "r", encoding="utf-8") as f:
                settings = json.load(f)
                # Merge with defaults (user settings override defaults)
                for section, values in defaults.items():
                    if section not in settings:
                        settings[section] = values
                    elif isinstance(values, dict):
                        for key, value in values.items():
                            if key not in settings[section]:
                                settings[section][key] = value
                return settings
        except json.JSONDecodeError:
            pass  # Fall through to defaults

    # Ultimate fallback - hardcoded defaults
    return defaults


def get_team_members() -> list:
    """
    Get all team members from project config.

    Returns:
        List of team member dicts
    """
    config = _load_team_config()
    return config.get("members", [])


def find_team_member(name_or_username: str) -> Optional[dict]:
    """
    Find a team member by name or Rally username.

    Supports fuzzy matching:
    1. Exact rally_username match (case-insensitive)
    2. Exact name match (case-insensitive)
    3. Partial name match

    Args:
        name_or_username: Name or Rally username to search for

    Returns:
        Team member dict if found, None otherwise
    """
    members = get_team_members()
    search = name_or_username.lower()

    # Try exact rally_username match first
    for member in members:
        if member.get("rally_username", "").lower() == search:
            return member

    # Try exact name match
    for member in members:
        if member.get("name", "").lower() == search:
            return member

    # Try partial name match
    for member in members:
        if search in member.get("name", "").lower():
            return member

    return None


def get_member_estimate_multiplier(name_or_username: str) -> Optional[float]:
    """
    Get the estimation multiplier for a team member.

    Args:
        name_or_username: Name or Rally username

    Returns:
        Multiplier float if found, None otherwise
    """
    member = find_team_member(name_or_username)
    if not member:
        return None
    return member.get("estimate_multiplier")


def add_team_member(
    name: str,
    rally_username: str,
    role: str = "",
    estimate_multiplier: Optional[float] = None,
    location: str = "",
    skills: Optional[list[str]] = None,
    notes: str = "",
    pods: Optional[list[str]] = None,
) -> dict:
    """
    Add a new team member to the project config.

    Args:
        name: Display name
        rally_username: Rally email address
        role: Job title (optional)
        estimate_multiplier: Estimation multiplier (optional, defaults to 1.0)
        location: Geographic location (optional)
        skills: List of technical skills (optional)
        notes: Additional notes (optional)
        pods: List of pod names the member belongs to (optional)

    Returns:
        The created team member dict
    """
    # Validate estimate_multiplier if provided
    if estimate_multiplier is not None and estimate_multiplier <= 0:
        raise ValueError(
            f"Invalid estimate_multiplier '{estimate_multiplier}'. Must be positive."
        )

    config = _load_team_config()

    # Check for duplicate
    existing = find_team_member(rally_username)
    if existing:
        raise ValueError(
            f"Team member with username '{rally_username}' already exists"
        )

    # Create member dict
    member = {
        "name": name,
        "rally_username": rally_username,
    }

    if role:
        member["role"] = role
    if estimate_multiplier is not None:
        member["estimate_multiplier"] = estimate_multiplier
    if location:
        member["location"] = location
    if skills:
        member["skills"] = skills
    if notes:
        member["notes"] = notes
    if pods:
        member["pods"] = pods

    # Add to config
    config["members"].append(member)

    # Save to user config directory
    claude_dir = PluginPaths.find_claude_dir()
    if not claude_dir:
        claude_dir = Path.cwd() / ".claude"

    team_config_path = claude_dir / ".fe-sm" / "config" / "team.json"
    team_config_path.parent.mkdir(parents=True, exist_ok=True)
    with open(team_config_path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)

    return member


def get_member_pods(name_or_username: str) -> list[str]:
    """
    Get the pod memberships for a team member.

    Args:
        name_or_username: Name or Rally username

    Returns:
        List of pod names, or empty list if not found or no pods assigned
    """
    member = find_team_member(name_or_username)
    if not member:
        return []
    return member.get("pods", [])


def get_team_members_by_pod(pod_name: str) -> list[dict]:
    """
    Get all team members belonging to a specific pod.

    Args:
        pod_name: Pod name to filter by (case-insensitive)

    Returns:
        List of team member dicts belonging to the pod
    """
    members = get_team_members()
    pod_lower = pod_name.lower()
    return [
        m for m in members
        if pod_lower in [p.lower() for p in m.get("pods", [])]
    ]


def get_all_pods() -> list[str]:
    """
    Get a deduplicated, sorted list of all pod names across team members.

    Returns:
        Sorted list of unique pod names
    """
    members = get_team_members()
    pods = set()
    for m in members:
        for p in m.get("pods", []):
            pods.add(p)
    return sorted(pods)


def update_team_member(name_or_username: str, **kwargs) -> Optional[dict]:
    """
    Update fields on an existing team member.

    Allowed fields: role, estimate_multiplier, location, skills, notes, pods.

    Args:
        name_or_username: Name or Rally username to find the member
        **kwargs: Fields to update

    Returns:
        Updated team member dict, or None if member not found

    Raises:
        ValueError: If an invalid field is provided or estimate_multiplier is invalid
    """
    allowed_fields = {"role", "estimate_multiplier", "location", "skills", "notes", "pods"}
    invalid_fields = set(kwargs.keys()) - allowed_fields
    if invalid_fields:
        raise ValueError(f"Invalid fields: {invalid_fields}. Allowed: {allowed_fields}")

    if "estimate_multiplier" in kwargs and kwargs["estimate_multiplier"] is not None:
        if kwargs["estimate_multiplier"] <= 0:
            raise ValueError(
                f"Invalid estimate_multiplier '{kwargs['estimate_multiplier']}'. Must be positive."
            )

    config = _load_team_config()
    members = config.get("members", [])
    search = name_or_username.lower()

    # Find the member in the config list
    target = None
    for member in members:
        if member.get("rally_username", "").lower() == search:
            target = member
            break
        if member.get("name", "").lower() == search:
            target = member
            break
    if target is None:
        for member in members:
            if search in member.get("name", "").lower():
                target = member
                break

    if target is None:
        return None

    # Apply updates
    for key, value in kwargs.items():
        if value is None:
            target.pop(key, None)
        else:
            target[key] = value

    # Save to user config directory
    claude_dir = PluginPaths.find_claude_dir()
    if not claude_dir:
        claude_dir = Path.cwd() / ".claude"

    team_config_path = claude_dir / ".fe-sm" / "config" / "team.json"
    team_config_path.parent.mkdir(parents=True, exist_ok=True)
    with open(team_config_path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)

    return target


def get_project_settings() -> dict:
    """
    Get all project settings and preferences.

    Returns:
        Dict with project settings
    """
    return _load_settings_config()


def get_project_rules() -> dict:
    """
    DEPRECATED: Use get_project_settings() instead.

    Get all project settings and preferences.

    Returns:
        Dict with project settings
    """
    return _load_settings_config()


def get_rule(section: str, key: str, default: Any = None) -> Any:
    """
    Get a specific rule value.

    Args:
        section: Rule section (e.g., 'story_points', 'writing_style')
        key: Rule key within section
        default: Default value if not found

    Returns:
        Rule value or default
    """
    rules = get_project_rules()
    return rules.get(section, {}).get(key, default)
