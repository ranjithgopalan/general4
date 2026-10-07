"""
Unit tests for rally_api.capacity module.

Tests capacity visualization, progress bars, skill matching,
role relevance, and assignment recommendations.
"""

import pytest
from unittest.mock import patch
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from rally_api.capacity import (
    render_progress_bar,
    calculate_team_capacity,
    format_capacity_report,
    _compute_skill_match,
    _compute_role_relevance,
    recommend_assignments,
    format_assignment_plan,
    MemberCapacity,
    TeamCapacityReport,
    AssignmentRecommendation,
    AssignmentPlan,
)


MOCK_TEAM = [
    {
        "name": "Alice",
        "rally_username": "alice@test.com",
        "role": "Senior Engineer",
        "estimate_multiplier": 0.8,
        "skills": ["Python", "AWS", "Angular"],
        "pods": ["POD 1"],
        "notes": "Tech lead",
    },
    {
        "name": "Bob",
        "rally_username": "bob@test.com",
        "role": "Junior Developer",
        "estimate_multiplier": 1.5,
        "skills": ["JavaScript", "Node.js"],
        "pods": ["POD 1"],
    },
    {
        "name": "Carol",
        "rally_username": "carol@test.com",
        "role": "Project Manager",
        "pods": ["POD 1", "POD 2"],
    },
]


class TestRenderProgressBar:
    """Tests for render_progress_bar function."""

    def test_empty_bar(self):
        """Test empty progress bar (0%)."""
        result = render_progress_bar(0, 100, width=10)
        assert "[----------]" in result
        assert "0.0%" in result

    def test_half_bar(self):
        """Test half-filled progress bar (50%)."""
        result = render_progress_bar(50, 100, width=10)
        assert "[#####-----]" in result
        assert "50.0%" in result

    def test_full_bar(self):
        """Test full progress bar (100%)."""
        result = render_progress_bar(100, 100, width=10)
        assert "[##########]" in result
        assert "100.0%" in result

    def test_overflow_bar(self):
        """Test overflow progress bar (>100%)."""
        result = render_progress_bar(150, 100, width=10)
        assert "!" in result
        assert "150.0%" in result

    def test_zero_max_value(self):
        """Test bar with zero max value."""
        result = render_progress_bar(5, 0, width=10)
        assert "[----------]" in result
        assert "0.0%" in result

    def test_custom_chars(self):
        """Test custom fill and empty characters."""
        result = render_progress_bar(50, 100, width=10, fill_char="=", empty_char=".")
        assert "[=====.....]" in result

    def test_default_width(self):
        """Test default width of 40."""
        result = render_progress_bar(50, 100)
        # Bar content should be 40 chars (between brackets)
        bar_content = result.split("[")[1].split("]")[0]
        assert len(bar_content) == 40


class TestCalculateTeamCapacity:
    """Tests for calculate_team_capacity function."""

    @patch('rally_api.capacity.get_team_members')
    def test_basic_capacity(self, mock_get_team):
        """Test basic capacity calculation with assignments."""
        mock_get_team.return_value = MOCK_TEAM

        report = calculate_team_capacity(
            "2026.PI1.Iteration3",
            story_assignments={"Alice": 5.0, "Bob": 3.0},
        )

        assert report.iteration_name == "2026.PI1.Iteration3"
        assert len(report.members) == 3
        assert report.total_assigned == 8.0
        # Alice: 13 * 0.8 = 10.4, Bob: 13 * 1.5 = 19.5, Carol: 13 * 1.0 = 13
        assert report.total_effective == pytest.approx(10.4 + 19.5 + 13.0)

    @patch('rally_api.capacity.get_team_members')
    def test_over_allocation_warning(self, mock_get_team):
        """Test over-allocation generates a warning."""
        mock_get_team.return_value = MOCK_TEAM

        report = calculate_team_capacity(
            "2026.PI1.Iteration3",
            story_assignments={"Alice": 15.0},  # Over Alice's 10.4 effective
        )

        assert len(report.warnings) > 0
        assert "Alice" in report.warnings[0]
        assert "over-allocated" in report.warnings[0]

    @patch('rally_api.capacity.get_team_members')
    def test_no_assignments(self, mock_get_team):
        """Test capacity with no assignments."""
        mock_get_team.return_value = MOCK_TEAM

        report = calculate_team_capacity("2026.PI1.Iteration3")

        assert report.total_assigned == 0.0
        assert report.total_available == report.total_effective
        assert len(report.warnings) == 0

    @patch('rally_api.capacity.get_team_members')
    def test_missing_multiplier_defaults_to_one(self, mock_get_team):
        """Test member without multiplier defaults to 1.0."""
        mock_get_team.return_value = [
            {"name": "Dave", "rally_username": "dave@test.com"}
        ]

        report = calculate_team_capacity("2026.PI1.Iteration3")

        assert report.members[0].estimate_multiplier == 1.0
        assert report.members[0].effective_points == 13.0

    @patch('rally_api.capacity.get_team_members')
    def test_custom_capacity_per_member(self, mock_get_team):
        """Test custom capacity override per member."""
        mock_get_team.return_value = MOCK_TEAM

        report = calculate_team_capacity(
            "2026.PI1.Iteration3",
            member_capacities={"Alice": 20.0},
        )

        alice = [m for m in report.members if m.name == "Alice"][0]
        assert alice.nominal_points == 20.0
        assert alice.effective_points == pytest.approx(20.0 * 0.8)


