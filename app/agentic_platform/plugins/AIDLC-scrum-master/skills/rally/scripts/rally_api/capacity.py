"""Sprint capacity visualization and assignment recommendations.

Provides ASCII progress bar visualization for sprint capacity,
team member utilization tracking, and skill-based assignment recommendations.

Related user stories:
- US789706: Sprint capacity visualization with ASCII progress bars
- US789705: Assignment recommendations based on team skills, roles, and capacity
"""

from dataclasses import dataclass, field
from typing import Optional

from .config import get_team_members, find_team_member


# ==============================================================================
# Data Classes - Capacity
# ==============================================================================


@dataclass
class MemberCapacity:
    """Capacity data for a single team member."""
    name: str
    role: str
    nominal_points: float
    estimate_multiplier: float
    effective_points: float
    max_capacity: float
    utilization_pct: float
    skills: list[str] = field(default_factory=list)
    pods: list[str] = field(default_factory=list)
    notes: str = ""


@dataclass
class TeamCapacityReport:
    """Aggregate capacity report for the team."""
    iteration_name: str
    total_nominal: float
    total_effective: float
    total_assigned: float
    total_available: float
    members: list[MemberCapacity] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# ==============================================================================
# Data Classes - Assignments
# ==============================================================================


@dataclass
class AssignmentRecommendation:
    """A recommendation for assigning a story to a team member."""
    story_id: str
    story_name: str
    story_points: float
    recommended_member: str
    confidence: str  # "high", "medium", "low"
    skill_match_score: float
    capacity_after: float
    reasoning: str
    alternatives: list[str] = field(default_factory=list)


@dataclass
class AssignmentPlan:
    """Complete assignment plan for an iteration."""
    iteration_name: str
    recommendations: list[AssignmentRecommendation] = field(default_factory=list)
    unassignable: list[dict] = field(default_factory=list)
    capacity_summary: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


# ==============================================================================
# Progress Bar Rendering
# ==============================================================================


def render_progress_bar(
    value: float,
    max_value: float,
    width: int = 40,
    fill_char: str = "#",
    empty_char: str = "-",
    overflow_char: str = "!",
) -> str:
    """
    Render an ASCII progress bar.

    Plain ASCII only (no ANSI codes) for Windows Git Bash compatibility.

    Args:
        value: Current value
        max_value: Maximum value (100% mark)
        width: Bar width in characters
        fill_char: Character for filled portion
        empty_char: Character for empty portion
        overflow_char: Character for over-capacity portion

    Returns:
        Formatted string like "[####------] 40.0%"
    """
    if max_value <= 0:
        return f"[{empty_char * width}]   0.0%"

    pct = (value / max_value) * 100
    filled = int((min(value, max_value) / max_value) * width)

    if value > max_value:
        # Over-capacity: show overflow
        overflow_amount = int(((value - max_value) / max_value) * width)
        overflow_amount = min(overflow_amount, width)
        normal = width - overflow_amount
        bar = fill_char * normal + overflow_char * overflow_amount
    else:
        bar = fill_char * filled + empty_char * (width - filled)

    return f"[{bar}] {pct:5.1f}%"


# ==============================================================================
# Capacity Calculation
# ==============================================================================


