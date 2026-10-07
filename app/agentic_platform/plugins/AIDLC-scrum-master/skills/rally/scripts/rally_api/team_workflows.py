"""Team-specific Rally workflows and utilities.

This module contains workflow functions that are specific to our team's
processes and conventions, separate from the core Rally API client.
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional, Any

from .client import RallyAPI, DEFAULT_MAX_WORKERS


def create_feature_with_stages(
    api: RallyAPI,
    name: str,
    parent_ref: str,
    description: str = "",
    state: str = "Funnel",
    owner_ref: Optional[str] = None,
    mike_ref: Optional[str] = None,
) -> dict:
    """
    Create a feature with Administrative Tasks story and all stage tasks.

    Creates:
    - The feature
    - Administrative Tasks story (0 points)
      - [Stage 1.1] Capture requirements from users
      - [Stage 1.2] Review requirements from users
      - [Stage 1.3] Create acceptance criteria, BRD, and TDD
      - [Stage 2.1] Sent artifacts to stake holders
      - [Stage 2.2] Obtain sign off
      - [Stage 4.1] Notified beta testers of new version
      - [Stage 4.2] Captured feedback
      - [Stage 4.3] Triage feedback

    Args:
        api: RallyAPI instance
        name: Feature name
        parent_ref: Parent capability reference (_ref)
        description: Feature description
        state: Feature state (default: "Funnel")
        owner_ref: Owner reference for the feature (_ref)
        mike_ref: Mike's user reference for Stage 2.1 task

    Returns:
        Dict with created feature and administrative tasks:
        - feature: Created feature object
        - admin_story: Administrative Tasks story object
        - tasks: Dict of stage tasks
    """
    result = {"feature": None, "admin_story": None, "tasks": {}}

    # Create the feature
    feature_fields: dict[str, Any] = {
        "Name": name,
        "State": state,
        "Parent": parent_ref,
    }
    if description:
        feature_fields["Description"] = description
    if owner_ref:
        feature_fields["Owner"] = owner_ref
    if api.project_ref:
        feature_fields["Project"] = api.project_ref

    feature = api.create_object("portfolioitem/feature", feature_fields)
    result["feature"] = feature
    feature_ref = feature.get("_ref")

    # Create Administrative Tasks story
    admin_story_fields: dict[str, Any] = {
        "Name": "Administrative Tasks",
        "Description": """<p><strong>As a</strong> team member,<br/>
<strong>I want</strong> to track administrative and process activities,<br/>
<strong>So that</strong> coordination work is visible and tracked.</p>

<h3>Acceptance Criteria</h3>
<ul>
<li>All administrative tasks completed</li>
<li>Relevant stakeholders notified and engaged</li>
<li>Process milestones documented</li>
</ul>""",
        "PortfolioItem": feature_ref,
        "ScheduleState": "Defined",
        "PlanEstimate": 0,  # Administrative tasks = 0 points
    }
    if api.project_ref:
        admin_story_fields["Project"] = api.project_ref

    admin_story = api.create_object("hierarchicalrequirement", admin_story_fields)
    result["admin_story"] = admin_story
    admin_story_ref = admin_story.get("_ref")

    # Define all stage tasks under Administrative Tasks with specific hour estimates
    all_tasks = [
        {
            "name": "[Stage 1.1] Capture requirements from users",
            "description": "<p>Meet with stakeholders and users to gather initial requirements. Document all requirements, constraints, and expectations. Collect any existing documentation, mockups, or reference materials.</p>",
            "estimate": 1,
        },
        {
            "name": "[Stage 1.2] Review requirements from users",
            "description": "<p>Review all gathered requirements and supporting documents. Identify any gaps, ambiguities, or conflicts. Schedule follow-up meetings to clarify any questions with stakeholders or users.</p>",
            "estimate": 3,
        },
        {
            "name": "[Stage 1.3] Create acceptance criteria, BRD, and TDD",
            "description": "<p>Convert the gathered requirements into clear, testable acceptance criteria. Document in SharePoint using the standard acceptance criteria format.</p>",
            "estimate": 4,
        },
        {
            "name": "[Stage 2.1] Sent artifacts to stake holders",
            "description": "<p>Send all completed artifacts to stakeholders and users for their review. Record when and to whom artifacts were sent.</p>",
            "estimate": 1,
            "assign_to_mike": True,
        },
        {
            "name": "[Stage 2.2] Obtain sign off",
            "description": "<p>Follow up with stakeholders to obtain formal sign-off on all artifacts. Document the sign-off in this ticket.</p>",
            "estimate": 1,
            "assign_to_mike": True,
        },
        {
            "name": "[Stage 4.1] Notified beta testers of new version",
            "description": "<p>Notify users/beta testers that a new version is available for testing. Include release notes describing what was added, changed, or fixed.</p>",
            "estimate": 1,
            "assign_to_mike": True,
        },
        {
            "name": "[Stage 4.2] Captured feedback",
            "description": "<p>Collect feedback from beta testers through meetings, emails, surveys, or a feedback form. Document all feedback in SharePoint.</p>",
            "estimate": 1,
        },
        {
            "name": "[Stage 4.3] Triage feedback",
            "description": "<p>Review all collected feedback and categorize it: Bugs (create Defects), Amendments (new stories with [Amendment] tag), or New Features (create new feature).</p>",
            "estimate": 4,
        },
    ]

    # Build task fields
    all_task_fields = []
    for task_def in all_tasks:
        task_fields: dict[str, Any] = {
            "Name": task_def["name"],
            "Description": task_def.get("description", ""),
            "WorkProduct": admin_story_ref,
            "State": "Defined",
            "Estimate": task_def.get("estimate", 0),  # Use specific hour estimates
        }

        # Assign Stage 2.x and 4.1 tasks to Mike if ref provided
        if task_def.get("assign_to_mike") and mike_ref:
            task_fields["Owner"] = mike_ref

        if api.project_ref:
            task_fields["Project"] = api.project_ref

        all_task_fields.append((task_def["name"], task_fields))

    # Create all tasks in parallel
    if all_task_fields:
        with ThreadPoolExecutor(max_workers=DEFAULT_MAX_WORKERS) as executor:
            task_futures = {
                executor.submit(api.create_object, "task", fields): task_name
                for task_name, fields in all_task_fields
            }

            for future in as_completed(task_futures):
                task_name = task_futures[future]
                task = future.result()
                result["tasks"][task_name] = task

    return result
