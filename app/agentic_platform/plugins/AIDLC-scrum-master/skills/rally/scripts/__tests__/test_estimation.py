"""
Unit tests for automated story point estimation module.

Tests complexity-based estimation, Fibonacci mapping, AI analysis, and edge cases.
"""

import pytest
from rally_api.estimation import (
    ComplexityFactors,
    EstimationResult,
    estimate_story,
    estimate_story_ai_analysis,
    map_complexity_to_fibonacci,
    format_estimation_result
)


class TestComplexityFactors:
    """Tests for ComplexityFactors data class."""

    def test_validate_valid_factors(self):
        """Test validation accepts valid factors (1-5)."""
        factors = ComplexityFactors(1, 3, 5)
        factors.validate()  # Should not raise

    def test_validate_invalid_low(self):
        """Test validation rejects factors below 1."""
        factors = ComplexityFactors(0, 3, 5)
        with pytest.raises(ValueError, match="must be an integer between 1 and 5"):
            factors.validate()

    def test_validate_invalid_high(self):
        """Test validation rejects factors above 5."""
        factors = ComplexityFactors(3, 6, 2)
        with pytest.raises(ValueError, match="must be an integer between 1 and 5"):
            factors.validate()

    def test_total_score_minimum(self):
        """Test minimum complexity score (all 1s = 0)."""
        factors = ComplexityFactors(1, 1, 1)
        assert factors.total_score() == 0

    def test_total_score_maximum(self):
        """Test maximum complexity score (all 5s = 100)."""
        factors = ComplexityFactors(5, 5, 5)
        assert factors.total_score() == 100

    def test_total_score_midpoint(self):
        """Test midpoint complexity score."""
        factors = ComplexityFactors(3, 3, 3)
        # Sum: 9, (9-3)/12*100 = 50
        assert factors.total_score() == 50

    def test_total_score_various(self):
        """Test various complexity scores."""
        # Sum: 4, (4-3)/12*100 = 8.33 -> 8
        assert ComplexityFactors(2, 1, 1).total_score() == 8

        # Sum: 6, (6-3)/12*100 = 25
        assert ComplexityFactors(2, 2, 2).total_score() == 25

        # Sum: 13, (13-3)/12*100 = 83.33 -> 83
        assert ComplexityFactors(4, 4, 5).total_score() == 83


class TestFibonacciMapping:
    """Tests for map_complexity_to_fibonacci function."""

    def test_1_point_range(self):
        """Test 1 point mapping (score 0-15)."""
        points, msg = map_complexity_to_fibonacci(0, 1)
        assert points == 1
        assert "Simple story" in msg

        points, msg = map_complexity_to_fibonacci(15, 1)
        assert points == 1

    def test_2_point_range_low_risk(self):
        """Test 2 point mapping (score 16-45, risk < 4)."""
        points, msg = map_complexity_to_fibonacci(16, 1)
        assert points == 2
        assert "low risk" in msg

        points, msg = map_complexity_to_fibonacci(30, 2)
        assert points == 2

        points, msg = map_complexity_to_fibonacci(45, 3)
        assert points == 2

    def test_3_point_range_high_risk(self):
        """Test 3 point mapping (score 16-45, risk >= 4)."""
        points, msg = map_complexity_to_fibonacci(16, 4)
        assert points == 3
        assert "high risk" in msg

        points, msg = map_complexity_to_fibonacci(30, 5)
        assert points == 3

    def test_5_point_range(self):
        """Test 5 point mapping (score 46-60)."""
        points, msg = map_complexity_to_fibonacci(46, 2)
        assert points == 5
        assert "Complex story" in msg

        points, msg = map_complexity_to_fibonacci(60, 2)
        assert points == 5

    def test_8_point_range(self):
        """Test 8 point mapping (score 61-75)."""
        points, msg = map_complexity_to_fibonacci(61, 2)
        assert points == 8
        assert "Very complex" in msg

        points, msg = map_complexity_to_fibonacci(75, 2)
        assert points == 8

    def test_13_point_range(self):
        """Test 13 point mapping (score 76-100, should split)."""
        points, msg = map_complexity_to_fibonacci(76, 2)
        assert points == 13
        assert "too large" in msg.lower()
        assert "split" in msg.lower()

        points, msg = map_complexity_to_fibonacci(100, 2)
        assert points == 13


