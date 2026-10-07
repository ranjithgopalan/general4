"""Rally API core client.

Core RallyAPI class with HTTP methods and Rally operations.
"""

import base64
import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Optional, Any
import requests

from .exceptions import RallyItemNotFoundError, RallyValidationError
from .config import load_api_key, get_default_workspace, get_last_project, save_last_project, get_member_estimate_multiplier
from .backup import RallyBackup
from .template_loader import TemplateLoader
from .estimation import (
    ComplexityFactors,
    EstimationResult,
    estimate_story,
    estimate_story_ai_analysis,
    format_estimation_result
)
from .capacity import (
    calculate_team_capacity,
    format_capacity_report,
    recommend_assignments as _recommend_assignments,
    format_assignment_plan,
)
from .config import find_team_member as _find_team_member

RALLY_BASE_URL = "https://rally1.rallydev.com/slm/webservice/v2.0"
DEFAULT_MAX_WORKERS = 10


class RallyAPI:
    """Generic Rally API client for making authenticated requests."""

    def __init__(self, api_key: Optional[str] = None, default_workspace: Optional[str] = None):
        """
        Initialize Rally API client.

        Args:
            api_key: Rally API key. If not provided, loads from ~/.claude/aig.json
            default_workspace: Default workspace name to search first. If not provided,
                              loads from config (~/.claude/aig.json rally.default_workspace)

        Raises:
            RallyAPIKeyError: If API key is not provided and not found in config
        """
        if api_key is None:
            api_key = load_api_key()

        self.api_key = api_key
        self.rally_url = RALLY_BASE_URL
        self.session = requests.Session()
        self.session.headers.update(
            {"zsessionid": api_key, "Content-Type": "application/json"}
        )

        # Initialize workspace/project attributes
        self.workspace_ref: Optional[str] = None
        self.workspace_id: Optional[str] = None
        self.workspace_name: Optional[str] = None
        self.project_ref: Optional[str] = None

        # Initialize backup utility
        self._backup_util = None

        # Initialize template loader
        self._template_loader = TemplateLoader()
        self.project_id: Optional[str] = None
        self.project_name: Optional[str] = None
        self.parent_project_ref: Optional[str] = None
        self.parent_project_id: Optional[str] = None
        self.parent_project_name: Optional[str] = None
        self.default_workspace_name: Optional[str] = default_workspace or get_default_workspace()
        self._workspaces_cache: Optional[list[dict]] = None
        self._state_ref_cache: dict[str, dict[str, str]] = {}  # type_name -> {state_name -> _ref}

        # Load last project for session continuity - this loads the complete hierarchy
        last_project = get_last_project()
        if last_project:
            # Workspace info
            self.workspace_id = last_project.get("workspace_id")
            self.workspace_name = last_project.get("workspace_name")
            self.workspace_ref = last_project.get("workspace_ref")

            # Project info (the child project where work is tracked)
            self.project_id = last_project.get("project_id")
            self.project_name = last_project.get("project_name")
            self.project_ref = last_project.get("project_ref")

            # Parent project info
            self.parent_project_id = last_project.get("parent_project_id")
            self.parent_project_name = last_project.get("parent_project_name")
            self.parent_project_ref = last_project.get("parent_project_ref")

    def set_workspace(self, workspace_ref: str) -> None:
        """Set the workspace context for subsequent requests."""
        self.workspace_ref = workspace_ref

    def set_project(self, project_ref: str, project_name: Optional[str] = None) -> None:
        """
        Set the project context for subsequent requests and persist it to config.

        Args:
            project_ref: The project reference URL
            project_name: Optional project name (will be extracted from ref if not provided)
        """
        self.project_ref = project_ref
        if project_name:
            self.project_name = project_name
            # Save to config for session continuity
            save_last_project(project_name, project_ref)

    def _get_workspaces_ordered(self) -> list[dict]:
        """
        Get workspaces with the default workspace first.

        Returns:
            List of workspace dicts, with default workspace at index 0 if configured
        """
        if self._workspaces_cache is None:
            self._workspaces_cache = self.get_workspaces()

        workspaces = self._workspaces_cache.copy()

        # If default workspace is configured, move it to the front
        if self.default_workspace_name:
            default_ws = None
            other_ws = []
            for ws in workspaces:
                if ws.get("Name") == self.default_workspace_name:
                    default_ws = ws
                else:
                    other_ws.append(ws)

            if default_ws:
                return [default_ws] + other_ws

        return workspaces

    def get(
        self,
        endpoint: str,
        params: Optional[dict] = None,
        include_scope: bool = True,
    ) -> dict:
        """
        Make a GET request to the Rally API.

        Args:
            endpoint: API endpoint (e.g., 'portfolioitem/feature' or full URL)
            params: Query parameters
            include_scope: Whether to include workspace/project scope

        Returns:
            JSON response as dict
        """
        if endpoint.startswith("http"):
            url = endpoint
        else:
            url = f"{RALLY_BASE_URL}/{endpoint}"

        if params is None:
            params = {}

        if include_scope:
            if self.workspace_ref and "workspace" not in params:
                params["workspace"] = self.workspace_ref
            if self.project_ref and "project" not in params:
                params["project"] = self.project_ref
                # Include child projects in the search
                params["projectScopeDown"] = "true"
                params["projectScopeUp"] = "false"

        response = self.session.get(url, params=params)
        response.raise_for_status()
        return response.json()

    def query(
        self,
        object_type: str,
        query: Optional[str] = None,
        fetch: str = "Name,FormattedID,ObjectID",
        order: Optional[str] = None,
        pagesize: int = 200,
        include_project_scope: bool = True,
    ) -> list[dict]:
        """
        Query Rally objects with filtering.

        Args:
            object_type: Rally object type (e.g., 'hierarchicalrequirement', 'task')
            query: Rally query string (e.g., '(Feature.ObjectID = "123")')
            fetch: Comma-separated fields to fetch
            order: Sort order (e.g., 'StartDate')
            pagesize: Number of results per page
            include_project_scope: Whether to include project in scope

        Returns:
            List of matching objects
        """
        params = {
            "fetch": fetch,
            "pagesize": pagesize,
        }

        if query:
            params["query"] = query
        if order:
            params["order"] = order

        if not include_project_scope:
            # Only include workspace, not project
            if self.workspace_ref:
                params["workspace"] = self.workspace_ref
            result = self.session.get(
                f"{RALLY_BASE_URL}/{object_type}", params=params
            ).json()
        else:
            result = self.get(object_type, params)

        return result.get("QueryResult", {}).get("Results", [])

    def get_object(self, ref: str, fetch: str = "Name,FormattedID,ObjectID") -> dict:
        """
        Get a single Rally object by its reference URL.

        Args:
            ref: Object reference URL (_ref)
            fetch: Comma-separated fields to fetch

        Returns:
            Object data dict
        """
        response = self.session.get(ref, params={"fetch": fetch})
        response.raise_for_status()
        data = response.json()

        # Handle different response structures
        for key in [
            "PortfolioItem",
            "Theme",
            "Initiative",
            "Epic",
            "Capability",
            "Feature",
            "HierarchicalRequirement",
            "Task",
            "Defect",
            "Iteration",
        ]:
            if key in data:
                return data[key]

        # Try to find any result with a Name field
        for key, value in data.items():
            if isinstance(value, dict) and "Name" in value:
                return value

        return data

    def get_workspaces(self) -> list[dict]:
        """Get all workspaces from the subscription."""
        sub_result = self.get(
            "subscription", params={"fetch": "Workspaces"}, include_scope=False
        )
        sub_data = sub_result.get("Subscription", {})
        workspaces_ref = sub_data.get("Workspaces", {}).get("_ref")

        if workspaces_ref:
            ws_result = self.session.get(
                workspaces_ref, params={"fetch": "Name,ObjectID,State", "pagesize": 50}
            ).json()
            return ws_result.get("QueryResult", {}).get("Results", [])
        return []

    def find_portfolio_item(
        self,
        formatted_id: str,
        item_type: str = "capability",
        raise_if_not_found: bool = False,
    ) -> Optional[dict]:
        """
        Find a portfolio item by FormattedID, searching last_project first then all workspaces in parallel.

        Args:
            formatted_id: The FormattedID (e.g., 'T12345', 'I12345', 'E12345', 'C17205', 'F12345')
            item_type: Portfolio item type ('theme', 'initiative', 'epic', 'capability', 'feature')
            raise_if_not_found: If True, raise RallyItemNotFoundError instead of returning None

        Returns:
            Portfolio item dict or None if not found

        Raises:
            RallyItemNotFoundError: If item not found and raise_if_not_found=True
        """
        # First, try searching in the last_project if available (for faster, more accurate results)
        if self.project_ref:
            params = {
                "query": f'(FormattedID = "{formatted_id}")',
                "fetch": "Name,FormattedID,ObjectID,_ref,Children,Project,Workspace,Owner,State,ScheduleState,Iteration,Release,PlanEstimate,Description",
                "project": self.project_ref,
            }
            response = self.session.get(
                f"{RALLY_BASE_URL}/portfolioitem/{item_type}", params=params
            )
            result = response.json()
            items = result.get("QueryResult", {}).get("Results", [])
            if items:
                item = items[0]
                # Update workspace context if needed
                if item.get("Workspace"):
                    self.workspace_ref = item["Workspace"].get("_ref")
                return item

        # Use ordered workspaces (default first)
        workspaces = self._get_workspaces_ordered()
        workspace_names = [ws.get("Name", "Unknown") for ws in workspaces]

        def search_workspace(ws: dict) -> Optional[dict]:
            """Search a single workspace for the portfolio item."""
            ws_ref = ws.get("_ref")
            params = {
                "query": f'(FormattedID = "{formatted_id}")',
                "fetch": "Name,FormattedID,ObjectID,_ref,Children,Project,Workspace,Owner,State,ScheduleState,Iteration,Release,PlanEstimate,Description",
                "workspace": ws_ref,
            }
            response = self.session.get(
                f"{RALLY_BASE_URL}/portfolioitem/{item_type}", params=params
            )
            result = response.json()
            items = result.get("QueryResult", {}).get("Results", [])
            return items[0] if items else None

        # Search all workspaces in parallel
        with ThreadPoolExecutor(max_workers=DEFAULT_MAX_WORKERS) as executor:
            futures = {executor.submit(search_workspace, ws): ws for ws in workspaces}

            for future in as_completed(futures):
                item = future.result()
                if item:
                    # Cancel remaining futures
                    for f in futures:
                        f.cancel()
                    # Update workspace/project context and persist for session continuity
                    if item.get("Workspace"):
                        self.workspace_ref = item["Workspace"].get("_ref")
                    if item.get("Project"):
                        project_ref = item["Project"].get("_ref")
                        project_name = item["Project"].get("_refObjectName")
                        if project_ref and project_name:
                            self.set_project(project_ref, project_name)
                    return item

        # Not found
        if raise_if_not_found:
            raise RallyItemNotFoundError(
                formatted_id=formatted_id,
                item_type=f"portfolioitem/{item_type}",
                workspaces_searched=workspace_names,
                default_workspace=self.default_workspace_name,
            )

        return None

    def find_user_story(
        self,
        formatted_id: str,
        raise_if_not_found: bool = False,
    ) -> Optional[dict]:
        """
        Find a user story by FormattedID, searching last_project first then all workspaces in parallel.

        Args:
            formatted_id: The FormattedID (e.g., 'US12345')
            raise_if_not_found: If True, raise RallyItemNotFoundError instead of returning None

        Returns:
            User story dict or None if not found

        Raises:
            RallyItemNotFoundError: If story not found and raise_if_not_found=True
        """
        # First, try searching in the last_project if available (for faster, more accurate results)
        if self.project_ref:
            params = {
                "query": f'(FormattedID = "{formatted_id}")',
                "fetch": "Name,FormattedID,ObjectID,_ref,ScheduleState,Owner,Feature,Tasks,Project,Workspace",
                "project": self.project_ref,
            }
            response = self.session.get(
                f"{RALLY_BASE_URL}/hierarchicalrequirement", params=params
            )
            result = response.json()
            items = result.get("QueryResult", {}).get("Results", [])
            if items:
                item = items[0]
                # Update workspace context if needed
                if item.get("Workspace"):
                    self.workspace_ref = item["Workspace"].get("_ref")
                return item

        # Use ordered workspaces (default first)
        workspaces = self._get_workspaces_ordered()
        workspace_names = [ws.get("Name", "Unknown") for ws in workspaces]

        def search_workspace(ws: dict) -> Optional[dict]:
            """Search a single workspace for the user story."""
            ws_ref = ws.get("_ref")
            params = {
                "query": f'(FormattedID = "{formatted_id}")',
                "fetch": "Name,FormattedID,ObjectID,_ref,ScheduleState,Owner,Feature,Tasks,Project,Workspace",
                "workspace": ws_ref,
            }
            response = self.session.get(
                f"{RALLY_BASE_URL}/hierarchicalrequirement", params=params
            )
            result = response.json()
            items = result.get("QueryResult", {}).get("Results", [])
            return items[0] if items else None

        # Search all workspaces in parallel
        with ThreadPoolExecutor(max_workers=DEFAULT_MAX_WORKERS) as executor:
            futures = {executor.submit(search_workspace, ws): ws for ws in workspaces}

            for future in as_completed(futures):
                item = future.result()
                if item:
                    # Cancel remaining futures
                    for f in futures:
                        f.cancel()
                    # Update workspace/project context and persist for session continuity
                    if item.get("Workspace"):
                        self.workspace_ref = item["Workspace"].get("_ref")
                    if item.get("Project"):
                        project_ref = item["Project"].get("_ref")
                        project_name = item["Project"].get("_refObjectName")
                        if project_ref and project_name:
                            self.set_project(project_ref, project_name)
                    return item

        # Not found
        if raise_if_not_found:
            raise RallyItemNotFoundError(
                formatted_id=formatted_id,
                item_type="hierarchicalrequirement (User Story)",
                workspaces_searched=workspace_names,
                default_workspace=self.default_workspace_name,
            )

        return None

    def find_task(
        self,
        formatted_id: str,
        raise_if_not_found: bool = False,
    ) -> Optional[dict]:
        """
        Find a task by FormattedID, searching last_project first then all workspaces in parallel.

        Args:
            formatted_id: The FormattedID (e.g., 'TA12345')
            raise_if_not_found: If True, raise RallyItemNotFoundError instead of returning None

        Returns:
            Task dict or None if not found

        Raises:
            RallyItemNotFoundError: If task not found and raise_if_not_found=True
        """
        # First, try searching in the last_project if available (for faster, more accurate results)
        if self.project_ref:
            params = {
                "query": f'(FormattedID = "{formatted_id}")',
                "fetch": "Name,FormattedID,ObjectID,_ref,State,Owner,Estimate,WorkProduct,Project,Workspace",
                "project": self.project_ref,
            }
            response = self.session.get(
                f"{RALLY_BASE_URL}/task", params=params
            )
            result = response.json()
            items = result.get("QueryResult", {}).get("Results", [])
            if items:
                item = items[0]
                # Update workspace context if needed
                if item.get("Workspace"):
                    self.workspace_ref = item["Workspace"].get("_ref")
                return item

        # Use ordered workspaces (default first)
        workspaces = self._get_workspaces_ordered()
        workspace_names = [ws.get("Name", "Unknown") for ws in workspaces]

        def search_workspace(ws: dict) -> Optional[dict]:
            """Search a single workspace for the task."""
            ws_ref = ws.get("_ref")
            params = {
                "query": f'(FormattedID = "{formatted_id}")',
                "fetch": "Name,FormattedID,ObjectID,_ref,State,Owner,Estimate,WorkProduct,Project,Workspace",
                "workspace": ws_ref,
            }
            response = self.session.get(
                f"{RALLY_BASE_URL}/task", params=params
            )
            result = response.json()
            items = result.get("QueryResult", {}).get("Results", [])
            return items[0] if items else None

        # Search all workspaces in parallel
        with ThreadPoolExecutor(max_workers=DEFAULT_MAX_WORKERS) as executor:
            futures = {executor.submit(search_workspace, ws): ws for ws in workspaces}

            for future in as_completed(futures):
                item = future.result()
                if item:
                    # Cancel remaining futures
                    for f in futures:
                        f.cancel()
                    # Update workspace/project context and persist for session continuity
                    if item.get("Workspace"):
                        self.workspace_ref = item["Workspace"].get("_ref")
                    if item.get("Project"):
                        project_ref = item["Project"].get("_ref")
                        project_name = item["Project"].get("_refObjectName")
                        if project_ref and project_name:
                            self.set_project(project_ref, project_name)
                    return item

        # Not found
        if raise_if_not_found:
            raise RallyItemNotFoundError(
                formatted_id=formatted_id,
                item_type="task",
                workspaces_searched=workspace_names,
                default_workspace=self.default_workspace_name,
            )

        return None

    def find_defect(
        self,
        formatted_id: str,
        raise_if_not_found: bool = False,
    ) -> Optional[dict]:
        """
        Find a defect by FormattedID, searching last_project first then all workspaces in parallel.

        Args:
            formatted_id: The FormattedID (e.g., 'DE12345')
            raise_if_not_found: If True, raise RallyItemNotFoundError instead of returning None

        Returns:
            Defect dict or None if not found

        Raises:
            RallyItemNotFoundError: If defect not found and raise_if_not_found=True
        """
        # First, try searching in the last_project if available (for faster, more accurate results)
        if self.project_ref:
            params = {
                "query": f'(FormattedID = "{formatted_id}")',
                "fetch": "Name,FormattedID,ObjectID,_ref,State,Owner,Priority,Severity,Project,Workspace",
                "project": self.project_ref,
            }
            response = self.session.get(
                f"{RALLY_BASE_URL}/defect", params=params
            )
            result = response.json()
            items = result.get("QueryResult", {}).get("Results", [])
            if items:
                item = items[0]
                # Update workspace context if needed
                if item.get("Workspace"):
                    self.workspace_ref = item["Workspace"].get("_ref")
                return item

        # Use ordered workspaces (default first)
        workspaces = self._get_workspaces_ordered()
        workspace_names = [ws.get("Name", "Unknown") for ws in workspaces]

        def search_workspace(ws: dict) -> Optional[dict]:
            """Search a single workspace for the defect."""
            ws_ref = ws.get("_ref")
            params = {
                "query": f'(FormattedID = "{formatted_id}")',
                "fetch": "Name,FormattedID,ObjectID,_ref,State,Owner,Priority,Severity,Project,Workspace",
                "workspace": ws_ref,
            }
            response = self.session.get(
                f"{RALLY_BASE_URL}/defect", params=params
            )
            result = response.json()
            items = result.get("QueryResult", {}).get("Results", [])
            return items[0] if items else None

        # Search all workspaces in parallel
        with ThreadPoolExecutor(max_workers=DEFAULT_MAX_WORKERS) as executor:
            futures = {executor.submit(search_workspace, ws): ws for ws in workspaces}

            for future in as_completed(futures):
                item = future.result()
                if item:
                    # Cancel remaining futures
                    for f in futures:
                        f.cancel()
                    # Update workspace/project context and persist for session continuity
                    if item.get("Workspace"):
                        self.workspace_ref = item["Workspace"].get("_ref")
                    if item.get("Project"):
                        project_ref = item["Project"].get("_ref")
                        project_name = item["Project"].get("_refObjectName")
                        if project_ref and project_name:
                            self.set_project(project_ref, project_name)
                    return item

        # Not found
        if raise_if_not_found:
            raise RallyItemNotFoundError(
                formatted_id=formatted_id,
                item_type="defect",
                workspaces_searched=workspace_names,
                default_workspace=self.default_workspace_name,
            )

        return None

    def find_by_formatted_id(
        self,
        formatted_id: str,
        raise_if_not_found: bool = True,
    ) -> Optional[dict]:
        """
        Find any Rally item by FormattedID. Automatically detects item type from prefix.

        Supported prefixes:
        - US: User Story
        - TA: Task
        - DE: Defect
        - F: Feature
        - C: Capability
        - E: Epic
        - I: Initiative
        - T: Theme (or Task if TA prefix)

        Args:
            formatted_id: The FormattedID (e.g., 'US12345', 'F116901', 'TA98765')
            raise_if_not_found: If True, raise RallyItemNotFoundError if not found

        Returns:
            Item dict or None if not found

        Raises:
            RallyItemNotFoundError: If item not found and raise_if_not_found=True
            ValueError: If prefix is not recognized
        """
        formatted_id = formatted_id.upper().strip()

        # Detect type from prefix
        if formatted_id.startswith("US"):
            return self.find_user_story(formatted_id, raise_if_not_found)
        elif formatted_id.startswith("TA"):
            return self.find_task(formatted_id, raise_if_not_found)
        elif formatted_id.startswith("DE"):
            return self.find_defect(formatted_id, raise_if_not_found)
        elif formatted_id.startswith("F"):
            return self.find_portfolio_item(formatted_id, "feature", raise_if_not_found)
        elif formatted_id.startswith("C"):
            return self.find_portfolio_item(formatted_id, "capability", raise_if_not_found)
        elif formatted_id.startswith("E"):
            return self.find_portfolio_item(formatted_id, "epic", raise_if_not_found)
        elif formatted_id.startswith("I"):
            return self.find_portfolio_item(formatted_id, "initiative", raise_if_not_found)
        elif formatted_id.startswith("T"):
            # Could be Theme (T) or Task (TA) - TA is already handled above
            return self.find_portfolio_item(formatted_id, "theme", raise_if_not_found)
        else:
            raise ValueError(
                f"Unrecognized Rally ID prefix: '{formatted_id}'\n"
                f"Expected prefixes: US (User Story), TA (Task), DE (Defect), "
                f"F (Feature), C (Capability), E (Epic), I (Initiative), T (Theme)"
            )

    def get_iterations(
        self,
        query: Optional[str] = None,
        order: str = "StartDate",
        pagesize: int = 10,
    ) -> list[dict]:
        """
        Get iterations (sprints).

        Args:
            query: Rally query string
            order: Sort order
            pagesize: Number of results

        Returns:
            List of iteration dicts
        """
        return self.query(
            "iteration",
            query=query,
            fetch="Name,StartDate,EndDate,ObjectID,_ref,Project",
            order=order,
            pagesize=pagesize,
        )

    def find_iteration(
        self,
        name: str,
        raise_if_not_found: bool = False,
    ) -> Optional[dict]:
        """
        Find an iteration by name, searching last_project first then all workspaces in parallel.

        If a project context exists (from previous queries or last session),
        this will prefer iterations from that project. This provides session
        continuity so you don't have to specify the project each time.

        Args:
            name: The iteration name (e.g., '2025.PI5.Iteration5')
            raise_if_not_found: If True, raise RallyItemNotFoundError when not found

        Returns:
            Iteration dict with Name, StartDate, EndDate, ObjectID, _ref, Project, Workspace
            or None if not found

        Raises:
            RallyItemNotFoundError: If iteration not found and raise_if_not_found=True
        """
        # First, try searching in the last_project if available (for faster, more accurate results)
        if self.project_ref:
            params = {
                "query": f'(Name = "{name}")',
                "fetch": "Name,StartDate,EndDate,ObjectID,_ref,Project,Workspace",
                "project": self.project_ref,
                "projectScopeDown": "true",
                "projectScopeUp": "false",
            }
            response = self.session.get(
                f"{RALLY_BASE_URL}/iteration", params=params
            )
            result = response.json()
            items = result.get("QueryResult", {}).get("Results", [])
            if items:
                item = items[0]
                # Update workspace context if needed
                if item.get("Workspace"):
                    self.workspace_ref = item["Workspace"].get("_ref")
                return item

        # Use ordered workspaces (default first)
        workspaces = self._get_workspaces_ordered()
        workspace_names = [ws.get("Name", "Unknown") for ws in workspaces]

        def search_workspace(ws: dict) -> list[dict]:
            """Search a single workspace for ALL iterations matching the name."""
            ws_ref = ws.get("_ref")
            params = {
                "query": f'(Name = "{name}")',
                "fetch": "Name,StartDate,EndDate,ObjectID,_ref,Project,Workspace",
                "workspace": ws_ref,
                "pagesize": 100,  # Get all matches
            }
            response = self.session.get(
                f"{RALLY_BASE_URL}/iteration", params=params
            )
            result = response.json()
            return result.get("QueryResult", {}).get("Results", [])

        # Search all workspaces in parallel and collect ALL matches
        all_matches = []
        with ThreadPoolExecutor(max_workers=DEFAULT_MAX_WORKERS) as executor:
            futures = {executor.submit(search_workspace, ws): ws for ws in workspaces}

            for future in as_completed(futures):
                items = future.result()
                all_matches.extend(items)

        if not all_matches:
            # Not found
            if raise_if_not_found:
                raise RallyItemNotFoundError(
                    formatted_id=name,
                    item_type="iteration",
                    workspaces_searched=workspace_names,
                    default_workspace=self.default_workspace_name,
                )
            return None

        # If we have a project context (from last session or previous queries),
        # prefer iterations from that project
        if self.project_ref:
            for item in all_matches:
                if item.get("Project", {}).get("_ref") == self.project_ref:
                    # Found match in the context project
                    if item.get("Workspace"):
                        self.workspace_ref = item["Workspace"].get("_ref")
                    # Already have project_ref set, just save it to persist
                    if item.get("Project"):
                        project_name = item["Project"].get("_refObjectName")
                        if project_name:
                            self.set_project(self.project_ref, project_name)
                    return item

        # No project context or no match in context project
        # Return first match and establish new project context
        item = all_matches[0]
        if item.get("Workspace"):
            self.workspace_ref = item["Workspace"].get("_ref")
        if item.get("Project"):
            project_ref = item["Project"].get("_ref")
            project_name = item["Project"].get("_refObjectName")
            if project_ref and project_name:
                self.set_project(project_ref, project_name)

        return item

    def find_all_iterations_with_context(
        self,
        name: str,
    ) -> list[dict]:
        """
        Find ALL iterations with the given name across all workspaces and enrich with context.

        This method is useful for disambiguation - when multiple iterations exist with the
        same name across different projects, this returns all of them with additional context
        like project name, story count, etc. so the caller can ask the user to choose.

        Args:
            name: The iteration name (e.g., '2025.PI5.Iteration5')

        Returns:
            List of iteration dicts, each enriched with:
            - All standard iteration fields (Name, StartDate, EndDate, ObjectID, _ref, Project)
            - 'story_count': Number of stories in this iteration
            - 'project_name': Human-readable project name
        """
        # Search all workspaces in parallel
        workspaces = self._get_workspaces_ordered()

        def search_workspace(ws: dict) -> list[dict]:
            """Search a single workspace for ALL iterations matching the name."""
            ws_ref = ws.get("_ref")
            params = {
                "query": f'(Name = "{name}")',
                "fetch": "Name,StartDate,EndDate,ObjectID,_ref,Project,Workspace",
                "workspace": ws_ref,
                "pagesize": 100,
            }
            response = self.session.get(
                f"{RALLY_BASE_URL}/iteration", params=params
            )
            result = response.json()
            return result.get("QueryResult", {}).get("Results", [])

        # Collect all matches
        all_matches = []
        with ThreadPoolExecutor(max_workers=DEFAULT_MAX_WORKERS) as executor:
            futures = {executor.submit(search_workspace, ws): ws for ws in workspaces}
            for future in as_completed(futures):
                items = future.result()
                all_matches.extend(items)

        # Deduplicate by ObjectID
        unique_iterations = {}
        for item in all_matches:
            oid = item.get("ObjectID")
            if oid and oid not in unique_iterations:
                unique_iterations[oid] = item

        # Enrich each iteration with context
        enriched = []
        for iteration in unique_iterations.values():
            # Add project name
            iteration["project_name"] = iteration.get("Project", {}).get("_refObjectName", "Unknown")

            # Count stories in this iteration (using project scope)
            project_ref = iteration.get("Project", {}).get("_ref")
            story_params = {
                "query": f'(Iteration.ObjectID = "{iteration["ObjectID"]}")',
                "pagesize": 1,
            }
            if project_ref:
                story_params["project"] = project_ref
                story_params["projectScopeDown"] = "true"
                story_params["projectScopeUp"] = "false"

            story_response = self.session.get(
                f"{RALLY_BASE_URL}/hierarchicalrequirement",
                params=story_params
            )
            story_result = story_response.json()
            iteration["story_count"] = story_result.get("QueryResult", {}).get("TotalResultCount", 0)

            enriched.append(iteration)

        # Sort by story count (descending) so most likely choice is first
        enriched.sort(key=lambda x: x.get("story_count", 0), reverse=True)

        return enriched

    def find_user(
        self,
        username: str,
        raise_if_not_found: bool = False,
    ) -> Optional[dict]:
        """
        Find a Rally user by username.

        Args:
            username: The Rally username (e.g., 'john.doe' or 'john.doe@company.com')
            raise_if_not_found: If True, raise RallyItemNotFoundError when not found

        Returns:
            User dict with UserName, DisplayName, _ref, etc. or None if not found

        Raises:
            RallyItemNotFoundError: If user not found and raise_if_not_found=True
        """
        # Try exact match first
        users = self.query(
            "user",
            query=f'(UserName = "{username}")',
            fetch="UserName,DisplayName,EmailAddress,_ref",
            pagesize=10
        )

        if users:
            return users[0]

        # Try with @domain if not provided
        if "@" not in username:
            users = self.query(
                "user",
                query=f'(UserName contains "{username}")',
                fetch="UserName,DisplayName,EmailAddress,_ref",
                pagesize=10
            )
            if users:
                return users[0]

        if raise_if_not_found:
            raise ValueError(f"User '{username}' not found")

        return None

    def find_release(
        self,
        name: str,
        raise_if_not_found: bool = False,
    ) -> Optional[dict]:
        """
        Find a Release by name.

        Args:
            name: The release name (e.g., '2026.PI1')
            raise_if_not_found: If True, raise RallyItemNotFoundError when not found

        Returns:
            Release dict with Name, ReleaseStartDate, ReleaseDate, ObjectID, _ref
            or None if not found

        Raises:
            RallyItemNotFoundError: If release not found and raise_if_not_found=True
        """
        releases = self.query(
            "release",
            query=f'(Name = "{name}")',
            fetch="Name,ReleaseStartDate,ReleaseDate,ObjectID,_ref,Project",
            pagesize=10
        )

        if releases:
            return releases[0]

        if raise_if_not_found:
            raise ValueError(f"Release '{name}' not found")

        return None

    def find_release_for_iteration(
        self,
        iteration: dict,
        raise_if_not_found: bool = False,
    ) -> Optional[dict]:
        """
        Find the Release that contains the given iteration's date range.

        An iteration belongs to a release if the iteration's StartDate and EndDate
        fall within the release's ReleaseStartDate and ReleaseDate.

        Args:
            iteration: Iteration dict with StartDate and EndDate
            raise_if_not_found: If True, raise ValueError when no matching release found

        Returns:
            Release dict with Name, ReleaseStartDate, ReleaseDate, ObjectID, _ref
            or None if not found

        Raises:
            ValueError: If no matching release found and raise_if_not_found=True
        """
        from datetime import datetime

        # Extract iteration dates
        iteration_start = iteration.get("StartDate")
        iteration_end = iteration.get("EndDate")

        if not iteration_start or not iteration_end:
            if raise_if_not_found:
                raise ValueError(f"Iteration missing StartDate or EndDate: {iteration.get('Name', 'Unknown')}")
            return None

        # Parse iteration dates
        iter_start = datetime.fromisoformat(iteration_start.replace("Z", "+00:00"))
        iter_end = datetime.fromisoformat(iteration_end.replace("Z", "+00:00"))

        # Query all releases (we need to check dates)
        releases = self.query(
            "release",
            query=None,  # Get all releases
            fetch="Name,ReleaseStartDate,ReleaseDate,ObjectID,_ref,Project",
            pagesize=200  # Assume not more than 200 releases
        )

        # Find release that contains this iteration
        for release in releases:
            release_start = release.get("ReleaseStartDate")
            release_end = release.get("ReleaseDate")

            if not release_start or not release_end:
                continue

            # Parse release dates
            rel_start = datetime.fromisoformat(release_start.replace("Z", "+00:00"))
            rel_end = datetime.fromisoformat(release_end.replace("Z", "+00:00"))

            # Check if iteration falls within release date range
            if rel_start <= iter_start and iter_end <= rel_end:
                return release

        if raise_if_not_found:
            raise ValueError(
                f"No release found for iteration '{iteration.get('Name', 'Unknown')}' "
                f"({iteration_start} to {iteration_end})"
            )

        return None

    def get_user_stories(
        self,
        query: Optional[str] = None,
        fetch: str = "Name,FormattedID,ScheduleState,Owner,PlanEstimate,PortfolioItem,Feature,Tasks,ObjectID,_ref,Blocked,Attachments",
        include_project_scope: bool = False,
    ) -> list[dict]:
        """
        Get user stories (hierarchical requirements).

        Args:
            query: Rally query string
            fetch: Fields to fetch
            include_project_scope: Whether to restrict to current project (default: False to search workspace-wide)

        Returns:
            List of user story dicts
        """
        return self.query("hierarchicalrequirement", query=query, fetch=fetch, include_project_scope=include_project_scope)

    def get_tasks(
        self,
        query: Optional[str] = None,
        fetch: str = "Name,State,Owner,Estimate,ToDo,FormattedID,ObjectID,Blocked,Attachments",
    ) -> list[dict]:
        """
        Get tasks.

        Args:
            query: Rally query string
            fetch: Fields to fetch

        Returns:
            List of task dicts
        """
        return self.query("task", query=query, fetch=fetch, pagesize=100)

    def get_themes(
        self,
        query: Optional[str] = None,
        fetch: str = "Name,FormattedID,ObjectID,Owner,State,Parent,Children,Attachments",
    ) -> list[dict]:
        """
        Get themes (top-level portfolio items).

        Args:
            query: Rally query string
            fetch: Fields to fetch

        Returns:
            List of theme dicts
        """
        return self.query("portfolioitem/theme", query=query, fetch=fetch)

    def get_initiatives(
        self,
        query: Optional[str] = None,
        fetch: str = "Name,FormattedID,ObjectID,Owner,State,Parent,Children,Attachments",
    ) -> list[dict]:
        """
        Get initiatives (portfolio items under themes).

        Args:
            query: Rally query string
            fetch: Fields to fetch

        Returns:
            List of initiative dicts
        """
        return self.query("portfolioitem/initiative", query=query, fetch=fetch)

    def get_epics(
        self,
        query: Optional[str] = None,
        fetch: str = "Name,FormattedID,ObjectID,Owner,State,Parent,Children,Attachments",
    ) -> list[dict]:
        """
        Get epics (portfolio items under initiatives).

        Args:
            query: Rally query string
            fetch: Fields to fetch

        Returns:
            List of epic dicts
        """
        return self.query("portfolioitem/epic", query=query, fetch=fetch)

    def get_capabilities(
        self,
        query: Optional[str] = None,
        fetch: str = "Name,FormattedID,ObjectID,Owner,State,Parent,Children,Attachments",
    ) -> list[dict]:
        """
        Get capabilities (portfolio items under epics).

        Args:
            query: Rally query string
            fetch: Fields to fetch

        Returns:
            List of capability dicts
        """
        return self.query("portfolioitem/capability", query=query, fetch=fetch)

    def get_features(
        self,
        query: Optional[str] = None,
        fetch: str = "Name,FormattedID,ObjectID,Owner,PlannedEndDate,State,PercentDoneByStoryCount,Parent,Attachments",
    ) -> list[dict]:
        """
        Get features (portfolio items under capabilities).

        Args:
            query: Rally query string
            fetch: Fields to fetch

        Returns:
            List of feature dicts
        """
        return self.query("portfolioitem/feature", query=query, fetch=fetch)

    def get_defects(
        self,
        query: Optional[str] = None,
        fetch: str = "Name,FormattedID,State,Owner,Priority,Severity,Requirement,ObjectID,_ref,Attachments",
    ) -> list[dict]:
        """
        Get defects.

        Args:
            query: Rally query string
            fetch: Fields to fetch

        Returns:
            List of defect dicts
        """
        return self.query("defect", query=query, fetch=fetch)

    def post(
        self,
        endpoint: str,
        data: dict,
        include_scope: bool = True,
    ) -> dict:
        """
        Make a POST request to the Rally API.

        Args:
            endpoint: API endpoint (e.g., 'portfolioitem/feature')
            data: Data to post (will be wrapped in appropriate structure)
            include_scope: Whether to include workspace/project scope

        Returns:
            JSON response as dict
        """
        if endpoint.startswith("http"):
            url = endpoint
        else:
            url = f"{RALLY_BASE_URL}/{endpoint}"

        params = {}
        if include_scope:
            if self.workspace_ref:
                params["workspace"] = self.workspace_ref
            if self.project_ref:
                params["project"] = self.project_ref

        response = self.session.post(url, json=data, params=params)
        response.raise_for_status()
        return response.json()

    def create_object(
        self,
        object_type: str,
        fields: dict[str, Any],
    ) -> dict:
        """
        Create a new Rally object.

        Args:
            object_type: Rally object type (e.g., 'portfolioitem/feature')
            fields: Field values for the new object

        Returns:
            Created object data
        """
        # Determine the wrapper key based on object type
        if "/" in object_type:
            wrapper_key = object_type.split("/")[-1].title()
        else:
            wrapper_key = object_type.title()

        # Map common type names - all portfolio items use PortfolioItem wrapper
        type_mapping = {
            "Theme": "PortfolioItem",
            "Initiative": "PortfolioItem",
            "Epic": "PortfolioItem",
            "Capability": "PortfolioItem",
            "Feature": "PortfolioItem",
            "Hierarchicalrequirement": "HierarchicalRequirement",
        }
        wrapper_key = type_mapping.get(wrapper_key, wrapper_key)

        data = {wrapper_key: fields}
        result = self.post(f"{object_type}/create", data)

        # Extract the created object from response
        create_result = result.get("CreateResult", {})
        if create_result.get("Errors"):
            # Extract friendly object type name for error message
            obj_type_name = object_type.split("/")[-1].title() if "/" in object_type else object_type.title()
            raise RallyValidationError("create", create_result["Errors"], obj_type_name)

        return create_result.get("Object", {})

    def create_feature(
        self,
        name: str,
        parent_ref: Optional[str] = None,
        description: str = "",
        state: str = "Funnel",
    ) -> dict:
        """
        Create a new feature.

        Args:
            name: Feature name
            parent_ref: Parent capability reference (_ref)
            description: Feature description
            state: Feature state (default: "Funnel")

        Returns:
            Created feature data
        """
        fields: dict[str, Any] = {
            "Name": name,
            "State": self._resolve_state_ref("Feature", state),
        }
        if parent_ref:
            fields["Parent"] = parent_ref
        if description:
            fields["Description"] = description
        if self.project_ref:
            fields["Project"] = self.project_ref

        return self.create_object("portfolioitem/feature", fields)

    def _render_story_description(
        self,
        role: str,
        want: str,
        benefit: str,
        acceptance_criteria: list[str]
    ) -> str:
        """
        Render story description using the template system.

        Args:
            role: User role (e.g., "frontend developer")
            want: What the user wants
            benefit: The benefit/so that clause (can be empty)
            acceptance_criteria: List of acceptance criteria strings

        Returns:
            Rendered HTML description using the story template
        """
        # Render acceptance criteria as HTML list items
        criteria_html = "\n".join([f"<li>{c}</li>" for c in acceptance_criteria])

        # Prepare template variables
        variables = {
            "role": role,
            "want": want,
            "benefit": benefit,
            "acceptance_criteria_html": criteria_html,
        }

        # Render template - this will use project-level template if available,
        # otherwise falls back to default template
        description = self._template_loader.render_template("story", variables)

        if description:
            # If benefit is empty, remove the "So that" line
            if not benefit:
                import re
                description = re.sub(
                    r",<br/>\s*<strong>So that</strong>.*?</p>",
                    ".</p>",
                    description,
                    flags=re.DOTALL
                )
            return description
        else:
            # Fallback to hardcoded format if template not found (should rarely happen)
            benefit_part = f",<br/>\n<strong>So that</strong> {benefit}" if benefit else ""
            return f"""<p><strong>As a</strong> {role},<br/>
<strong>I want</strong> {want}{benefit_part}.</p>

<h3>Acceptance Criteria</h3>
<ul>
{criteria_html}
</ul>"""

    def _render_task_description(
        self,
        goal: str,
        details: str
    ) -> str:
        """
        Render task description using the template system.

        Args:
            goal: What this task aims to achieve
            details: Specific work to be done, technical details

        Returns:
            Rendered HTML description using the task template
        """
        variables = {
            "goal": goal,
            "details": details,
        }

        description = self._template_loader.render_template("task", variables)

        if description:
            return description
        else:
            # Fallback to hardcoded format if template not found
            return f"""<h3>Goal</h3>
<p>{goal}</p>

<h3>Details</h3>
<p>{details}</p>"""

    def _render_defect_description(
        self,
        summary: str,
        steps_to_reproduce: list[str],
        expected: str,
        actual: str,
        environment: dict[str, str]
    ) -> str:
        """
        Render defect description using the template system.

        Args:
            summary: One-line summary of the defect
            steps_to_reproduce: List of steps to reproduce the bug
            expected: Expected behavior
            actual: Actual behavior
            environment: Dict with browser, os, version keys

        Returns:
            Rendered HTML description using the defect template
        """
        # Render steps as ordered list items
        steps_html = "\n".join([f"<li>{step}</li>" for step in steps_to_reproduce])

        # Render environment as list items
        env_html = "\n".join([
            f"<li><strong>{key.title()}:</strong> {value}</li>"
            for key, value in environment.items()
        ])

        variables = {
            "summary": summary,
            "steps_to_reproduce_html": steps_html,
            "expected": expected,
            "actual": actual,
            "environment_html": env_html,
        }

        description = self._template_loader.render_template("defect", variables)

        if description:
            return description
        else:
            # Fallback to hardcoded format if template not found
            return f"""<h3>Summary</h3>
<p>{summary}</p>

<h3>Steps to Reproduce</h3>
<ol>
{steps_html}
</ol>

<h3>Expected Behavior</h3>
<p>{expected}</p>

<h3>Actual Behavior</h3>
<p>{actual}</p>

<h3>Environment</h3>
<ul>
{env_html}
</ul>"""

    def validate_hierarchy(self, feature_ref: str) -> dict:
        """
        Validate that a feature has complete parent hierarchy (Feature → Capability → Initiative).

        Args:
            feature_ref: Feature reference to validate

        Returns:
            Dict with hierarchy information:
            {
                "feature": {...},
                "capability": {...},
                "initiative": {...},
                "complete": bool
            }

        Raises:
            ValueError: If hierarchy is incomplete
        """
        # Fetch feature with parent info
        feature = self.get_object(feature_ref, fetch="FormattedID,Name,Parent,_ref")

        result = {
            "feature": {
                "id": feature.get("FormattedID"),
                "name": feature.get("Name"),
                "ref": feature["_ref"]
            },
            "capability": None,
            "initiative": None,
            "complete": False
        }

        # Check if feature has parent (Capability)
        if not feature.get("Parent"):
            raise ValueError(
                f"Feature {feature.get('FormattedID')} has no parent Capability. "
                f"Features must be linked to a Capability, which must be linked to an Initiative."
            )

        # Fetch capability
        capability = self.get_object(feature["Parent"]["_ref"], fetch="FormattedID,Name,Parent,_ref")
        result["capability"] = {
            "id": capability.get("FormattedID"),
            "name": capability.get("Name"),
            "ref": capability["_ref"]
        }

        # Check if capability has parent (Initiative)
        if not capability.get("Parent"):
            raise ValueError(
                f"Capability {capability.get('FormattedID')} has no parent Initiative. "
                f"The hierarchy must be: Story → Feature → Capability → Initiative."
            )

        # Fetch initiative
        initiative = self.get_object(capability["Parent"]["_ref"], fetch="FormattedID,Name,_ref")
        result["initiative"] = {
            "id": initiative.get("FormattedID"),
            "name": initiative.get("Name"),
            "ref": initiative["_ref"]
        }

        result["complete"] = True
        return result

    def create_user_story(
        self,
        feature_ref: str,
        name: str,
        role: str,
        want: str,
        so_that: str,
        acceptance_criteria: list[str],
        schedule_state: str = "Defined",
        inherit_from_parent: bool = True,
        validate_hierarchy: bool = True,
    ) -> dict:
        """
        Create a new user story under a feature using the standard template.

        Args:
            feature_ref: Parent feature reference (_ref)
            name: Short descriptive name (3-8 words, e.g., "Run optimizer on existing plugins")
            role: User role (e.g., "plugin developer")
            want: What the user wants (e.g., "run the optimizer on plugins")
            so_that: The benefit (e.g., "I can identify performance issues")
            acceptance_criteria: List of acceptance criteria strings
            schedule_state: Schedule state (default: "Defined")
            inherit_from_parent: If True, inherit Owner and Release from parent feature
            validate_hierarchy: If True, validate complete parent hierarchy before creation

        Returns:
            Created user story data

        Raises:
            ValueError: If validate_hierarchy=True and hierarchy is incomplete

        Example:
            api.create_user_story(
                feature_ref=feature['_ref'],
                name='Run optimizer on existing plugins',
                role='plugin developer',
                want='run the performance optimizer on existing plugins',
                so_that='I can identify and fix performance bottlenecks',
                acceptance_criteria=[
                    'Run optimizer on all Tier 1 plugins',
                    'Document optimization recommendations',
                    'Measure and report improvements'
                ]
            )
        """
        # Validate complete hierarchy: Story → Feature → Capability → Initiative
        if validate_hierarchy:
            hierarchy = self.validate_hierarchy(feature_ref)
            # Log hierarchy for debugging (optional)
            print(f"✓ Validated hierarchy: Story → {hierarchy['feature']['id']} → "
                  f"{hierarchy['capability']['id']} → {hierarchy['initiative']['id']}")

        # Build description using template from templates/story.md
        description = self._render_story_description(role, want, so_that, acceptance_criteria)

        fields: dict[str, Any] = {
            "Name": name,
            "Description": description,
            "PortfolioItem": feature_ref,
            "ScheduleState": schedule_state,
        }
        if self.project_ref:
            fields["Project"] = self.project_ref

        # Inherit Owner and Release from parent feature
        if inherit_from_parent:
            feature = self.get_object(feature_ref, fetch="Owner,Release")
            if feature.get("Owner"):
                fields["Owner"] = feature["Owner"]["_ref"]
            if feature.get("Release"):
                fields["Release"] = feature["Release"]["_ref"]

        return self.create_object("HierarchicalRequirement", fields)

    def upload_attachment(
        self,
        artifact_ref: str,
        filename: str,
        content: bytes,
        content_type: str = "text/html",
        description: str = "",
    ) -> dict:
        """
        Upload an attachment to a Rally artifact.

        Args:
            artifact_ref: Reference to the artifact (_ref)
            filename: Name of the attachment file
            content: File content as bytes
            content_type: MIME type of the content
            description: Optional description

        Returns:
            Created attachment data
        """
        # First create the AttachmentContent
        encoded_content = base64.b64encode(content).decode("utf-8")
        content_data = {
            "AttachmentContent": {
                "Content": encoded_content,
            }
        }
        content_result = self.post("attachmentcontent/create", content_data)
        content_create = content_result.get("CreateResult", {})
        if content_create.get("Errors"):
            raise RallyValidationError("create", content_create["Errors"], "AttachmentContent")

        content_ref = content_create.get("Object", {}).get("_ref")

        # Then create the Attachment linking to the content
        attachment_data = {
            "Attachment": {
                "Name": filename,
                "Content": content_ref,
                "ContentType": content_type,
                "Size": len(content),
                "Artifact": artifact_ref,
            }
        }
        if description:
            attachment_data["Attachment"]["Description"] = description

        attachment_result = self.post("attachment/create", attachment_data)
        attachment_create = attachment_result.get("CreateResult", {})
        if attachment_create.get("Errors"):
            raise RallyValidationError("create", attachment_create["Errors"], "Attachment")

        return attachment_create.get("Object", {})

    def get_attachments(self, artifact_ref: str) -> list[dict]:
        """
        Get all attachments for a Rally artifact.

        Args:
            artifact_ref: Reference to the artifact (_ref)

        Returns:
            List of attachment dicts
        """
        return self.query(
            "attachment",
            query=f'(Artifact = "{artifact_ref}")',
            fetch="Name,ObjectID,_ref,Size,ContentType",
        )

    def delete_object(self, ref: str, backup: bool = True) -> None:
        """
        Delete a Rally object by its reference URL.

        Args:
            ref: Object reference URL (_ref)
            backup: Whether to backup the object before deleting (default: True)

        Raises:
            Exception: If backup is enabled and backup fails
        """
        if backup:
            backup_path = self._backup_object(ref)
            if not backup_path:
                raise Exception(f"Backup failed for {ref}, delete aborted")

        response = self.session.delete(ref)
        response.raise_for_status()

    def _backup_object(self, ref: str) -> Optional[str]:
        """
        Backup a Rally object to .backup folder before deletion.
        Includes all fields and attachments with their content.

        Args:
            ref: Object reference URL (_ref)

        Returns:
            Path to backup file if successful, None if failed
        """
        try:
            # Fetch full object data with all fields
            response = self.session.get(ref, params={"fetch": "true"})
            if response.status_code != 200:
                return None

            data = response.json()

            # Extract object type and ID from ref
            # e.g., ".../hierarchicalrequirement/770919" -> "hierarchicalrequirement", "770919"
            parts = ref.rstrip("/").split("/")
            obj_id = parts[-1] if parts else "unknown"
            obj_type = parts[-2] if len(parts) >= 2 else "object"

            # Try to get FormattedID and Name from the response
            formatted_id = None
            obj_name = None
            for _, value in data.items():
                if isinstance(value, dict):
                    if "FormattedID" in value:
                        formatted_id = value.get("FormattedID")
                    if "Name" in value:
                        obj_name = value.get("Name")

            # Fetch and include attachments
            attachments_data = []
            try:
                attachments = self.query(
                    "attachment",
                    query=f'(Artifact = "{ref}")',
                    fetch="Name,ObjectID,_ref,Size,ContentType,Description",
                )

                for att in attachments:
                    att_info = {
                        "Name": att.get("Name"),
                        "Size": att.get("Size"),
                        "ContentType": att.get("ContentType"),
                        "Description": att.get("Description"),
                        "_ref": att.get("_ref"),
                    }

                    # Get attachment content
                    try:
                        att_ref = att.get("_ref")
                        if not att_ref:
                            continue
                        att_details = self.get_object(
                            att_ref,
                            fetch="Content,Name,Size,ContentType"
                        )
                        content_ref = att_details.get("Content", {}).get("_ref")
                        if content_ref:
                            content_response = self.session.get(content_ref)
                            if content_response.status_code == 200:
                                content_data = content_response.json()
                                # Content is base64 encoded
                                att_info["Content"] = content_data.get(
                                    "AttachmentContent", {}
                                ).get("Content")
                    except Exception:
                        pass

                    attachments_data.append(att_info)
            except Exception:
                pass

            # Add attachments to backup data
            data["_backup_attachments"] = attachments_data
            data["_backup_metadata"] = {
                "backup_time": datetime.now().isoformat(),
                "original_ref": ref,
                "formatted_id": formatted_id,
                "name": obj_name,
                "attachment_count": len(attachments_data),
            }

            # Create backup directory in ~/.claude/.fe-sm/backups
            backup_dir = os.path.join(
                os.path.expanduser("~"), ".claude", ".fe-sm", "backups", obj_type
            )
            os.makedirs(backup_dir, exist_ok=True)

            # Create filename with timestamp
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            if formatted_id:
                filename = f"{formatted_id}_{timestamp}.json"
            else:
                filename = f"{obj_id}_{timestamp}.json"

            filepath = os.path.join(backup_dir, filename)

            # Write backup
            with open(filepath, "w") as f:
                json.dump(data, f, indent=2)

            return filepath

        except Exception:
            return None

    def restore_from_backup(
        self,
        backup_path: str,
        parent_ref: Optional[str] = None,
        restore_attachments: bool = True,
    ) -> dict:
        """
        Restore a Rally object from a backup file.

        Args:
            backup_path: Path to the backup JSON file
            parent_ref: Optional parent reference for portfolio items (Feature/PortfolioItem)
            restore_attachments: Whether to restore attachments (default: True)

        Returns:
            Created object data

        Raises:
            Exception: If restore fails
        """
        # Read backup file
        with open(backup_path, "r") as f:
            backup_data = json.load(f)

        # Get metadata
        metadata = backup_data.get("_backup_metadata", {})
        attachments = backup_data.get("_backup_attachments", [])

        # Determine object type from backup
        object_data = None
        object_type = None
        type_mapping = {
            "HierarchicalRequirement": "hierarchicalrequirement",
            "PortfolioItem": "portfolioitem/feature",  # Default to feature
            "Task": "task",
            "Defect": "defect",
            "Feature": "portfolioitem/feature",
            "Capability": "portfolioitem/capability",
            "Epic": "portfolioitem/epic",
            "Initiative": "portfolioitem/initiative",
            "Theme": "portfolioitem/theme",
        }

        for key in type_mapping:
            if key in backup_data:
                object_data = backup_data[key]
                object_type = type_mapping[key]
                break

        if not object_data or not object_type:
            raise Exception("Could not determine object type from backup")

        # Extract fields to restore (exclude system fields)
        excluded_fields = {
            "_ref", "_refObjectUUID", "_refObjectName", "_type",
            "ObjectID", "ObjectUUID", "CreationDate", "Subscription",
            "Workspace", "Project", "FormattedID", "DirectChildrenCount",
            "Attachments", "Tasks", "Children", "Changesets", "Milestones",
            "Predecessors", "Successors", "Defects", "TestCases",
            "RevisionHistory", "Discussion", "Tags", "Recycled",
            "_objectVersion", "_CreatedAt", "_ValidFrom", "_ValidTo",
        }

        fields_to_restore = {}
        for key, value in object_data.items():
            if key not in excluded_fields and not key.startswith("c_"):
                # Skip reference fields that are objects (except Parent/PortfolioItem)
                if isinstance(value, dict) and "_ref" in value:
                    if key in ["Parent", "PortfolioItem", "Feature", "WorkProduct"]:
                        # Use provided parent_ref or skip
                        if parent_ref and key in ["Parent", "PortfolioItem", "Feature"]:
                            fields_to_restore[key] = parent_ref
                        elif key == "WorkProduct" and parent_ref:
                            fields_to_restore[key] = parent_ref
                    continue
                elif value is not None:
                    fields_to_restore[key] = value

        # Override parent if provided
        if parent_ref:
            if object_type.startswith("portfolioitem"):
                fields_to_restore["Parent"] = parent_ref
            elif object_type == "hierarchicalrequirement":
                fields_to_restore["PortfolioItem"] = parent_ref
            elif object_type == "task":
                fields_to_restore["WorkProduct"] = parent_ref

        # Create the object
        print(f"Restoring {metadata.get('formatted_id', 'object')}: {metadata.get('name', 'Unknown')}")
        created_obj = self.create_object(object_type, fields_to_restore)
        created_ref = created_obj.get("_ref")
        created_id = created_obj.get("FormattedID", "?")

        print(f"  Created {created_id}")

        # Restore attachments
        if restore_attachments and attachments and created_ref:
            print(f"  Restoring {len(attachments)} attachments...")
            for att in attachments:
                att_name = att.get("Name", "Unknown")
                att_content = att.get("Content")
                att_type = att.get("ContentType", "application/octet-stream")

                if att_content:
                    try:
                        # Content is base64 encoded
                        content_bytes = base64.b64decode(att_content)
                        self.upload_attachment(
                            created_ref,
                            att_name,
                            content_bytes,
                            content_type=att_type,
                            description=att.get("Description", ""),
                        )
                        print(f"    Restored: {att_name}")
                    except Exception as e:
                        print(f"    Failed to restore {att_name}: {e}")
                else:
                    print(f"    Skipped {att_name} (no content)")

        return created_obj

    def prepare_iteration_and_release(
        self,
        iteration_name: Optional[str] = None,
        release_name: Optional[str] = None,
        auto_set_release: bool = True,
    ) -> dict[str, str]:
        """
        Prepare Iteration and Release fields for creating or updating work items.

        When iteration_name is provided and auto_set_release is True, this will
        automatically find and set the matching release based on date ranges.

        Args:
            iteration_name: Name of the iteration (e.g., "2026.PI1.Iteration1")
            release_name: Name of the release (e.g., "2026.PI1") - optional if auto_set_release=True
            auto_set_release: If True, automatically find release matching the iteration's dates

        Returns:
            Dict with "Iteration" and/or "Release" keys containing _ref values

        Example:
            # Automatically set release based on iteration dates
            fields = api.prepare_iteration_and_release(iteration_name="2026.PI1.Iteration1")
            # Returns: {"Iteration": "...", "Release": "..."}

            # Manually specify both
            fields = api.prepare_iteration_and_release(
                iteration_name="2026.PI1.Iteration1",
                release_name="2026.PI1",
                auto_set_release=False
            )
        """
        fields: dict[str, str] = {}

        # Handle iteration
        iteration = None
        if iteration_name:
            iteration = self.find_iteration(iteration_name, raise_if_not_found=True)
            fields["Iteration"] = iteration["_ref"]

        # Handle release
        if release_name:
            # Explicit release provided
            release = self.find_release(release_name, raise_if_not_found=True)
            fields["Release"] = release["_ref"]
        elif auto_set_release and iteration:
            # Auto-find release matching iteration dates
            release = self.find_release_for_iteration(iteration, raise_if_not_found=False)
            if release:
                fields["Release"] = release["_ref"]

        return fields

    def update_object(self, ref: str, fields: dict[str, Any], backup: bool = True) -> dict:
        """
        Update a Rally object.

        Args:
            ref: Object reference URL (_ref)
            fields: Field values to update
            backup: Whether to backup the object before updating (default: True)

        Returns:
            Updated object data

        Raises:
            Exception: If backup is enabled and backup fails
        """
        # Backup before update if enabled
        if backup:
            try:
                # Fetch current object state
                current_obj = self.get_object(ref, fetch="true")

                # Initialize backup utility if needed
                if self._backup_util is None:
                    self._backup_util = RallyBackup()

                # Determine object type and ID for context
                parts = ref.rstrip("/").split("/")
                obj_type = parts[-2] if len(parts) >= 2 else "object"
                obj_id = current_obj.get("FormattedID", parts[-1] if parts else "unknown")

                context = {
                    "object_type": obj_type,
                    "object_id": obj_id,
                    "object_name": current_obj.get("Name", ""),
                    "operation": "UPDATE",
                    "fields_to_update": list(fields.keys())
                }

                # Create backup
                self._backup_util.create_backup("UPDATE", [current_obj], context)

            except Exception as e:
                raise Exception(f"Backup failed for {ref}: {str(e)}. Update aborted.")

        # Determine wrapper key from ref URL
        # e.g., ".../hierarchicalrequirement/12345" -> "HierarchicalRequirement"
        parts = ref.rstrip("/").split("/")
        type_part = parts[-2] if len(parts) >= 2 else "Object"

        wrapper_mapping = {
            "hierarchicalrequirement": "HierarchicalRequirement",
            "portfolioitem": "PortfolioItem",
            "task": "Task",
            "defect": "Defect",
        }
        wrapper_key = wrapper_mapping.get(type_part.lower(), type_part.title())

        data = {wrapper_key: fields}

        params = {}
        if self.workspace_ref:
            params["workspace"] = self.workspace_ref

        response = self.session.post(ref, json=data, params=params)
        response.raise_for_status()
        result = response.json()

        # Extract from OperationResult
        op_result = result.get("OperationResult", {})
        if op_result.get("Errors"):
            raise RallyValidationError("update", op_result["Errors"], wrapper_key)

        return op_result.get("Object", {})

    def create_capability(
        self,
        name: str,
        parent_ref: str,
        description: str = "",
        state: str = "Funnel",
    ) -> dict:
        """
        Create a new capability.

        Args:
            name: Capability name
            parent_ref: Parent epic reference (_ref)
            description: Capability description
            state: Capability state

        Returns:
            Created capability data
        """
        fields: dict[str, Any] = {
            "Name": name,
            "State": self._resolve_state_ref("Capability", state),
            "Parent": parent_ref,
        }
        if description:
            fields["Description"] = description
        if self.project_ref:
            fields["Project"] = self.project_ref

        return self.create_object("portfolioitem/capability", fields)

    def create_epic(
        self,
        name: str,
        parent_ref: str,
        description: str = "",
        state: str = "Funnel",
    ) -> dict:
        """
        Create a new epic.

        Args:
            name: Epic name
            parent_ref: Parent initiative reference (_ref)
            description: Epic description
            state: Epic state

        Returns:
            Created epic data
        """
        fields: dict[str, Any] = {
            "Name": name,
            "State": self._resolve_state_ref("Epic", state),
            "Parent": parent_ref,
        }
        if description:
            fields["Description"] = description
        if self.project_ref:
            fields["Project"] = self.project_ref

        return self.create_object("portfolioitem/epic", fields)

    def create_initiative(
        self,
        name: str,
        parent_ref: str,
        description: str = "",
        state: str = "Discovering",
    ) -> dict:
        """
        Create a new initiative.

        Args:
            name: Initiative name
            parent_ref: Parent theme reference (_ref)
            description: Initiative description
            state: Initiative state (default: "Discovering"). Valid values: Discovering, Developing,
                   Measuring, Done

        Returns:
            Created initiative data
        """
        fields: dict[str, Any] = {
            "Name": name,
            "State": self._resolve_state_ref("Initiative", state),
            "Parent": parent_ref,
        }
        if description:
            fields["Description"] = description
        if self.project_ref:
            fields["Project"] = self.project_ref

        return self.create_object("portfolioitem/initiative", fields)

    def create_theme(
        self,
        name: str,
        description: str = "",
        state: str = "Funnel",
    ) -> dict:
        """
        Create a new theme.

        Args:
            name: Theme name
            description: Theme description
            state: Theme state (default: "Funnel")

        Returns:
            Created theme data
        """
        fields: dict[str, Any] = {
            "Name": name,
            "State": self._resolve_state_ref("Theme", state),
        }
        if description:
            fields["Description"] = description
        if self.project_ref:
            fields["Project"] = self.project_ref

        return self.create_object("portfolioitem/theme", fields)

    def _resolve_state_ref(self, type_name: str, state: str) -> str:
        """
        Resolve a portfolio item state name to its Rally _ref URL.

        Rally requires the State field to be a reference URL, not a plain string name.
        If `state` already looks like a URL it is returned unchanged.

        Args:
            type_name: Portfolio item type (e.g. "Initiative", "Epic", "Capability",
                       "Feature", "Theme")
            state:     State name (e.g. "Funnel", "Discovering") or existing _ref URL

        Returns:
            The _ref URL for the state, or the original string if resolution fails.
        """
        if state.startswith("http"):
            return state

        # Populate cache for this type on first call
        if type_name not in self._state_ref_cache:
            try:
                results = self.query(
                    "PortfolioItemState",
                    f'(TypeDef.Name = "{type_name}")',
                    fetch="_ref,Name",
                    include_project_scope=False,
                )
                self._state_ref_cache[type_name] = {
                    r["Name"]: r["_ref"] for r in results if "Name" in r and "_ref" in r
                }
            except Exception:
                self._state_ref_cache[type_name] = {}

        ref = self._state_ref_cache[type_name].get(state)
        return ref if ref else state

    def validate_before_create(
        self,
        object_type: str,
        name: str,
        parent_ref: Optional[str] = None,
    ) -> dict:
        """
        Validate before creating a Rally object. Checks for duplicates and parent validity.

        Args:
            object_type: Rally object type (e.g., 'hierarchicalrequirement', 'portfolioitem/feature')
            name: Name of the item to create
            parent_ref: Parent reference to check (optional)

        Returns:
            Dict with validation results:
            - has_duplicate: bool - True if item with same name exists
            - duplicates: list - Existing items with same/similar name
            - parent_valid: bool - True if parent exists and is accessible
            - parent_info: dict - Parent item info if valid
            - warnings: list - Any warnings to consider
        """
        result = {
            "has_duplicate": False,
            "duplicates": [],
            "parent_valid": True,
            "parent_info": None,
            "warnings": [],
        }

        # Check for duplicates by name
        try:
            # Build query based on object type
            if object_type == "hierarchicalrequirement" and parent_ref:
                # Check within the same feature
                existing = self.query(
                    object_type,
                    query=f'((Name = "{name}") AND (PortfolioItem = "{parent_ref}"))',
                    fetch="Name,FormattedID,ScheduleState",
                )
            elif object_type.startswith("portfolioitem") and parent_ref:
                # Check within same parent
                existing = self.query(
                    object_type,
                    query=f'((Name = "{name}") AND (Parent = "{parent_ref}"))',
                    fetch="Name,FormattedID,State",
                )
            else:
                # General name check
                existing = self.query(
                    object_type,
                    query=f'(Name = "{name}")',
                    fetch="Name,FormattedID",
                    pagesize=5,
                )

            if existing:
                result["has_duplicate"] = True
                result["duplicates"] = existing
                result["warnings"].append(
                    f"Found {len(existing)} item(s) with the same name"
                )

            # Also check for similar names (contains)
            similar = self.query(
                object_type,
                query=f'(Name contains "{name}")',
                fetch="Name,FormattedID",
                pagesize=10,
            )
            # Filter out exact matches
            similar = [s for s in similar if s["Name"] != name]
            if similar:
                result["warnings"].append(
                    f"Found {len(similar)} item(s) with similar names"
                )

        except Exception as e:
            result["warnings"].append(f"Could not check for duplicates: {e}")

        # Validate parent if provided
        if parent_ref:
            try:
                parent = self.get_object(parent_ref, fetch="Name,FormattedID,State")
                result["parent_valid"] = True
                result["parent_info"] = parent
            except Exception:
                result["parent_valid"] = False
                result["warnings"].append("Parent reference is invalid or inaccessible")

        return result

    def search_by_name(
        self,
        object_type: str,
        name: str,
        exact: bool = False,
        fetch: str = "Name,FormattedID,ObjectID,_ref",
    ) -> list[dict]:
        """
        Search for Rally items by name.

        Args:
            object_type: Rally object type (e.g., 'hierarchicalrequirement', 'portfolioitem/feature')
            name: Name to search for
            exact: If True, match exact name; if False, use contains
            fetch: Fields to fetch

        Returns:
            List of matching items
        """
        if exact:
            query = f'(Name = "{name}")'
        else:
            query = f'(Name contains "{name}")'

        return self.query(object_type, query=query, fetch=fetch)

    def get_children(
        self,
        parent_ref: str,
        child_type: Optional[str] = None,
        fetch: str = "Name,FormattedID,ObjectID,_ref,State,ScheduleState",
    ) -> list[dict]:
        """
        Get all children of a portfolio item or feature.

        Args:
            parent_ref: Parent reference (_ref)
            child_type: Optional child type filter. If None, auto-detects based on parent type.
            fetch: Fields to fetch

        Returns:
            List of child items
        """
        # Determine parent type from ref
        parent_type = None
        for t in ["theme", "initiative", "epic", "capability", "feature"]:
            if t in parent_ref.lower():
                parent_type = t
                break

        # Map parent type to child type
        child_type_map = {
            "theme": "portfolioitem/initiative",
            "initiative": "portfolioitem/epic",
            "epic": "portfolioitem/capability",
            "capability": "portfolioitem/feature",
            "feature": "hierarchicalrequirement",
        }

        if child_type is None and parent_type:
            child_type = child_type_map.get(parent_type)

        if not child_type:
            return []

        # Query based on child type
        if child_type == "hierarchicalrequirement":
            # Use include_project_scope=False because stories can be in different projects
            return self.query(
                child_type,
                query=f'(PortfolioItem = "{parent_ref}")',
                fetch=fetch,
                include_project_scope=False,
            )
        else:
            return self.query(
                child_type,
                query=f'(Parent = "{parent_ref}")',
                fetch=fetch,
            )

    def move_story(
        self,
        story_ref: str,
        new_feature_ref: str,
        inherit_release_iteration: bool = True,
    ) -> dict:
        """
        Move a user story to a different feature.

        Args:
            story_ref: Reference to the story to move (_ref)
            new_feature_ref: Reference to the new parent feature (_ref)
            inherit_release_iteration: If True, update Release/Iteration from new feature

        Returns:
            Updated story data
        """
        fields: dict[str, Any] = {"PortfolioItem": new_feature_ref}

        if inherit_release_iteration:
            # Get new feature's Release and Iteration
            feature = self.get_object(new_feature_ref, fetch="Release,Iteration")
            if feature.get("Release"):
                fields["Release"] = feature["Release"].get("_ref")
            if feature.get("Iteration"):
                fields["Iteration"] = feature["Iteration"].get("_ref")

        return self.update_object(story_ref, fields)

    def clone_item(
        self,
        source_ref: str,
        new_name: Optional[str] = None,
        new_parent_ref: Optional[str] = None,
        include_children: bool = False,
    ) -> dict:
        """
        Clone a Rally item (story, feature, etc.).

        Args:
            source_ref: Reference to the item to clone (_ref)
            new_name: Name for the clone. If None, uses "Copy of [original name]"
            new_parent_ref: New parent reference. If None, uses same parent as original
            include_children: If True, also clone child items (stories under feature, etc.)

        Returns:
            Created clone data
        """
        # Get source item with all fields
        source = self.get_object(source_ref, fetch="true")

        # Determine object type from ref
        object_type = None
        type_mapping = {
            "hierarchicalrequirement": "hierarchicalrequirement",
            "portfolioitem/feature": "portfolioitem/feature",
            "portfolioitem/capability": "portfolioitem/capability",
            "portfolioitem/epic": "portfolioitem/epic",
            "portfolioitem/initiative": "portfolioitem/initiative",
            "portfolioitem/theme": "portfolioitem/theme",
            "task": "task",
            "defect": "defect",
        }

        for key in type_mapping:
            if key in source_ref.lower():
                object_type = type_mapping[key]
                break

        if not object_type:
            raise Exception(f"Could not determine object type from ref: {source_ref}")

        # Build fields for clone
        excluded_fields = {
            "_ref", "_refObjectUUID", "_refObjectName", "_type",
            "ObjectID", "ObjectUUID", "CreationDate", "FormattedID",
            "DirectChildrenCount", "Attachments", "Tasks", "Children",
            "Changesets", "Milestones", "Predecessors", "Successors",
            "Defects", "TestCases", "RevisionHistory", "Discussion",
            "Tags", "Recycled", "Subscription", "Workspace", "Project",
        }

        fields: dict[str, Any] = {}
        for key, value in source.items():
            if key in excluded_fields or key.startswith("_"):
                continue
            if isinstance(value, dict) and "_ref" in value:
                # Handle reference fields
                if key in ["Parent", "PortfolioItem", "Feature", "WorkProduct"]:
                    if new_parent_ref:
                        fields[key] = new_parent_ref
                    else:
                        fields[key] = value.get("_ref")
                elif key == "Owner":
                    fields[key] = value.get("_ref")
                # Skip other reference fields
            elif value is not None:
                fields[key] = value

        # Set new name
        original_name = source.get("Name", "Unknown")
        fields["Name"] = new_name or f"Copy of {original_name}"

        # Set parent if provided
        if new_parent_ref:
            if object_type == "hierarchicalrequirement":
                fields["PortfolioItem"] = new_parent_ref
            elif object_type.startswith("portfolioitem"):
                fields["Parent"] = new_parent_ref
            elif object_type == "task":
                fields["WorkProduct"] = new_parent_ref

        # Reset state for clone
        if "ScheduleState" in fields:
            fields["ScheduleState"] = "Defined"
        if "State" in fields and object_type.startswith("portfolioitem"):
            fields["State"] = "Backlog"

        # Create the clone
        clone = self.create_object(object_type, fields)

        # Clone children if requested
        if include_children and clone.get("_ref"):
            children = self.get_children(source_ref)
            for child in children:
                self.clone_item(
                    child["_ref"],
                    new_parent_ref=clone["_ref"],
                    include_children=True,  # Recursive
                )

        return clone

    def bulk_create_stories(
        self,
        feature_ref: str,
        stories: list[dict],
        inherit_from_feature: bool = True,
        parallel: bool = True,
        auto_estimate: bool = False,
        use_ai_estimation: bool = True
    ) -> list[dict]:
        """
        Create multiple user stories under a feature (parallelized).

        Args:
            feature_ref: Parent feature reference (_ref)
            stories: List of story dicts with keys:
                - name: Story title (required)
                - role: User role for "As a..." (optional)
                - want: What user wants (optional)
                - so_that: Benefit (optional)
                - acceptance_criteria: List of criteria (optional)
                - description: Raw description HTML (alternative to role/want/so_that)
                - plan_estimate: Story points (optional)
                - owner: Owner reference (optional)
            inherit_from_feature: If True, inherit Release/Iteration/Owner from feature
            parallel: If True, create stories in parallel (default: True)
            auto_estimate: If True, auto-estimate stories missing plan_estimate
            use_ai_estimation: Use AI analysis vs. prompting user (default: True)

        Returns:
            List of created story objects
        """
        # Auto-estimate stories missing plan_estimate
        # US789694: Store estimation results for rationale generation
        estimation_results = {}
        if auto_estimate:
            from .estimation import generate_estimation_rationale

            for story in stories:
                if 'plan_estimate' not in story or story['plan_estimate'] is None:
                    # Extract story details for estimation
                    story_name = story.get('name', '')
                    story_description = story.get('description', '')
                    acceptance_criteria = story.get('acceptance_criteria', [])

                    # Build description from role/want/so_that if not provided
                    if not story_description and 'role' in story and 'want' in story:
                        role = story['role']
                        want = story['want']
                        so_that = story.get('so_that', '')
                        story_description = f"As a {role}, I want {want} so that {so_that}"

                    try:
                        # Estimate using AI analysis
                        result = self.estimate_user_story(
                            story_name=story_name,
                            story_description=story_description,
                            acceptance_criteria=acceptance_criteria,
                            use_ai_analysis=use_ai_estimation
                        )

                        # US789694: Generate rationale for estimate
                        result = generate_estimation_rationale(result, story_name, story_description)

                        # Set the estimate
                        story['plan_estimate'] = result.base_estimate

                        # US789694: Store result for Notes field population
                        estimation_results[story_name] = result

                        # US789694: Enhanced logging with rationale summary
                        print(f"[ESTIMATE] Auto-estimated '{story_name}': {result.base_estimate} points (score: {result.total_complexity_score}/100)")
                        print(f"   Recommendation: {result.rationale_action}")
                        if result.key_drivers:
                            print(f"   Key driver: {result.key_drivers[0]}")

                        # Warn if story should be split
                        if result.should_split:
                            print(f"⚠️  Story '{story_name}' is too large ({result.base_estimate} pts) - consider splitting")

                    except Exception as e:
                        print(f"⚠️  Failed to estimate '{story_name}': {e}")
                        # Continue without estimate

        # Get feature details for inheritance
        feature = None
        if inherit_from_feature:
            feature = self.get_object(feature_ref, fetch="Release,Iteration,Owner")

        def build_story_fields(story_data: dict) -> dict:
            """Build fields dict for a single story."""
            fields: dict[str, Any] = {
                "Name": story_data["name"],
                "PortfolioItem": feature_ref,
                "ScheduleState": "Defined",
            }

            # Build description
            if "description" in story_data:
                fields["Description"] = story_data["description"]
            elif "role" in story_data and "want" in story_data:
                role = story_data["role"]
                want = story_data["want"]
                so_that = story_data.get("so_that", "")
                criteria = story_data.get("acceptance_criteria", [])

                # Use template rendering for consistent formatting
                fields["Description"] = self._render_story_description(role, want, so_that, criteria)

            # US789694: Populate Notes field with rationale if auto-estimated
            if auto_estimate and story_data["name"] in estimation_results:
                from .estimation import format_rationale_markdown
                result = estimation_results[story_data["name"]]
                fields["Notes"] = format_rationale_markdown(result)

            # Set optional fields
            if "plan_estimate" in story_data:
                fields["PlanEstimate"] = story_data["plan_estimate"]

            if "owner" in story_data:
                fields["Owner"] = story_data["owner"]
            elif inherit_from_feature and feature and feature.get("Owner"):
                fields["Owner"] = feature["Owner"].get("_ref")

            # Inherit Release/Iteration
            if inherit_from_feature and feature:
                if feature.get("Release"):
                    fields["Release"] = feature["Release"].get("_ref")
                if feature.get("Iteration"):
                    fields["Iteration"] = feature["Iteration"].get("_ref")

            # Add project
            if self.project_ref:
                fields["Project"] = self.project_ref

            return fields

        # Build all field dicts first
        all_fields = [build_story_fields(s) for s in stories]

        if parallel and len(all_fields) > 1:
            # Create stories in parallel
            created = []
            with ThreadPoolExecutor(max_workers=DEFAULT_MAX_WORKERS) as executor:
                futures = [
                    executor.submit(self.create_object, "hierarchicalrequirement", fields)
                    for fields in all_fields
                ]
                for future in futures:
                    created.append(future.result())
            return created
        else:
            # Create stories sequentially
            return [self.create_object("hierarchicalrequirement", fields) for fields in all_fields]

    def estimate_user_story(
        self,
        story_name: str,
        story_description: str = "",
        acceptance_criteria: Optional[list[str]] = None,
        complexity_factors: Optional[ComplexityFactors] = None,
        use_ai_analysis: bool = False,
        team_member: Optional[str] = None
    ) -> EstimationResult:
        """
        Estimate story points for a user story.

        Args:
            story_name: Story title
            story_description: Story description
            acceptance_criteria: List of acceptance criteria
            complexity_factors: Manual complexity assessment (preferred)
            use_ai_analysis: Use AI to analyze story if complexity_factors not provided
            team_member: Team member for multiplier adjustment

        Returns:
            EstimationResult with base and adjusted estimates

        Example:
            # Manual complexity factors
            factors = ComplexityFactors(
                technical_complexity=4,
                integration_requirements=3,
                risk_uncertainty=2
            )
            result = rally.estimate_user_story(
                "Implement OAuth 2.0",
                "Add OAuth authentication",
                complexity_factors=factors,
                team_member="chris"
            )

            # AI-based analysis
            result = rally.estimate_user_story(
                "Implement OAuth 2.0",
                "Add OAuth authentication with Google and GitHub providers",
                acceptance_criteria=["Support Google OAuth", "Support GitHub OAuth"],
                use_ai_analysis=True,
                team_member="chris"
            )
        """
        # Get team multiplier if team member provided
        team_multiplier = None
        if team_member:
            team_multiplier = get_member_estimate_multiplier(team_member)
            if team_multiplier is None:
                print(f"⚠️  Warning: Team member '{team_member}' not found in config. Using base estimate only.")

        # Use manual complexity factors if provided
        if complexity_factors:
            return estimate_story(
                story_name=story_name,
                story_description=story_description,
                complexity_factors=complexity_factors,
                team_member=team_member,
                team_multiplier=team_multiplier
            )

        # Use AI analysis
        if use_ai_analysis:
            return estimate_story_ai_analysis(
                story_name=story_name,
                story_description=story_description,
                acceptance_criteria=acceptance_criteria,
                team_member=team_member,
                team_multiplier=team_multiplier
            )

        # No estimation method provided
        raise ValueError(
            "Must provide either complexity_factors or set use_ai_analysis=True"
        )

    # =========================================================================
    # Plan Estimate Validation & Helpers
    # =========================================================================
    #
    # Rules for Plan Estimate:
    #   - Plan Estimate = Sum(Task Estimates) / 8
    #   - (So a story with tasks totaling 8 hours = 1 story point)
    #   - If a story has no tasks, Task Estimate = Plan Estimate * 8
    #
    # =========================================================================

    def is_stage_story(self, story_name: str) -> bool:
        """
        Check if a story is a Stage story (should be excluded from estimate validation).

        Stage stories are administrative tracking stories that follow the format:
        "[Stage X] ..." and have 0 points by design.

        Args:
            story_name: Name of the story

        Returns:
            True if story name starts with "[Stage", False otherwise
        """
        return story_name.strip().startswith("[Stage")

    def validate_story_estimate(
        self,
        story_ref: str,
        skip_stage_stories: bool = True,
    ) -> dict:
        """
        Validate that a story's Plan Estimate matches Sum(Task Estimates) / 8.

        Args:
            story_ref: Reference to the user story (_ref)
            skip_stage_stories: If True, skip validation for Stage stories (default: True)

        Returns:
            Dict with validation results:
            - valid: bool - True if estimate matches formula
            - story_id: str - Story FormattedID
            - plan_estimate: float - Current Plan Estimate
            - task_sum: float - Sum of task estimates in hours
            - expected_plan_estimate: float - What Plan Estimate should be
            - tasks: list - List of tasks with their estimates
            - message: str - Description of validation result
            - skipped: bool - True if story was skipped (Stage story)
        """
        story = self.get_object(
            story_ref,
            fetch="Name,FormattedID,PlanEstimate,Tasks"
        )

        # Check if this is a Stage story
        story_name = story.get("Name", "")
        if skip_stage_stories and self.is_stage_story(story_name):
            return {
                "valid": True,
                "story_id": story.get("FormattedID"),
                "story_name": story_name,
                "plan_estimate": story.get("PlanEstimate"),
                "task_sum": 0,
                "expected_plan_estimate": 0,
                "tasks": [],
                "message": "Skipped: Stage story (excluded from validation)",
                "skipped": True,
            }

        result = {
            "valid": True,
            "story_id": story.get("FormattedID"),
            "story_name": story.get("Name"),
            "plan_estimate": story.get("PlanEstimate"),
            "task_sum": 0,
            "expected_plan_estimate": 0,
            "tasks": [],
            "message": "",
            "skipped": False,
        }

        # Get tasks for this story
        tasks = self.query(
            "task",
            query=f'(WorkProduct = "{story_ref}")',
            fetch="Name,FormattedID,Estimate,State",
            include_project_scope=False,
        )

        task_sum = 0
        has_none_estimate = False
        for task in tasks:
            est = task.get("Estimate")
            result["tasks"].append({
                "id": task.get("FormattedID"),
                "name": task.get("Name"),
                "estimate": est,
            })
            if est is None:
                has_none_estimate = True
            else:
                task_sum += est

        result["task_sum"] = task_sum
        result["expected_plan_estimate"] = task_sum / 8 if task_sum > 0 else 0

        plan_est = story.get("PlanEstimate")

        # Validation logic
        if plan_est is None:
            result["valid"] = False
            result["message"] = "Story missing Plan Estimate"
        elif len(tasks) == 0:
            # All stories must have at least 1 task
            result["valid"] = False
            result["message"] = "Story must have at least 1 task"
        elif has_none_estimate:
            result["valid"] = False
            result["message"] = f"Some tasks missing estimates. Task sum: {task_sum}, expected Plan Estimate: {result['expected_plan_estimate']}"
        elif plan_est != result["expected_plan_estimate"]:
            result["valid"] = False
            result["message"] = f"Plan Estimate ({plan_est}) != Sum(Tasks)/{8} ({result['expected_plan_estimate']})"
        else:
            result["message"] = "Valid: Plan Estimate matches Sum(Task Estimates) / 8"

        return result

    def get_expected_task_estimate(
        self,
        story_ref: str,
    ) -> float:
        """
        Calculate the expected task estimate for a story with no tasks.

        Formula: Task Estimate = Plan Estimate * 8

        Args:
            story_ref: Reference to the user story (_ref)

        Returns:
            Expected task estimate in hours (Plan Estimate * 8)

        Raises:
            ValueError: If story has no Plan Estimate
        """
        story = self.get_object(story_ref, fetch="PlanEstimate,FormattedID")
        plan_est = story.get("PlanEstimate")

        if plan_est is None:
            raise ValueError(
                f"Story {story.get('FormattedID')} has no Plan Estimate"
            )

        return plan_est * 8

    def validate_story_hierarchy(self, story_ref: str) -> dict:
        """
        Validate that a story has complete parent hierarchy (Story → Feature → Capability → Initiative).

        Args:
            story_ref: Story reference to validate

        Returns:
            Dict with hierarchy information:
            {
                "story": {...},
                "feature": {...},
                "capability": {...},
                "initiative": {...},
                "complete": bool
            }

        Raises:
            ValueError: If hierarchy is incomplete
        """
        # Fetch story with parent info
        story = self.get_object(story_ref, fetch="FormattedID,Name,PortfolioItem,_ref")

        result = {
            "story": {
                "id": story.get("FormattedID"),
                "name": story.get("Name"),
                "ref": story["_ref"]
            },
            "feature": None,
            "capability": None,
            "initiative": None,
            "complete": False
        }

        # Check if story has parent (Feature)
        if not story.get("PortfolioItem"):
            raise ValueError(
                f"Story {story.get('FormattedID')} has no parent Feature. "
                f"Stories must be linked to a Feature → Capability → Initiative."
            )

        # Use existing validate_hierarchy to check Feature → Capability → Initiative
        feature_hierarchy = self.validate_hierarchy(story["PortfolioItem"]["_ref"])

        result["feature"] = feature_hierarchy["feature"]
        result["capability"] = feature_hierarchy["capability"]
        result["initiative"] = feature_hierarchy["initiative"]
        result["complete"] = True

        return result

    def create_task(
        self,
        story_ref: str,
        name: str,
        description: str = "",
        goal: str = "",
        details: str = "",
        state: str = "Defined",
        owner_ref: Optional[str] = None,
        estimate: Optional[float] = None,
        auto_estimate: bool = True,
        validate_hierarchy: bool = True,
    ) -> dict:
        """
        Create a task under a user story.

        If auto_estimate is True and no estimate is provided, calculates
        estimate based on story's Plan Estimate * 8.

        Args:
            story_ref: Parent user story reference (_ref)
            name: Task name
            description: Raw task description (alternative to goal/details)
            goal: What this task aims to achieve (used with template)
            details: Specific work to be done (used with template)
            state: Task state (default: "Defined")
            owner_ref: Owner reference (_ref)
            estimate: Task estimate in hours. If None and auto_estimate=True,
                      uses Plan Estimate * 8
            auto_estimate: If True, auto-calculate estimate from story's
                          Plan Estimate when estimate is not provided
            validate_hierarchy: If True, validate complete parent hierarchy before creation

        Returns:
            Created task data

        Raises:
            ValueError: If validate_hierarchy=True and hierarchy is incomplete

        Note:
            Provide either 'description' OR 'goal'+'details'.
            If goal/details provided, uses template rendering.
        """
        # Validate complete hierarchy: Task → Story → Feature → Capability → Initiative
        if validate_hierarchy:
            hierarchy = self.validate_story_hierarchy(story_ref)
            # Log hierarchy for debugging (optional)
            print(f"✓ Validated hierarchy: Task → {hierarchy['story']['id']} → "
                  f"{hierarchy['feature']['id']} → {hierarchy['capability']['id']} → "
                  f"{hierarchy['initiative']['id']}")

        fields: dict[str, Any] = {
            "Name": name,
            "WorkProduct": story_ref,
            "State": state,
        }

        # Build description using template if goal/details provided
        if goal and details:
            fields["Description"] = self._render_task_description(goal, details)
        elif description:
            fields["Description"] = description

        if owner_ref:
            fields["Owner"] = owner_ref

        # Handle estimate
        if estimate is not None:
            fields["Estimate"] = estimate
        elif auto_estimate:
            try:
                fields["Estimate"] = self.get_expected_task_estimate(story_ref)
            except ValueError:
                pass  # Story has no Plan Estimate, skip auto-estimate

        if self.project_ref:
            fields["Project"] = self.project_ref

        return self.create_object("task", fields)

    def create_defect(
        self,
        name: str,
        description: str = "",
        summary: str = "",
        steps_to_reproduce: Optional[list[str]] = None,
        expected: str = "",
        actual: str = "",
        environment: Optional[dict[str, str]] = None,
        state: str = "Submitted",
        priority: str = "Normal Attention",
        severity: str = "Major Problem",
    ) -> dict:
        """
        Create a defect/bug report.

        Args:
            name: Defect title/name
            description: Raw defect description (alternative to structured fields)
            summary: One-line summary of the defect (used with template)
            steps_to_reproduce: List of steps to reproduce (used with template)
            expected: Expected behavior (used with template)
            actual: Actual behavior (used with template)
            environment: Dict with browser, os, version keys (used with template)
            state: Defect state (default: "Submitted")
            priority: Priority level (default: "Normal Attention")
            severity: Severity level (default: "Major Problem")

        Returns:
            Created defect data

        Note:
            Provide either 'description' OR structured fields (summary, steps, etc.).
            If structured fields provided, uses template rendering.
        """
        fields: dict[str, Any] = {
            "Name": name,
            "State": state,
            "Priority": priority,
            "Severity": severity,
        }

        # Build description using template if structured fields provided
        if summary and steps_to_reproduce and expected and actual:
            env = environment or {"browser": "Not specified", "os": "Not specified", "version": "Not specified"}
            fields["Description"] = self._render_defect_description(
                summary, steps_to_reproduce, expected, actual, env
            )
        elif description:
            fields["Description"] = description

        if self.project_ref:
            fields["Project"] = self.project_ref

        return self.create_object("defect", fields)

    def fix_task_estimates(
        self,
        story_ref: str,
        dry_run: bool = True,
    ) -> dict:
        """
        Fix task estimates to match the story's Plan Estimate.

        Distributes Plan Estimate * 8 hours evenly across all tasks.

        Args:
            story_ref: Reference to the user story (_ref)
            dry_run: If True, only report what would change without updating

        Returns:
            Dict with:
            - story_id: str
            - plan_estimate: float
            - total_hours: float (Plan Estimate * 8)
            - task_count: int
            - hours_per_task: float
            - updates: list of task updates (made or proposed)
        """
        story = self.get_object(
            story_ref,
            fetch="Name,FormattedID,PlanEstimate"
        )

        plan_est = story.get("PlanEstimate")
        if plan_est is None or plan_est == 0:
            return {
                "story_id": story.get("FormattedID"),
                "plan_estimate": plan_est,
                "total_hours": 0,
                "task_count": 0,
                "hours_per_task": 0,
                "updates": [],
                "message": "Story has no Plan Estimate or Plan Estimate is 0",
            }

        total_hours = plan_est * 8

        # Get tasks
        tasks = self.query(
            "task",
            query=f'(WorkProduct = "{story_ref}")',
            fetch="Name,FormattedID,Estimate,_ref",
            include_project_scope=False,
        )

        if not tasks:
            return {
                "story_id": story.get("FormattedID"),
                "plan_estimate": plan_est,
                "total_hours": total_hours,
                "task_count": 0,
                "hours_per_task": 0,
                "updates": [],
                "message": "Story has no tasks",
            }

        hours_per_task = total_hours / len(tasks)
        updates = []

        for task in tasks:
            current_est = task.get("Estimate")
            update_info = {
                "task_id": task.get("FormattedID"),
                "task_name": task.get("Name"),
                "current_estimate": current_est,
                "new_estimate": hours_per_task,
                "updated": False,
            }

            if current_est != hours_per_task:
                if not dry_run:
                    self.update_object(task["_ref"], {"Estimate": hours_per_task})
                    update_info["updated"] = True
                updates.append(update_info)

        return {
            "story_id": story.get("FormattedID"),
            "plan_estimate": plan_est,
            "total_hours": total_hours,
            "task_count": len(tasks),
            "hours_per_task": hours_per_task,
            "updates": updates,
            "dry_run": dry_run,
            "message": f"{'Would update' if dry_run else 'Updated'} {len(updates)} task(s)",
        }

    def validate_feature_estimates(
        self,
        feature_ref: str,
        skip_stage_stories: bool = True,
    ) -> dict:
        """
        Validate all story estimates under a feature (parallelized).

        Checks that each story's Plan Estimate matches Sum(Task Estimates) / 8.
        Stage stories (names starting with "[Stage") are excluded by default.

        Args:
            feature_ref: Reference to the feature (_ref)
            skip_stage_stories: If True, skip Stage stories (default: True)

        Returns:
            Dict with validation results:
            - feature_id: str - Feature FormattedID
            - feature_name: str - Feature name
            - total_stories: int - Total stories checked
            - valid_count: int - Stories with valid estimates
            - invalid_count: int - Stories with invalid estimates
            - skipped_count: int - Stories skipped (Stage stories)
            - missing_tasks_count: int - Stories with points but no tasks
            - discrepancies: list - Stories with estimate mismatches
            - missing_tasks: list - Stories needing tasks
        """
        feature = self.get_object(feature_ref, fetch="Name,FormattedID")

        result = {
            "feature_id": feature.get("FormattedID"),
            "feature_name": feature.get("Name"),
            "total_stories": 0,
            "valid_count": 0,
            "invalid_count": 0,
            "skipped_count": 0,
            "missing_tasks_count": 0,
            "discrepancies": [],
            "missing_tasks": [],
        }

        # Get all stories under this feature
        stories = self.query(
            "hierarchicalrequirement",
            query=f'(PortfolioItem = "{feature_ref}")',
            fetch="Name,FormattedID,PlanEstimate,PortfolioItem,_ref",
            include_project_scope=False,
        )

        result["total_stories"] = len(stories)

        # Filter out Stage stories first
        stories_to_validate = []
        for story in stories:
            story_name = story.get("Name", "")
            if skip_stage_stories and self.is_stage_story(story_name):
                result["skipped_count"] += 1
            else:
                stories_to_validate.append(story)

        if not stories_to_validate:
            return result

        def get_tasks_for_story(story: dict) -> tuple[dict, list]:
            """Fetch tasks for a single story."""
            tasks = self.query(
                "task",
                query=f'(WorkProduct = "{story["_ref"]}")',
                fetch="Name,FormattedID,Estimate",
                include_project_scope=False,
            )
            return story, tasks

        # Fetch tasks for all stories in parallel
        with ThreadPoolExecutor(max_workers=DEFAULT_MAX_WORKERS) as executor:
            futures = [executor.submit(get_tasks_for_story, s) for s in stories_to_validate]

            for future in futures:
                story, tasks = future.result()
                story_name = story.get("Name", "")
                story_id = story.get("FormattedID")
                plan_estimate = story.get("PlanEstimate") or 0

                if not tasks:
                    # All stories must have at least 1 task
                    result["missing_tasks_count"] += 1
                    result["missing_tasks"].append({
                        "story_id": story_id,
                        "story_name": story_name[:50],
                        "plan_estimate": plan_estimate,
                        "expected_hours": plan_estimate * 8 if plan_estimate else 0,
                    })
                    continue

                # Calculate task sum
                task_sum = sum(t.get("Estimate") or 0 for t in tasks)
                expected_hours = plan_estimate * 8

                if task_sum != expected_hours:
                    result["invalid_count"] += 1
                    result["discrepancies"].append({
                        "story_id": story_id,
                        "story_name": story_name[:50],
                        "plan_estimate": plan_estimate,
                        "expected_hours": expected_hours,
                        "actual_hours": task_sum,
                        "difference": task_sum - expected_hours,
                        "task_count": len(tasks),
                    })
                else:
                    result["valid_count"] += 1

        return result

    def audit_initiative_estimates(
        self,
        initiative_id: str,
        skip_stage_stories: bool = True,
    ) -> dict:
        """
        Audit all story estimates under an initiative (parallelized).

        Traverses the hierarchy: Initiative → Epics → Capabilities → Features → Stories
        and validates that each story's Plan Estimate matches Sum(Task Estimates) / 8.
        Stage stories (names starting with "[Stage") are excluded by default.

        Args:
            initiative_id: FormattedID of the initiative (e.g., "I937")
            skip_stage_stories: If True, skip Stage stories (default: True)

        Returns:
            Dict with audit results:
            - initiative_id: str
            - initiative_name: str
            - total_stories: int
            - valid_count: int
            - invalid_count: int
            - skipped_count: int
            - missing_tasks_count: int
            - discrepancies: list - Stories with estimate mismatches
            - missing_tasks: list - Stories needing tasks
            - by_feature: dict - Results grouped by feature
        """
        # Find the initiative
        initiative = self.find_portfolio_item(initiative_id, "initiative")
        if not initiative:
            raise ValueError(f"Initiative {initiative_id} not found")

        result = {
            "initiative_id": initiative.get("FormattedID"),
            "initiative_name": initiative.get("Name"),
            "total_stories": 0,
            "valid_count": 0,
            "invalid_count": 0,
            "skipped_count": 0,
            "missing_tasks_count": 0,
            "discrepancies": [],
            "missing_tasks": [],
            "by_feature": {},
        }

        init_ref = initiative["_ref"]

        # Get all epics under this initiative
        epics = self.query(
            "portfolioitem/epic",
            query=f'(Parent = "{init_ref}")',
            fetch="FormattedID,Name,_ref",
        )

        if not epics:
            return result

        # Helper functions for parallel queries
        def get_capabilities(epic: dict) -> list[dict]:
            """Get capabilities under an epic."""
            return self.query(
                "portfolioitem/capability",
                query=f'(Parent = "{epic["_ref"]}")',
                fetch="FormattedID,Name,_ref",
            )

        def get_features(cap: dict) -> list[dict]:
            """Get features under a capability."""
            return self.query(
                "portfolioitem/feature",
                query=f'(Parent = "{cap["_ref"]}")',
                fetch="FormattedID,Name,_ref",
            )

        # Get all capabilities in parallel
        all_capabilities = []
        with ThreadPoolExecutor(max_workers=DEFAULT_MAX_WORKERS) as executor:
            cap_futures = [executor.submit(get_capabilities, epic) for epic in epics]
            for future in cap_futures:
                all_capabilities.extend(future.result())

        if not all_capabilities:
            return result

        # Get all features in parallel
        all_features = []
        with ThreadPoolExecutor(max_workers=DEFAULT_MAX_WORKERS) as executor:
            feature_futures = [executor.submit(get_features, cap) for cap in all_capabilities]
            for future in feature_futures:
                all_features.extend(future.result())

        if not all_features:
            return result

        # Validate all features in parallel
        with ThreadPoolExecutor(max_workers=DEFAULT_MAX_WORKERS) as executor:
            validation_futures = {
                executor.submit(
                    self.validate_feature_estimates,
                    feature["_ref"],
                    skip_stage_stories,
                ): feature
                for feature in all_features
            }

            for future in as_completed(validation_futures):
                feature_result = future.result()

                # Aggregate results
                result["total_stories"] += feature_result["total_stories"]
                result["valid_count"] += feature_result["valid_count"]
                result["invalid_count"] += feature_result["invalid_count"]
                result["skipped_count"] += feature_result["skipped_count"]
                result["missing_tasks_count"] += feature_result["missing_tasks_count"]
                result["discrepancies"].extend(feature_result["discrepancies"])
                result["missing_tasks"].extend(feature_result["missing_tasks"])

                # Store per-feature results
                result["by_feature"][feature_result["feature_id"]] = {
                    "name": feature_result["feature_name"],
                    "total": feature_result["total_stories"],
                    "valid": feature_result["valid_count"],
                    "invalid": feature_result["invalid_count"],
                    "skipped": feature_result["skipped_count"],
                    "missing_tasks": feature_result["missing_tasks_count"],
                }

        return result