def calculate_team_capacity(
    iteration_name: str,
    story_assignments: Optional[dict[str, float]] = None,
    default_capacity_per_member: float = 13.0,
    member_capacities: Optional[dict[str, float]] = None,
) -> TeamCapacityReport:
    """
    Calculate team capacity for a sprint iteration.

    Args:
        iteration_name: Name of the iteration (e.g., "2026.PI1.Iteration3")
        story_assignments: Dict of member name -> assigned points.
                          If None, assumes no assignments.
        default_capacity_per_member: Default max capacity points per member
        member_capacities: Optional override of max capacity per member name

    Returns:
        TeamCapacityReport with per-member and aggregate data
    """
    if story_assignments is None:
        story_assignments = {}
    if member_capacities is None:
        member_capacities = {}

    team_members = get_team_members()
    members = []
    total_nominal = 0.0
    total_effective = 0.0
    total_assigned = 0.0
    warnings = []

    for tm in team_members:
        name = tm.get("name", "Unknown")
        role = tm.get("role", "")
        multiplier = tm.get("estimate_multiplier", 1.0)
        skills = tm.get("skills", [])
        pods = tm.get("pods", [])
        notes = tm.get("notes", "")

        max_cap = member_capacities.get(name, default_capacity_per_member)
        nominal = max_cap
        effective = nominal * multiplier
        assigned = story_assignments.get(name, 0.0)

        if assigned > effective:
            warnings.append(
                f"{name} is over-allocated: {assigned:.1f} points assigned vs {effective:.1f} effective capacity"
            )

        utilization = (assigned / effective * 100) if effective > 0 else 0.0

        mc = MemberCapacity(
            name=name,
            role=role,
            nominal_points=nominal,
            estimate_multiplier=multiplier,
            effective_points=effective,
            max_capacity=max_cap,
            utilization_pct=utilization,
            skills=skills,
            pods=pods,
            notes=notes,
        )
        members.append(mc)
        total_nominal += nominal
        total_effective += effective
        total_assigned += assigned

    total_available = total_effective - total_assigned

    return TeamCapacityReport(
        iteration_name=iteration_name,
        total_nominal=total_nominal,
        total_effective=total_effective,
        total_assigned=total_assigned,
        total_available=total_available,
        members=members,
        warnings=warnings,
    )


def format_capacity_report(report: TeamCapacityReport, bar_width: int = 40) -> str:
    """
    Format a capacity report with ASCII progress bars.

    Args:
        report: TeamCapacityReport to format
        bar_width: Width of progress bars

    Returns:
        Formatted multi-line string
    """
    lines = []
    lines.append(f"Sprint Capacity: {report.iteration_name}")
    lines.append("=" * 70)
    lines.append("")

    # Team summary
    team_bar = render_progress_bar(report.total_assigned, report.total_effective, width=bar_width)
    lines.append(f"Team Overall:  {team_bar}")
    lines.append(
        f"  Assigned: {report.total_assigned:.1f} / {report.total_effective:.1f} effective points"
        f"  (Available: {report.total_available:.1f})"
    )
    lines.append("")
    lines.append("-" * 70)

    # Per-member breakdown
    for mc in report.members:
        assigned = 0.0
        # Calculate assigned from utilization
        if mc.utilization_pct > 0:
            assigned = mc.effective_points * mc.utilization_pct / 100.0

        member_bar = render_progress_bar(assigned, mc.effective_points, width=bar_width)
        lines.append(f"  {mc.name:<15} {member_bar}")
        role_str = f"  Role: {mc.role}" if mc.role else ""
        lines.append(
            f"    Effective: {mc.effective_points:.1f} pts"
            f" (nominal {mc.nominal_points:.1f} x {mc.estimate_multiplier})"
            f"{role_str}"
        )
        if mc.pods:
            lines.append(f"    Pods: {', '.join(mc.pods)}")
        lines.append("")

    # Warnings
    if report.warnings:
        lines.append("-" * 70)
        lines.append("WARNINGS:")
        for w in report.warnings:
            lines.append(f"  ! {w}")
        lines.append("")

    return "\n".join(lines)


# ==============================================================================
# Assignment Recommendations
# ==============================================================================


def _compute_skill_match(story_name: str, story_description: str, member_skills: list[str]) -> float:
    """
    Compute skill match score between a story and member skills.

    Uses substring matching of member skills against story text.

    Args:
        story_name: Story title
        story_description: Story description text
        member_skills: List of member's skills

    Returns:
        Score between 0.0 and 1.0
    """
    if not member_skills:
        return 0.0

    text = (story_name + " " + story_description).lower()
    matches = sum(1 for skill in member_skills if skill.lower() in text)

    return min(matches / max(len(member_skills), 1), 1.0)