class TestFormatCapacityReport:
    """Tests for format_capacity_report function."""

    @patch('rally_api.capacity.get_team_members')
    def test_contains_progress_bars(self, mock_get_team):
        """Test output contains progress bars."""
        mock_get_team.return_value = MOCK_TEAM

        report = calculate_team_capacity(
            "2026.PI1.Iteration3",
            story_assignments={"Alice": 5.0},
        )
        output = format_capacity_report(report)

        assert "[" in output
        assert "]" in output
        assert "#" in output

    @patch('rally_api.capacity.get_team_members')
    def test_contains_member_names(self, mock_get_team):
        """Test output contains all member names."""
        mock_get_team.return_value = MOCK_TEAM

        report = calculate_team_capacity("2026.PI1.Iteration3")
        output = format_capacity_report(report)

        assert "Alice" in output
        assert "Bob" in output
        assert "Carol" in output

    @patch('rally_api.capacity.get_team_members')
    def test_contains_iteration_name(self, mock_get_team):
        """Test output contains iteration name."""
        mock_get_team.return_value = MOCK_TEAM

        report = calculate_team_capacity("2026.PI1.Iteration3")
        output = format_capacity_report(report)

        assert "2026.PI1.Iteration3" in output

    @patch('rally_api.capacity.get_team_members')
    def test_contains_pods(self, mock_get_team):
        """Test output contains pod information."""
        mock_get_team.return_value = MOCK_TEAM

        report = calculate_team_capacity("2026.PI1.Iteration3")
        output = format_capacity_report(report)

        assert "POD 1" in output

    @patch('rally_api.capacity.get_team_members')
    def test_shows_warnings(self, mock_get_team):
        """Test output shows warnings section."""
        mock_get_team.return_value = MOCK_TEAM

        report = calculate_team_capacity(
            "2026.PI1.Iteration3",
            story_assignments={"Alice": 15.0},
        )
        output = format_capacity_report(report)

        assert "WARNINGS" in output


class TestSkillMatch:
    """Tests for _compute_skill_match function."""

    def test_full_match(self):
        """Test skill match when all skills appear in story text."""
        score = _compute_skill_match(
            "Implement Python AWS Lambda", "", ["Python", "AWS"]
        )
        assert score == 1.0

    def test_no_match(self):
        """Test no skill match."""
        score = _compute_skill_match("Setup database", "", ["Python", "AWS"])
        assert score == 0.0

    def test_partial_match(self):
        """Test partial skill match."""
        score = _compute_skill_match(
            "Build Python service", "", ["Python", "AWS", "Angular"]
        )
        assert 0.0 < score < 1.0

    def test_empty_skills(self):
        """Test with empty skills list."""
        score = _compute_skill_match("Any story", "description", [])
        assert score == 0.0

    def test_case_insensitive(self):
        """Test skill matching is case-insensitive."""
        score = _compute_skill_match("python lambda", "", ["Python"])
        assert score > 0.0


class TestRoleRelevance:
    """Tests for _compute_role_relevance function."""

    def test_pm_on_dev_story(self):
        """Test PM gets low score for development stories."""
        score = _compute_role_relevance("Implement API endpoint", "Project Manager")
        assert score <= 0.3

    def test_engineer_on_dev_story(self):
        """Test engineer gets high score for development stories."""
        score = _compute_role_relevance("Implement API endpoint", "Senior Engineer")
        assert score >= 0.8

    def test_lead_relevance(self):
        """Test lead role gets good score."""
        score = _compute_role_relevance("Implement feature", "Project Lead / Developer")
        assert score >= 0.7

    def test_default_relevance(self):
        """Test unknown role gets moderate score."""
        score = _compute_role_relevance("Some task", "Intern")
        assert 0.3 <= score <= 0.7


