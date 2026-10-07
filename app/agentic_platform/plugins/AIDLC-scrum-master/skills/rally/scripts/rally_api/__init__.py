"""Rally API - Python wrapper for Rally REST API.

A modular package for interacting with Rally's REST API.

Usage:
    from rally_api import RallyAPI
    from rally_api.exceptions import RallyAPIKeyError
    from rally_api.config import load_api_key

    api = RallyAPI()
    stories = api.query("hierarchicalrequirement", query='(Name contains "test")')
"""

# Core client
from .client import RallyAPI, RALLY_BASE_URL, DEFAULT_MAX_WORKERS

# Exceptions
from .exceptions import (
    RallyAPIKeyError,
    RallyItemNotFoundError,
    RallyValidationError,
)

# Configuration functions
from .config import (
    load_api_key,
    get_portfolio_ids,
    get_default_portfolio_id,
    get_portfolio_id,
    get_default_workspace,
    get_last_project,
    save_last_project,
    save_api_key,
    validate_config,
    fix_config,
    get_member_pods,
    get_team_members_by_pod,
    get_all_pods,
    update_team_member,
    CONFIG_PATH,
    API_KEY_URL,
)

# Parsing utilities
from .parsing import (
    HTMLTextExtractor,
    parse_date,
    extract_dates_from_description,
    extract_rally_links,
    markdown_to_html,
    validate_description_format,
)

# Team-specific workflows
from .team_workflows import (
    create_feature_with_stages,
)

# Capacity and assignment recommendations
from .capacity import (
    calculate_team_capacity,
    format_capacity_report,
    render_progress_bar,
    recommend_assignments,
    format_assignment_plan,
    TeamCapacityReport,
    MemberCapacity,
    AssignmentRecommendation,
    AssignmentPlan,
)

# Backup utility
from .backup import RallyBackup

# Public API
__all__ = [
    # Core
    'RallyAPI',
    'RALLY_BASE_URL',
    'DEFAULT_MAX_WORKERS',
    # Exceptions
    'RallyAPIKeyError',
    'RallyItemNotFoundError',
    'RallyValidationError',
    # Config
    'load_api_key',
    'get_portfolio_ids',
    'get_default_portfolio_id',
    'get_portfolio_id',
    'get_default_workspace',
    'get_last_project',
    'save_last_project',
    'save_api_key',
    'validate_config',
    'fix_config',
    'get_member_pods',
    'get_team_members_by_pod',
    'get_all_pods',
    'update_team_member',
    'CONFIG_PATH',
    'API_KEY_URL',
    # Parsing
    'HTMLTextExtractor',
    'parse_date',
    'extract_dates_from_description',
    'extract_rally_links',
    'markdown_to_html',
    'validate_description_format',
    # Team workflows
    'create_feature_with_stages',
    # Capacity and assignments
    'calculate_team_capacity',
    'format_capacity_report',
    'render_progress_bar',
    'recommend_assignments',
    'format_assignment_plan',
    'TeamCapacityReport',
    'MemberCapacity',
    'AssignmentRecommendation',
    'AssignmentPlan',
    # Backup
    'RallyBackup',
]