def _compute_role_relevance(story_name: str, member_role: str) -> float:
    """
    Compute role relevance score for a story.

    PMs get lower scores for dev-heavy stories; engineers get higher scores.

    Args:
        story_name: Story title
        member_role: Member's role title

    Returns:
        Score between 0.0 and 1.0
    """
    role_lower = member_role.lower()
    name_lower = story_name.lower()

    # PM-type roles get low scores for development work
    pm_keywords = ["manager", "pm", "scrum master", "product owner"]
    dev_keywords = ["implement", "develop", "code", "api", "fix", "refactor", "test", "deploy"]

    is_pm = any(kw in role_lower for kw in pm_keywords)
    is_dev_story = any(kw in name_lower for kw in dev_keywords)

    if is_pm and is_dev_story:
        return 0.2

    # Lead/Engineer roles are generally good for dev stories
    eng_keywords = ["engineer", "developer", "lead", "architect", "senior"]
    is_engineer = any(kw in role_lower for kw in eng_keywords)

    if is_engineer and is_dev_story:
        return 0.9

    if is_engineer:
        return 0.7

    # Default moderate relevance
    return 0.5


def recommend_assignments(
    stories: list[dict],
    iteration_name: str,
    current_assignments: Optional[dict[str, float]] = None,
    default_capacity_per_member: float = 13.0,
    member_capacities: Optional[dict[str, float]] = None,
) -> AssignmentPlan:
    """
    Generate assignment recommendations for unassigned stories.

    Scores each member per story using: skill_match * 0.5 + capacity_fit * 0.3 + role_relevance * 0.2.
    Sorts stories by points descending for better bin-packing.

    Args:
        stories: List of story dicts with FormattedID, Name, PlanEstimate, Description
        iteration_name: Name of the iteration
        current_assignments: Dict of member name -> already-assigned points
        default_capacity_per_member: Default max capacity points per member
        member_capacities: Optional override of max capacity per member name

    Returns:
        AssignmentPlan with recommendations and unassignable stories
    """
    if current_assignments is None:
        current_assignments = {}
    if member_capacities is None:
        member_capacities = {}

    team_members = get_team_members()
    if not team_members:
        return AssignmentPlan(
            iteration_name=iteration_name,
            warnings=["No team members configured. Add members to team.json."],
        )

    # Build running allocation tracker
    running_allocation = {}
    for tm in team_members:
        name = tm.get("name", "Unknown")
        running_allocation[name] = current_assignments.get(name, 0.0)

    # Sort stories by points descending (largest first for better bin-packing)
    sorted_stories = sorted(
        stories,
        key=lambda s: s.get("PlanEstimate", 0) or 0,
        reverse=True,
    )

    recommendations = []
    unassignable = []
    warnings = []

    for story in sorted_stories:
        story_id = story.get("FormattedID", "?")
        story_name = story.get("Name", "")
        story_points = story.get("PlanEstimate", 0) or 0
        story_desc = story.get("Description", "") or ""

        best_member = None
        best_score = -1.0
        best_reasoning = ""
        alternatives = []

        for tm in team_members:
            name = tm.get("name", "Unknown")
            role = tm.get("role", "")
            multiplier = tm.get("estimate_multiplier", 1.0)
            skills = tm.get("skills", [])
            max_cap = member_capacities.get(name, default_capacity_per_member)
            effective_cap = max_cap * multiplier

            # Skip if member is at or over capacity
            remaining = effective_cap - running_allocation.get(name, 0.0)
            if remaining < story_points:
                continue

            # Score components
            skill_score = _compute_skill_match(story_name, story_desc, skills)
            role_score = _compute_role_relevance(story_name, role)
            capacity_score = remaining / effective_cap if effective_cap > 0 else 0.0

            total_score = skill_score * 0.5 + capacity_score * 0.3 + role_score * 0.2

            reasoning_parts = []
            if skill_score > 0:
                reasoning_parts.append(f"skill match ({skill_score:.0%})")
            if capacity_score > 0.5:
                reasoning_parts.append(f"good capacity ({remaining:.1f} pts remaining)")
            if role_score > 0.7:
                reasoning_parts.append(f"role fit ({role})")

            candidate_info = {
                "name": name,
                "score": total_score,
                "reasoning": ", ".join(reasoning_parts) if reasoning_parts else "available capacity",
            }

            if total_score > best_score:
                if best_member:
                    alternatives.append(best_member)
                best_member = candidate_info
                best_score = total_score
                best_reasoning = candidate_info["reasoning"]
            else:
                alternatives.append(candidate_info)

        if best_member:
            # Determine confidence
            if best_score >= 0.6:
                confidence = "high"
            elif best_score >= 0.3:
                confidence = "medium"
            else:
                confidence = "low"

            member_name = best_member["name"]
            running_allocation[member_name] = running_allocation.get(member_name, 0.0) + story_points
            tm_data = find_team_member(member_name)
            multiplier = tm_data.get("estimate_multiplier", 1.0) if tm_data else 1.0
            max_cap = member_capacities.get(member_name, default_capacity_per_member)
            effective_cap = max_cap * multiplier
            capacity_after = running_allocation[member_name] / effective_cap * 100 if effective_cap > 0 else 0

            alt_names = [a["name"] for a in sorted(alternatives, key=lambda x: x["score"], reverse=True)[:3]]

            recommendations.append(AssignmentRecommendation(
                story_id=story_id,
                story_name=story_name,
                story_points=story_points,
                recommended_member=member_name,
                confidence=confidence,
                skill_match_score=best_score,
                capacity_after=capacity_after,
                reasoning=best_reasoning,
                alternatives=alt_names,
            ))
        else:
            unassignable.append({
                "story_id": story_id,
                "story_name": story_name,
                "story_points": story_points,
                "reason": "No team member has sufficient remaining capacity",
            })

    if unassignable:
        warnings.append(f"{len(unassignable)} stories could not be assigned due to capacity constraints")

    # Build capacity summary
    capacity_summary = {}
    for tm in team_members:
        name = tm.get("name", "Unknown")
        multiplier = tm.get("estimate_multiplier", 1.0)
        max_cap = member_capacities.get(name, default_capacity_per_member)
        effective = max_cap * multiplier
        assigned = running_allocation.get(name, 0.0)
        capacity_summary[name] = {
            "effective_capacity": effective,
            "assigned": assigned,
            "remaining": effective - assigned,
            "utilization_pct": (assigned / effective * 100) if effective > 0 else 0,
        }

    return AssignmentPlan(
        iteration_name=iteration_name,
        recommendations=recommendations,
        unassignable=unassignable,
        capacity_summary=capacity_summary,
        warnings=warnings,
    )