class TestEstimateStory:
    """Tests for estimate_story function."""

    def test_estimate_simple_story(self):
        """Test estimation of simple story (1 point)."""
        factors = ComplexityFactors(1, 1, 1)
        result = estimate_story("Simple UI change", "Update button color", factors)

        assert result.base_estimate == 1
        assert result.total_complexity_score == 0
        assert not result.should_split

    def test_estimate_complex_story(self):
        """Test estimation of complex story (5 points)."""
        factors = ComplexityFactors(4, 3, 2)
        result = estimate_story("OAuth integration", "Add OAuth 2.0", factors)

        assert result.base_estimate == 5
        assert result.total_complexity_score == 50
        assert not result.should_split

    def test_estimate_too_large(self):
        """Test estimation flags stories >8 points for split."""
        factors = ComplexityFactors(5, 5, 5)
        result = estimate_story("Complex migration", "Migrate entire system", factors)

        assert result.base_estimate == 13
        assert result.total_complexity_score == 100
        assert result.should_split

    def test_estimate_with_team_multiplier(self):
        """Test estimation with team member multiplier."""
        factors = ComplexityFactors(3, 3, 3)
        result = estimate_story(
            "Medium story",
            "Some description",
            factors,
            team_member="chris",
            team_multiplier=0.6
        )

        assert result.base_estimate == 5
        assert result.adjusted_estimate == 3.0  # 5 * 0.6
        assert result.task_hours == 24.0  # 5 * 8 * 0.6
        assert result.team_member == "chris"

    def test_estimate_2_vs_3_point_decision(self):
        """Test 2 vs 3 point decision based on risk factor."""
        # Low risk -> 2 points
        factors_low_risk = ComplexityFactors(2, 2, 2)
        result_low = estimate_story("Low risk", "Description", factors_low_risk)
        assert result_low.base_estimate == 2

        # High risk -> 3 points
        factors_high_risk = ComplexityFactors(2, 2, 4)
        result_high = estimate_story("High risk", "Description", factors_high_risk)
        assert result_high.base_estimate == 3


class TestAIAnalysis:
    """Tests for AI-based estimation analysis."""

    def test_administrative_story(self):
        """Test administrative stories return 0 points."""
        result = estimate_story_ai_analysis(
            "Administrative: Update documentation",
            "Update team wiki"
        )
        assert result.base_estimate == 0

        result = estimate_story_ai_analysis(
            "[Stage 1] Setup environment",
            "Initial setup"
        )
        assert result.base_estimate == 0

    def test_spike_story(self):
        """Test spike stories return fixed 2 points."""
        result = estimate_story_ai_analysis(
            "Spike: Research OAuth providers",
            "Investigate OAuth options"
        )
        assert result.base_estimate == 2

        result = estimate_story_ai_analysis(
            "Research: Database performance",
            "POC for caching"
        )
        assert result.base_estimate == 2

    def test_simple_crud_story(self):
        """Test simple CRUD story detection."""
        result = estimate_story_ai_analysis(
            "Add user list display",
            "Simple CRUD operation to list users"
        )
        # Should be low complexity (1-2 points)
        assert result.base_estimate <= 2

    def test_high_complexity_keywords(self):
        """Test high complexity keyword detection."""
        result = estimate_story_ai_analysis(
            "Implement complex algorithm",
            "Develop optimization algorithm with caching and encryption for distributed system"
        )
        # Should detect high complexity keywords
        assert result.complexity_factors.technical_complexity >= 4

    def test_integration_detection(self):
        """Test integration requirements detection."""
        result = estimate_story_ai_analysis(
            "Integrate with external APIs",
            "Add integration with Google, Salesforce, and AWS services via REST API"
        )
        # Should detect multiple integrations
        assert result.complexity_factors.integration_requirements >= 3

    def test_risk_detection(self):
        """Test risk/uncertainty detection."""
        result = estimate_story_ai_analysis(
            "Implement TBD feature",
            "Requirements unclear, may need research, uncertain approach"
        )
        # Should detect high risk keywords
        assert result.complexity_factors.risk_uncertainty >= 4

    def test_acceptance_criteria_analysis(self):
        """Test acceptance criteria are included in analysis."""
        result = estimate_story_ai_analysis(
            "Basic feature",
            "Simple description",
            acceptance_criteria=[
                "Must integrate with complex external API",
                "Requires encryption and security",
                "Uncertain performance requirements"
            ]
        )
        # Should detect complexity in acceptance criteria
        assert result.base_estimate >= 3