# =============================================================================
# Date and Deliverable Tracking
# =============================================================================

    def analyze_data_consistency(
        self,
        item_ref: str,
        check_children: bool = True,
        check_parent: bool = True,
    ) -> dict[str, Any]:
        """
        Analyze an item for data consistency issues (missing dates, unaligned release/iteration, etc.).

        Checks for:
        1. PlannedEndDate exists but Release/Iteration missing or misaligned
        2. Iteration exists but Release missing
        3. Children have PlannedEndDate but parent doesn't
        4. Parent has PlannedEndDate but children don't

        Args:
            item_ref: Rally item reference (_ref)
            check_children: Whether to check child items (default: True)
            check_parent: Whether to check parent item (default: True)

        Returns:
            Dict with:
            - has_issues (bool): Whether any issues were found
            - issues (list): List of issue descriptions
            - suggestions (list): List of suggested fixes with action data
            - item_info (dict): Basic item information

        Example:
            result = api.analyze_data_consistency(feature["_ref"])
            if result["has_issues"]:
                for issue in result["issues"]:
                    print(f"Issue: {issue}")
                for suggestion in result["suggestions"]:
                    print(f"Suggestion: {suggestion['description']}")
        """
        # Fetch the item with all relevant fields
        item = self.get_object(
            item_ref,
            fetch="Name,FormattedID,ObjectID,_type,PlannedEndDate,PlannedStartDate,"
                  "Release,Iteration,Parent,Children,UserStories,Features,Capabilities"
        )

        result = {
            "has_issues": False,
            "issues": [],
            "suggestions": [],
            "item_info": {
                "ref": item_ref,
                "formatted_id": item.get("FormattedID"),
                "name": item.get("Name"),
                "type": item.get("_type"),
            }
        }

        # Check 1: PlannedEndDate exists but Release/Iteration missing or misaligned
        planned_end = item.get("PlannedEndDate")
        release_ref = item.get("Release")
        iteration_ref = item.get("Iteration")

        if planned_end and not iteration_ref:
            result["has_issues"] = True
            result["issues"].append(
                f"Item has PlannedEndDate ({planned_end}) but no Iteration assigned"
            )
            # Try to find matching iteration
            try:
                from datetime import datetime
                end_date = datetime.fromisoformat(planned_end.replace("Z", "+00:00"))
                iterations = self.get_iterations()
                matching_iteration = None
                for iteration in iterations:
                    iter_start = iteration.get("StartDate")
                    iter_end = iteration.get("EndDate")
                    if iter_start and iter_end:
                        start_dt = datetime.fromisoformat(iter_start.replace("Z", "+00:00"))
                        end_dt = datetime.fromisoformat(iter_end.replace("Z", "+00:00"))
                        if start_dt <= end_date <= end_dt:
                            matching_iteration = iteration
                            break

                if matching_iteration:
                    result["suggestions"].append({
                        "description": f"Set Iteration to '{matching_iteration['Name']}' (matches PlannedEndDate)",
                        "action": "set_iteration",
                        "data": {
                            "item_ref": item_ref,
                            "iteration_ref": matching_iteration["_ref"],
                            "iteration_name": matching_iteration["Name"]
                        }
                    })
            except Exception:
                pass  # Ignore date parsing errors

        # Check 2: Iteration exists but Release missing
        if iteration_ref and not release_ref:
            result["has_issues"] = True
            result["issues"].append(
                "Item has Iteration assigned but no Release"
            )
            # Try to find matching release for iteration
            try:
                iteration = self.get_object(iteration_ref, fetch="Name,StartDate,EndDate")
                matching_release = self.find_release_for_iteration(iteration, raise_if_not_found=False)
                if matching_release:
                    result["suggestions"].append({
                        "description": f"Set Release to '{matching_release['Name']}' (matches Iteration dates)",
                        "action": "set_release",
                        "data": {
                            "item_ref": item_ref,
                            "release_ref": matching_release["_ref"],
                            "release_name": matching_release["Name"]
                        }
                    })
            except Exception:
                pass

        # Check 3: Release/Iteration exist but PlannedEndDate missing
        if (release_ref or iteration_ref) and not planned_end:
            result["has_issues"] = True
            result["issues"].append(
                "Item has Release/Iteration assigned but no PlannedEndDate"
            )
            # Suggest using iteration end date or release end date
            try:
                if iteration_ref:
                    iteration = self.get_object(iteration_ref, fetch="Name,EndDate")
                    if iteration.get("EndDate"):
                        result["suggestions"].append({
                            "description": f"Set PlannedEndDate to Iteration end date ({iteration['EndDate']})",
                            "action": "set_planned_end_date",
                            "data": {
                                "item_ref": item_ref,
                                "date": iteration["EndDate"],
                                "source": f"Iteration {iteration['Name']}"
                            }
                        })
                elif release_ref:
                    release = self.get_object(release_ref, fetch="Name,ReleaseDate")
                    if release.get("ReleaseDate"):
                        result["suggestions"].append({
                            "description": f"Set PlannedEndDate to Release date ({release['ReleaseDate']})",
                            "action": "set_planned_end_date",
                            "data": {
                                "item_ref": item_ref,
                                "date": release["ReleaseDate"],
                                "source": f"Release {release['Name']}"
                            }
                        })
            except Exception:
                pass

        # Check 4: Parent-child date consistency
        if check_parent and item.get("Parent"):
            try:
                parent = self.get_object(
                    item["Parent"]["_ref"],
                    fetch="Name,FormattedID,PlannedEndDate,PlannedStartDate"
                )
                parent_end = parent.get("PlannedEndDate")

                # Child has date but parent doesn't
                if planned_end and not parent_end:
                    result["has_issues"] = True
                    result["issues"].append(
                        f"Item has PlannedEndDate but parent {parent.get('FormattedID')} doesn't"
                    )
                    result["suggestions"].append({
                        "description": f"Set parent {parent.get('FormattedID')} PlannedEndDate to {planned_end}",
                        "action": "set_parent_date",
                        "data": {
                            "item_ref": parent["_ref"],
                            "date": planned_end,
                            "source": f"child {item.get('FormattedID')}"
                        }
                    })
            except Exception:
                pass

        # Check 5: Check children
        if check_children:
            children = []
            item_type = item.get("_type", "")

            # Determine child collection based on item type
            if "UserStories" in item and item["UserStories"].get("Count", 0) > 0:
                stories = self.query(
                    "hierarchicalrequirement",
                    query=f'(Feature.ObjectID = {item["ObjectID"]})',
                    fetch="Name,FormattedID,PlannedEndDate,Release,Iteration"
                )
                children.extend(stories)
            elif "Features" in item and item["Features"].get("Count", 0) > 0:
                features = self.query(
                    "portfolioitem/feature",
                    query=f'(Parent.ObjectID = {item["ObjectID"]})',
                    fetch="Name,FormattedID,PlannedEndDate,Release,Iteration"
                )
                children.extend(features)
            elif "Children" in item and item["Children"].get("Count", 0) > 0:
                # Generic children (for capabilities, epics, etc.)
                children_data = self.query(
                    "portfolioitem",
                    query=f'(Parent.ObjectID = {item["ObjectID"]})',
                    fetch="Name,FormattedID,PlannedEndDate,Release,Iteration,_type"
                )
                children.extend(children_data)

            # Check if children have dates but this item doesn't
            if children and not planned_end:
                children_with_dates = [c for c in children if c.get("PlannedEndDate")]
                if children_with_dates:
                    result["has_issues"] = True
                    result["issues"].append(
                        f"{len(children_with_dates)} of {len(children)} children have PlannedEndDate but this item doesn't"
                    )
                    # Find the latest child date
                    latest_date = max(c["PlannedEndDate"] for c in children_with_dates)
                    result["suggestions"].append({
                        "description": f"Set PlannedEndDate to latest child date ({latest_date})",
                        "action": "set_planned_end_date",
                        "data": {
                            "item_ref": item_ref,
                            "date": latest_date,
                            "source": "latest child date"
                        }
                    })

            # Check if this item has date but children don't
            if planned_end and children:
                children_without_dates = [c for c in children if not c.get("PlannedEndDate")]
                if children_without_dates:
                    result["has_issues"] = True
                    result["issues"].append(
                        f"{len(children_without_dates)} of {len(children)} children missing PlannedEndDate"
                    )
                    result["suggestions"].append({
                        "description": f"Propagate PlannedEndDate ({planned_end}) to {len(children_without_dates)} children",
                        "action": "propagate_date_to_children",
                        "data": {
                            "parent_ref": item_ref,
                            "date": planned_end,
                            "child_refs": [c["_ref"] for c in children_without_dates]
                        }
                    })

                # Check if children have Release/Iteration but parent doesn't
                if not release_ref or not iteration_ref:
                    children_with_release = [c for c in children if c.get("Release")]
                    children_with_iteration = [c for c in children if c.get("Iteration")]

                    if not release_ref and children_with_release:
                        # Find most common release among children
                        release_counts: dict[str, int] = {}
                        for child in children_with_release:
                            release_name = child["Release"].get("_refObjectName", "")
                            release_counts[release_name] = release_counts.get(release_name, 0) + 1
                        most_common_release = max(release_counts, key=release_counts.get)

                        result["has_issues"] = True
                        result["issues"].append(
                            f"{len(children_with_release)} children have Release but this item doesn't"
                        )
                        result["suggestions"].append({
                            "description": f"Set Release to most common child Release: {most_common_release}",
                            "action": "set_release_from_children",
                            "data": {
                                "item_ref": item_ref,
                                "release_name": most_common_release
                            }
                        })

        return result

    def fill_missing_data(
        self,
        item_ref: str,
        suggestions: list[dict],
        confirm: bool = True,
    ) -> dict[str, Any]:
        """
        Apply suggested fixes from analyze_data_consistency().

        Args:
            item_ref: Rally item reference (_ref)
            suggestions: List of suggestion dicts from analyze_data_consistency()
            confirm: If True, show what will be changed (for preview mode)

        Returns:
            Dict with:
            - applied (list): List of successfully applied suggestions
            - failed (list): List of failed suggestions with error messages
            - preview (list): If confirm=True, shows what would be changed

        Example:
            # Analyze first
            analysis = api.analyze_data_consistency(feature["_ref"])

            # Preview changes
            preview = api.fill_missing_data(
                feature["_ref"],
                analysis["suggestions"],
                confirm=True
            )

            # Apply changes
            result = api.fill_missing_data(
                feature["_ref"],
                analysis["suggestions"],
                confirm=False
            )
        """
        result = {
            "applied": [],
            "failed": [],
            "preview": []
        }

        for suggestion in suggestions:
            action = suggestion.get("action")
            data = suggestion.get("data", {})
            description = suggestion.get("description")

            try:
                if confirm:
                    # Preview mode - just show what would be changed
                    result["preview"].append({
                        "description": description,
                        "action": action,
                        "data": data
                    })
                else:
                    # Apply the fix
                    if action == "set_iteration":
                        self.update_object(
                            data["item_ref"],
                            {"Iteration": data["iteration_ref"]},
                            backup=True
                        )
                        result["applied"].append(f"Set Iteration to {data['iteration_name']}")

                    elif action == "set_release":
                        self.update_object(
                            data["item_ref"],
                            {"Release": data["release_ref"]},
                            backup=True
                        )
                        result["applied"].append(f"Set Release to {data['release_name']}")

                    elif action == "set_planned_end_date":
                        self.update_object(
                            data["item_ref"],
                            {"PlannedEndDate": data["date"]},
                            backup=True
                        )
                        result["applied"].append(f"Set PlannedEndDate to {data['date']} (from {data['source']})")

                    elif action == "set_parent_date":
                        self.update_object(
                            data["item_ref"],
                            {"PlannedEndDate": data["date"]},
                            backup=True
                        )
                        result["applied"].append(f"Set parent PlannedEndDate to {data['date']}")

                    elif action == "propagate_date_to_children":
                        for child_ref in data["child_refs"]:
                            self.update_object(
                                child_ref,
                                {"PlannedEndDate": data["date"]},
                                backup=True
                            )
                        result["applied"].append(
                            f"Propagated PlannedEndDate to {len(data['child_refs'])} children"
                        )

                    elif action == "set_release_from_children":
                        release = self.find_release(data["release_name"], raise_if_not_found=True)
                        self.update_object(
                            data["item_ref"],
                            {"Release": release["_ref"]},
                            backup=True
                        )
                        result["applied"].append(f"Set Release to {data['release_name']} (from children)")

            except Exception as e:
                result["failed"].append({
                    "description": description,
                    "error": str(e)
                })

        return result

    # ==========================================================================
    # Capacity Visualization & Assignment Recommendations (US789706, US789705)
    # ==========================================================================

    def _get_iteration_assignments(self, iteration_name: str) -> dict[str, float]:
        """
        Get current story point assignments per team member for an iteration.

        Queries Rally for stories in the iteration and aggregates points per owner,
        mapping Rally usernames to team member names.

        Args:
            iteration_name: Iteration name (e.g., "2026.PI1.Iteration3")

        Returns:
            Dict of team member name -> total assigned points
        """
        iteration = self.find_iteration(iteration_name)
        if not iteration:
            return {}

        stories = self.get_user_stories(
            query=f'(Iteration.Name = "{iteration_name}")',
            fetch="Name,FormattedID,PlanEstimate,Owner,ScheduleState",
        )

        assignments: dict[str, float] = {}
        for story in stories:
            points = story.get("PlanEstimate") or 0
            owner = story.get("Owner")
            if not owner or not points:
                continue

            owner_name = owner.get("_refObjectName", "")
            owner_username = owner.get("_refObjectUUID", owner_name)

            # Try to map to team member
            member = _find_team_member(owner_name) or _find_team_member(owner_username)
            if member:
                name = member.get("name", owner_name)
            else:
                name = owner_name

            assignments[name] = assignments.get(name, 0.0) + points

        return assignments

    def visualize_sprint_capacity(
        self,
        iteration_name: str,
        story_assignments: Optional[dict[str, float]] = None,
        default_capacity_per_member: float = 13.0,
        member_capacities: Optional[dict[str, float]] = None,
        bar_width: int = 40,
    ) -> str:
        """
        Visualize sprint capacity with ASCII progress bars.

        If story_assignments is None, queries Rally for current iteration assignments.

        Args:
            iteration_name: Iteration name (e.g., "2026.PI1.Iteration3")
            story_assignments: Optional pre-computed assignments (member name -> points)
            default_capacity_per_member: Default max points per member
            member_capacities: Optional per-member capacity overrides
            bar_width: Width of ASCII progress bars

        Returns:
            Formatted capacity report string with ASCII progress bars
        """
        if story_assignments is None:
            story_assignments = self._get_iteration_assignments(iteration_name)

        report = calculate_team_capacity(
            iteration_name=iteration_name,
            story_assignments=story_assignments,
            default_capacity_per_member=default_capacity_per_member,
            member_capacities=member_capacities,
        )

        return format_capacity_report(report, bar_width=bar_width)

    def recommend_assignments(
        self,
        iteration_name: str,
        feature_id: Optional[str] = None,
        default_capacity_per_member: float = 13.0,
        member_capacities: Optional[dict[str, float]] = None,
    ) -> str:
        """
        Generate assignment recommendations for unassigned stories in an iteration.

        Queries Rally for stories, separates assigned from unassigned, and generates
        skill/capacity-based recommendations for unassigned stories.

        Args:
            iteration_name: Iteration name (e.g., "2026.PI1.Iteration3")
            feature_id: Optional feature FormattedID to scope stories
            default_capacity_per_member: Default max points per member
            member_capacities: Optional per-member capacity overrides

        Returns:
            Formatted assignment plan string
        """
        # Build query
        query_parts = [f'(Iteration.Name = "{iteration_name}")']
        if feature_id:
            query_parts.append(f'(Feature.FormattedID = "{feature_id}")')

        if len(query_parts) > 1:
            query = f'({" AND ".join(query_parts)})'
        else:
            query = query_parts[0]

        stories = self.get_user_stories(
            query=query,
            fetch="Name,FormattedID,PlanEstimate,Owner,ScheduleState,Description",
        )

        # Separate assigned vs unassigned
        unassigned_stories = []
        current_assignments: dict[str, float] = {}

        for story in stories:
            owner = story.get("Owner")
            points = story.get("PlanEstimate") or 0

            if owner and owner.get("_refObjectName"):
                owner_name = owner["_refObjectName"]
                member = _find_team_member(owner_name)
                name = member.get("name", owner_name) if member else owner_name
                current_assignments[name] = current_assignments.get(name, 0.0) + points
            else:
                unassigned_stories.append(story)

        if not unassigned_stories:
            return f"All stories in {iteration_name} are already assigned.\n"

        plan = _recommend_assignments(
            stories=unassigned_stories,
            iteration_name=iteration_name,
            current_assignments=current_assignments,
            default_capacity_per_member=default_capacity_per_member,
            member_capacities=member_capacities,
        )

        return format_assignment_plan(plan)