def format_assignment_plan(plan: AssignmentPlan, bar_width: int = 30) -> str:
    """
    Format an assignment plan with recommendations and capacity bars.

    Args:
        plan: AssignmentPlan to format
        bar_width: Width of progress bars

    Returns:
        Formatted multi-line string
    """
    lines = []
    lines.append(f"Assignment Recommendations: {plan.iteration_name}")
    lines.append("=" * 70)
    lines.append("")

    if plan.recommendations:
        lines.append(f"Recommendations ({len(plan.recommendations)} stories):")
        lines.append("-" * 70)

        for rec in plan.recommendations:
            confidence_marker = {"high": "[+++]", "medium": "[++ ]", "low": "[+  ]"}.get(rec.confidence, "[   ]")
            lines.append(f"  {confidence_marker} {rec.story_id}: {rec.story_name} ({rec.story_points} pts)")
            lines.append(f"    -> {rec.recommended_member} | {rec.reasoning}")
            if rec.alternatives:
                lines.append(f"    Alternatives: {', '.join(rec.alternatives)}")
            lines.append("")

    if plan.unassignable:
        lines.append(f"Unassignable ({len(plan.unassignable)} stories):")
        lines.append("-" * 70)
        for item in plan.unassignable:
            lines.append(f"  {item['story_id']}: {item['story_name']} ({item['story_points']} pts)")
            lines.append(f"    Reason: {item['reason']}")
        lines.append("")

    if plan.capacity_summary:
        lines.append("Capacity Utilization:")
        lines.append("-" * 70)
        for name, data in plan.capacity_summary.items():
            bar = render_progress_bar(data["assigned"], data["effective_capacity"], width=bar_width)
            lines.append(f"  {name:<15} {bar}  ({data['assigned']:.1f}/{data['effective_capacity']:.1f} pts)")
        lines.append("")

    if plan.warnings:
        lines.append("WARNINGS:")
        for w in plan.warnings:
            lines.append(f"  ! {w}")
        lines.append("")

    return "\n".join(lines)