class TestFormatEstimationResult:
    """Tests for format_estimation_result function."""

    def test_format_basic_result(self):
        """Test formatting basic estimation result."""
        factors = ComplexityFactors(3, 3, 3)
        result = EstimationResult(
            base_estimate=5,
            complexity_factors=factors,
            total_complexity_score=50,
            should_split=False,
            recommendation="Complex story (moderate complexity)"
        )

        output = format_estimation_result(result)

        assert "📊 Story Point Estimation" in output
        assert "Technical Complexity:      3/5" in output
        assert "Integration Requirements:  3/5" in output
        assert "Risk/Uncertainty:          3/5" in output
        assert "Complexity Score: 50/100" in output
        assert "Recommended Estimate: 5 points" in output

    def test_format_with_team_member(self):
        """Test formatting result with team member."""
        factors = ComplexityFactors(4, 3, 2)
        result = EstimationResult(
            base_estimate=5,
            complexity_factors=factors,
            total_complexity_score=50,
            adjusted_estimate=3.0,
            team_member="chris",
            task_hours=24.0,
            should_split=False,
            recommendation="Complex story"
        )

        output = format_estimation_result(result)

        assert "Team Member: chris" in output
        assert "Adjusted Estimate: 3.0 points" in output
        assert "Task Hours: 24 hours" in output

    def test_format_should_split(self):
        """Test formatting result with split recommendation."""
        factors = ComplexityFactors(5, 5, 5)
        result = EstimationResult(
            base_estimate=13,
            complexity_factors=factors,
            total_complexity_score=100,
            should_split=True,
            recommendation="⚠️ Story too large - recommend splitting"
        )

        output = format_estimation_result(result)

        assert "⚠️" in output
        assert "too large" in output.lower()
        assert "split" in output.lower()