class TestRecommendAssignments:
    """Tests for recommend_assignments function."""

    @patch('rally_api.capacity.get_team_members')
    @patch('rally_api.capacity.find_team_member')
    def test_basic_recommendation(self, mock_find, mock_get_team):
        """Test skill match picks the right member."""
        mock_get_team.return_value = MOCK_TEAM
        mock_find.side_effect = lambda name: next(
            (m for m in MOCK_TEAM if m["name"] == name), None
        )

        stories = [
            {
                "FormattedID": "US001",
                "Name": "Implement Python AWS Lambda function",
                "PlanEstimate": 3,
                "Description": "Build a Python Lambda for data processing on AWS",
            }
        ]

        plan = recommend_assignments(stories, "2026.PI1.Iteration3")

        assert len(plan.recommendations) == 1
        # Alice has Python + AWS skills, should be recommended
        assert plan.recommendations[0].recommended_member == "Alice"
        assert plan.recommendations[0].story_id == "US001"

    @patch('rally_api.capacity.get_team_members')
    @patch('rally_api.capacity.find_team_member')
    def test_capacity_overflow_goes_to_unassignable(self, mock_find, mock_get_team):
        """Test stories go to unassignable when capacity is exhausted."""
        mock_get_team.return_value = MOCK_TEAM
        mock_find.side_effect = lambda name: next(
            (m for m in MOCK_TEAM if m["name"] == name), None
        )

        # Create stories that exceed total team capacity
        stories = [
            {"FormattedID": f"US{i}", "Name": f"Story {i}", "PlanEstimate": 20, "Description": ""}
            for i in range(10)
        ]

        plan = recommend_assignments(stories, "2026.PI1.Iteration3")

        assert len(plan.unassignable) > 0
        assert any("capacity" in item["reason"].lower() for item in plan.unassignable)

    @patch('rally_api.capacity.get_team_members')
    @patch('rally_api.capacity.find_team_member')
    def test_confidence_levels(self, mock_find, mock_get_team):
        """Test confidence levels are assigned based on score."""
        mock_get_team.return_value = MOCK_TEAM
        mock_find.side_effect = lambda name: next(
            (m for m in MOCK_TEAM if m["name"] == name), None
        )

        stories = [
            {
                "FormattedID": "US001",
                "Name": "Implement Python AWS Angular app",
                "PlanEstimate": 3,
                "Description": "Full match for Alice's skills",
            }
        ]

        plan = recommend_assignments(stories, "2026.PI1.Iteration3")

        assert len(plan.recommendations) == 1
        assert plan.recommendations[0].confidence in ("high", "medium", "low")

    @patch('rally_api.capacity.get_team_members')
    def test_no_team_members(self, mock_get_team):
        """Test graceful handling when no team members configured."""
        mock_get_team.return_value = []

        stories = [
            {"FormattedID": "US001", "Name": "Story", "PlanEstimate": 3, "Description": ""}
        ]

        plan = recommend_assignments(stories, "2026.PI1.Iteration3")

        assert len(plan.warnings) > 0
        assert "No team members" in plan.warnings[0]

    @patch('rally_api.capacity.get_team_members')
    @patch('rally_api.capacity.find_team_member')
    def test_current_assignments_reduce_capacity(self, mock_find, mock_get_team):
        """Test current assignments are considered in capacity."""
        mock_get_team.return_value = MOCK_TEAM
        mock_find.side_effect = lambda name: next(
            (m for m in MOCK_TEAM if m["name"] == name), None
        )

        stories = [
            {"FormattedID": "US001", "Name": "New story", "PlanEstimate": 5, "Description": ""}
        ]

        # Alice already has 8 points (effective cap = 10.4), only 2.4 remaining
        plan = recommend_assignments(
            stories, "2026.PI1.Iteration3",
            current_assignments={"Alice": 8.0}
        )

        # Alice shouldn't be recommended since 5 > 2.4 remaining
        if plan.recommendations:
            assert plan.recommendations[0].recommended_member != "Alice"


class TestFormatAssignmentPlan:
    """Tests for format_assignment_plan function."""

    def test_output_formatting(self):
        """Test basic output formatting."""
        plan = AssignmentPlan(
            iteration_name="2026.PI1.Iteration3",
            recommendations=[
                AssignmentRecommendation(
                    story_id="US001",
                    story_name="Test Story",
                    story_points=3,
                    recommended_member="Alice",
                    confidence="high",
                    skill_match_score=0.8,
                    capacity_after=50.0,
                    reasoning="skill match, good capacity",
                    alternatives=["Bob"],
                )
            ],
            capacity_summary={
                "Alice": {
                    "effective_capacity": 10.4,
                    "assigned": 3.0,
                    "remaining": 7.4,
                    "utilization_pct": 28.8,
                }
            },
        )

        output = format_assignment_plan(plan)

        assert "2026.PI1.Iteration3" in output
        assert "US001" in output
        assert "Alice" in output
        assert "Test Story" in output
        assert "[+++]" in output  # high confidence marker

    def test_unassignable_shown(self):
        """Test unassignable stories are shown."""
        plan = AssignmentPlan(
            iteration_name="2026.PI1.Iteration3",
            unassignable=[
                {
                    "story_id": "US999",
                    "story_name": "Overflow Story",
                    "story_points": 13,
                    "reason": "No capacity",
                }
            ],
            warnings=["1 stories could not be assigned"],
        )

        output = format_assignment_plan(plan)

        assert "US999" in output
        assert "Unassignable" in output
        assert "WARNINGS" in output

    def test_empty_plan(self):
        """Test formatting empty plan."""
        plan = AssignmentPlan(iteration_name="2026.PI1.Iteration3")

        output = format_assignment_plan(plan)

        assert "2026.PI1.Iteration3" in output