class TestRationaleGeneration:
    """Tests for story point estimation rationale generation (US789694)."""

    def test_generate_rationale_accept_low_complexity(self):
        """Test rationale action 'Accept' for stories ≤8 pts with low complexity."""
        from rally_api.estimation import generate_estimation_rationale

        factors = ComplexityFactors(2, 2, 2)
        result = estimate_story("Simple feature", "Add a button", factors)
        result = generate_estimation_rationale(result, "Simple feature", "Add a button")

        assert result.rationale_action == "Accept"
        assert result.key_drivers is not None
        assert len(result.key_drivers) >= 2
        assert len(result.key_drivers) <= 3
        assert result.assumptions is not None
        assert len(result.assumptions) >= 1
        assert len(result.assumptions) <= 2

    def test_generate_rationale_decompose_large_story(self):
        """Test rationale action 'Decompose' for 13 pt stories."""
        from rally_api.estimation import generate_estimation_rationale

        factors = ComplexityFactors(5, 5, 5)
        result = estimate_story("Huge migration", "Migrate entire system", factors)
        result = generate_estimation_rationale(result, "Huge migration", "Migrate entire system")

        assert result.rationale_action == "Decompose"
        assert result.base_estimate == 13
        assert result.should_split is True

    def test_generate_rationale_adjust_high_risk(self):
        """Test rationale action 'Adjust' for high-risk 5/8 pt stories."""
        from rally_api.estimation import generate_estimation_rationale

        # 5 point story with high risk
        factors = ComplexityFactors(3, 3, 5)
        result = estimate_story("Research new API", "Investigate unknown API", factors)
        result = generate_estimation_rationale(result, "Research new API", "Investigate unknown API")

        assert result.rationale_action == "Adjust"
        assert result.base_estimate >= 5
        assert result.complexity_factors.risk_uncertainty >= 4

    def test_key_drivers_high_technical(self):
        """Test key drivers extraction for high technical complexity."""
        from rally_api.estimation import generate_estimation_rationale

        factors = ComplexityFactors(5, 1, 1)
        result = estimate_story("Complex algorithm", "Implement sorting", factors)
        result = generate_estimation_rationale(result, "Complex algorithm", "Implement sorting")

        assert any("technical complexity" in driver.lower() for driver in result.key_drivers)

    def test_key_drivers_high_integration(self):
        """Test key drivers extraction for high integration requirements."""
        from rally_api.estimation import generate_estimation_rationale

        factors = ComplexityFactors(1, 5, 1)
        result = estimate_story("Multi-system integration", "Connect 5 APIs", factors)
        result = generate_estimation_rationale(result, "Multi-system integration", "Connect 5 APIs")

        assert any("integration" in driver.lower() for driver in result.key_drivers)

    def test_key_drivers_count(self):
        """Test that key drivers are limited to 2-3 bullets."""
        from rally_api.estimation import generate_estimation_rationale

        factors = ComplexityFactors(5, 5, 5)
        result = estimate_story("Complex story", "High complexity", factors)
        result = generate_estimation_rationale(result, "Complex story", "High complexity")

        assert len(result.key_drivers) >= 2
        assert len(result.key_drivers) <= 3

    def test_assumptions_low_risk(self):
        """Test assumptions for low risk stories (clear/familiar)."""
        from rally_api.estimation import generate_estimation_rationale

        factors = ComplexityFactors(2, 1, 1)
        result = estimate_story("Standard feature", "Add form field", factors)
        result = generate_estimation_rationale(result, "Standard feature", "Add form field")

        # Should have clear/familiar assumptions
        assert any("familiar" in assumption.lower() or "clear" in assumption.lower()
                   for assumption in result.assumptions)

    def test_assumptions_high_risk(self):
        """Test assumptions for high risk stories (spike/clarification)."""
        from rally_api.estimation import generate_estimation_rationale

        factors = ComplexityFactors(2, 1, 5)
        result = estimate_story("Unclear requirements", "TBD feature", factors)
        result = generate_estimation_rationale(result, "Unclear requirements", "TBD feature")

        # Should have uncertainty assumptions
        assert any("clarification" in assumption.lower() or "spike" in assumption.lower()
                   for assumption in result.assumptions)

    def test_assumptions_count(self):
        """Test that assumptions are limited to 1-2 bullets."""
        from rally_api.estimation import generate_estimation_rationale

        factors = ComplexityFactors(3, 3, 3)
        result = estimate_story("Moderate story", "Standard complexity", factors)
        result = generate_estimation_rationale(result, "Moderate story", "Standard complexity")

        assert len(result.assumptions) >= 1
        assert len(result.assumptions) <= 2

    def test_format_rationale_markdown(self):
        """Test markdown formatting for Rally Notes field."""
        from rally_api.estimation import generate_estimation_rationale, format_rationale_markdown

        factors = ComplexityFactors(4, 3, 2)
        result = estimate_story("Test story", "Test description", factors)
        result = generate_estimation_rationale(result, "Test story", "Test description")

        markdown = format_rationale_markdown(result)

        # Verify markdown structure
        assert "## Story Point Estimation Rationale" in markdown
        assert f"**Estimate:** {result.base_estimate} points" in markdown
        assert "**Complexity Factors:**" in markdown
        assert "**Key Drivers:**" in markdown
        assert "**Assumptions:**" in markdown
        assert "**Recommendation:**" in markdown
        assert "*Generated by GATHER-scrum-master estimation system*" in markdown

    def test_cli_output_includes_rationale(self):
        """Test that CLI formatting includes rationale section."""
        from rally_api.estimation import generate_estimation_rationale, format_estimation_result

        factors = ComplexityFactors(3, 3, 3)
        result = estimate_story("Test story", "Test description", factors)
        result = generate_estimation_rationale(result, "Test story", "Test description")

        output = format_estimation_result(result)

        # Verify rationale section in CLI output
        assert "📝 Rationale:" in output
        assert "Key Drivers:" in output
        assert "Assumptions:" in output
        assert "Recommendation:" in output


class TestEdgeCases:
    """Tests for edge cases and boundary conditions."""

    def test_boundary_score_0(self):
        """Test boundary: score 0 (all factors = 1)."""
        factors = ComplexityFactors(1, 1, 1)
        result = estimate_story("Boundary test", "Description", factors)
        assert result.total_complexity_score == 0
        assert result.base_estimate == 1

    def test_boundary_score_100(self):
        """Test boundary: score 100 (all factors = 5)."""
        factors = ComplexityFactors(5, 5, 5)
        result = estimate_story("Boundary test", "Description", factors)
        assert result.total_complexity_score == 100
        assert result.base_estimate == 13

    def test_boundary_15_16(self):
        """Test boundary between 1 and 2 points (score 15 vs 16)."""
        # Score 15 -> 1 point
        points_15, _ = map_complexity_to_fibonacci(15, 1)
        assert points_15 == 1

        # Score 16 -> 2 points
        points_16, _ = map_complexity_to_fibonacci(16, 1)
        assert points_16 == 2

    def test_boundary_45_46(self):
        """Test boundary between 2-3 and 5 points (score 45 vs 46)."""
        # Score 45 -> 2 or 3 points
        points_45_low, _ = map_complexity_to_fibonacci(45, 2)
        assert points_45_low == 2

        points_45_high, _ = map_complexity_to_fibonacci(45, 4)
        assert points_45_high == 3

        # Score 46 -> 5 points
        points_46, _ = map_complexity_to_fibonacci(46, 2)
        assert points_46 == 5

    def test_boundary_60_61(self):
        """Test boundary between 5 and 8 points (score 60 vs 61)."""
        # Score 60 -> 5 points
        points_60, _ = map_complexity_to_fibonacci(60, 2)
        assert points_60 == 5

        # Score 61 -> 8 points
        points_61, _ = map_complexity_to_fibonacci(61, 2)
        assert points_61 == 8

    def test_boundary_75_76(self):
        """Test boundary between 8 and 13 points (score 75 vs 76)."""
        # Score 75 -> 8 points
        points_75, _ = map_complexity_to_fibonacci(75, 2)
        assert points_75 == 8

        # Score 76 -> 13 points (should split)
        points_76, _ = map_complexity_to_fibonacci(76, 2)
        assert points_76 == 13

    def test_no_multiplier(self):
        """Test estimation without team multiplier."""
        factors = ComplexityFactors(3, 3, 3)
        result = estimate_story("Test", "Description", factors)

        assert result.adjusted_estimate is None
        assert result.task_hours is None
        assert result.team_member is None

    def test_zero_multiplier(self):
        """Test estimation with zero multiplier (edge case)."""
        factors = ComplexityFactors(3, 3, 3)
        result = estimate_story(
            "Test",
            "Description",
            factors,
            team_member="test",
            team_multiplier=0.0
        )

        assert result.adjusted_estimate == 0.0
        assert result.task_hours == 0.0

    def test_empty_story_name(self):
        """Test estimation with empty story name."""
        factors = ComplexityFactors(3, 3, 3)
        result = estimate_story("", "", factors)

        assert result.base_estimate == 5
        assert result.total_complexity_score == 50

    def test_ai_analysis_empty_text(self):
        """Test AI analysis with empty text."""
        result = estimate_story_ai_analysis("", "")

        # Should still return valid estimate (default to moderate)
        assert result.base_estimate > 0
        assert isinstance(result.complexity_factors, ComplexityFactors)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
