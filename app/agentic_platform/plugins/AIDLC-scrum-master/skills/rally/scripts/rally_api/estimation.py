"""
Automated Story Point Estimation Module

Provides complexity-based story point estimation using three objective factors:
1. Technical Complexity (1-5)
2. Integration Requirements (1-5)
3. Risk/Uncertainty (1-5)

Formula: (sum - 3) / 12 × 100 → Fibonacci mapping (1, 2, 3, 5, 8, 13)
"""

from dataclasses import dataclass
from typing import Optional, Tuple, List
import re


@dataclass
class ComplexityFactors:
    """Three-factor complexity assessment for story point estimation."""
    technical_complexity: int  # 1-5: Simple to highly complex
    integration_requirements: int  # 1-5: No integration to many systems
    risk_uncertainty: int  # 1-5: Clear requirements to major unknowns

    def validate(self) -> None:
        """Validate all factors are in 1-5 range."""
        factors = [
            ("Technical Complexity", self.technical_complexity),
            ("Integration Requirements", self.integration_requirements),
            ("Risk/Uncertainty", self.risk_uncertainty)
        ]

        for name, value in factors:
            if not isinstance(value, int) or value < 1 or value > 5:
                raise ValueError(f"{name} must be an integer between 1 and 5, got: {value}")

    def total_score(self) -> int:
        """
        Calculate normalized complexity score (0-100).

        Formula: (sum - 3) / 12 × 100
        - Minimum: 1+1+1=3 → (3-3)/12×100 = 0
        - Maximum: 5+5+5=15 → (15-3)/12×100 = 100

        Returns:
            Complexity score in range 0-100
        """
        sum_factors = (self.technical_complexity +
                      self.integration_requirements +
                      self.risk_uncertainty)
        return int((sum_factors - 3) / 12 * 100)


@dataclass
class EstimationResult:
    """Result of story point estimation with rationale."""
    base_estimate: int  # Fibonacci story points (1, 2, 3, 5, 8, 13)
    complexity_factors: ComplexityFactors
    total_complexity_score: int  # 0-100
    adjusted_estimate: Optional[float] = None  # With team multiplier
    team_member: Optional[str] = None
    should_split: bool = False  # True if > 8 points
    recommendation: str = ""  # Human-readable message
    task_hours: Optional[float] = None  # Story points × 8 × multiplier
    key_drivers: Optional[List[str]] = None  # 2-3 bullet points explaining complexity
    assumptions: Optional[List[str]] = None  # 1-2 bullet points on key assumptions
    rationale_action: str = ""  # "Accept", "Adjust", or "Decompose"


def map_complexity_to_fibonacci(score: int, risk_factor: int) -> Tuple[int, str]:
    """
    Map complexity score (0-100) to Fibonacci sequence.

    Mapping rules:
    - 0-15: 1 point (simple)
    - 16-45: 2 or 3 points (use 3 if risk ≥ 4, else 2)
    - 46-60: 5 points (complex)
    - 61-75: 8 points (very complex)
    - 76-100: 13 points (too large - recommend split)

    Args:
        score: Complexity score 0-100
        risk_factor: Risk/uncertainty factor (1-5) for 2-3 point decision

    Returns:
        (story_points, recommendation_message)
    """
    if score <= 15:
        return 1, "Simple story (low complexity)"
    elif score <= 45:
        # AI judgment: use risk factor to decide 2 vs 3
        if risk_factor >= 4:
            return 3, "3 points (straightforward, but high risk/uncertainty)"
        else:
            return 2, "2 points (straightforward, low risk)"
    elif score <= 60:
        return 5, "Complex story (moderate complexity)"
    elif score <= 75:
        return 8, "Very complex story (high complexity)"
    else:
        return 13, "[WARNING] Story too large (13 pts) - recommend splitting into smaller stories"


def estimate_story(
    story_name: str,
    story_description: str,
    complexity_factors: ComplexityFactors,
    team_member: Optional[str] = None,
    team_multiplier: Optional[float] = None
) -> EstimationResult:
    """
    Estimate story points based on explicit complexity factors.

    Args:
        story_name: Story title
        story_description: Story description
        complexity_factors: Manual complexity assessment
        team_member: Team member name for multiplier adjustment
        team_multiplier: Override multiplier (default: use config lookup)

    Returns:
        EstimationResult with base and adjusted estimates
    """
    # Validate inputs
    complexity_factors.validate()

    # Calculate complexity score
    total_score = complexity_factors.total_score()

    # Map to Fibonacci
    base_estimate, recommendation = map_complexity_to_fibonacci(
        total_score,
        complexity_factors.risk_uncertainty
    )

    # Check if story should be split
    should_split = base_estimate > 8

    # Apply team multiplier if provided
    adjusted_estimate = None
    task_hours = None
    if team_multiplier is not None:
        adjusted_estimate = base_estimate * team_multiplier
        task_hours = base_estimate * 8 * team_multiplier

    return EstimationResult(
        base_estimate=base_estimate,
        complexity_factors=complexity_factors,
        total_complexity_score=total_score,
        adjusted_estimate=adjusted_estimate,
        team_member=team_member,
        should_split=should_split,
        recommendation=recommendation,
        task_hours=task_hours
    )


def estimate_story_ai_analysis(
    story_name: str,
    story_description: str,
    acceptance_criteria: Optional[List[str]] = None,
    team_member: Optional[str] = None,
    team_multiplier: Optional[float] = None
) -> EstimationResult:
    """
    AI-based estimation analyzing story content for complexity indicators.

    Heuristics:
    - Technical: Keywords like "algorithm", "optimization", "migration", "refactor"
    - Integration: Count of systems/APIs mentioned
    - Risk: Words like "unclear", "TBD", "research", "spike"

    Args:
        story_name: Story title
        story_description: Story description
        acceptance_criteria: List of acceptance criteria
        team_member: Team member name for multiplier adjustment
        team_multiplier: Override multiplier (default: use config lookup)

    Returns:
        EstimationResult with AI-analyzed complexity factors
    """
    # Combine all text for analysis
    full_text = f"{story_name} {story_description}"
    if acceptance_criteria:
        full_text += " " + " ".join(acceptance_criteria)
    full_text = full_text.lower()

    # Check for administrative/spike stories (edge cases)
    if _is_administrative_story(story_name):
        # Administrative tasks always 0 points (skip estimation)
        return EstimationResult(
            base_estimate=0,
            complexity_factors=ComplexityFactors(1, 1, 1),
            total_complexity_score=0,
            recommendation="Administrative task - no estimation needed",
            should_split=False
        )

    if _is_spike_story(story_name):
        # Spike stories fixed 2-3 points
        return EstimationResult(
            base_estimate=2,
            complexity_factors=ComplexityFactors(2, 1, 3),
            total_complexity_score=25,
            recommendation="Spike story - fixed 2 point estimate for research",
            should_split=False
        )

    # Analyze technical complexity (1-5)
    technical = _analyze_technical_complexity(full_text)

    # Analyze integration requirements (1-5)
    integration = _analyze_integration_requirements(full_text)

    # Analyze risk/uncertainty (1-5)
    risk = _analyze_risk_uncertainty(full_text)

    # Create complexity factors and estimate
    complexity_factors = ComplexityFactors(
        technical_complexity=technical,
        integration_requirements=integration,
        risk_uncertainty=risk
    )

    return estimate_story(
        story_name=story_name,
        story_description=story_description,
        complexity_factors=complexity_factors,
        team_member=team_member,
        team_multiplier=team_multiplier
    )


def _is_administrative_story(story_name: str) -> bool:
    """Check if story is administrative (no estimation needed)."""
    admin_patterns = [
        r'^administrative',
        r'^\[stage',
        r'^admin:',
    ]
    name_lower = story_name.lower()
    return any(re.search(pattern, name_lower) for pattern in admin_patterns)


def _is_spike_story(story_name: str) -> bool:
    """Check if story is a spike/research story."""
    spike_keywords = ['spike', 'research', 'investigate', 'poc', 'proof of concept']
    name_lower = story_name.lower()
    return any(keyword in name_lower for keyword in spike_keywords)


def _analyze_technical_complexity(text: str) -> int:
    """
    Analyze technical complexity from text content.

    Returns: 1-5 score
    - 1: Simple CRUD, configuration
    - 2: Standard features
    - 3: Moderate complexity
    - 4: Complex algorithms
    - 5: Highly complex, architectural
    """
    # High complexity indicators
    high_complexity = [
        'algorithm', 'optimization', 'performance', 'migration', 'refactor',
        'architecture', 'distributed', 'concurrent', 'parallel', 'cache',
        'encryption', 'security', 'authentication', 'authorization',
        'real-time', 'streaming', 'websocket', 'complex logic'
    ]

    # Moderate complexity indicators
    moderate_complexity = [
        'business logic', 'validation', 'custom', 'workflow', 'state machine',
        'multi-step', 'orchestration', 'transformation', 'aggregation'
    ]

    # Simple indicators
    simple_keywords = [
        'simple', 'basic', 'trivial', 'straightforward', 'crud',
        'configuration', 'config', 'display', 'show', 'list'
    ]

    high_count = sum(1 for keyword in high_complexity if keyword in text)
    moderate_count = sum(1 for keyword in moderate_complexity if keyword in text)
    simple_count = sum(1 for keyword in simple_keywords if keyword in text)

    # Score based on keyword density
    if high_count >= 3:
        return 5
    elif high_count >= 1:
        return 4
    elif moderate_count >= 2:
        return 3
    elif moderate_count >= 1 or simple_count == 0:
        return 2
    else:
        return 1


def _analyze_integration_requirements(text: str) -> int:
    """
    Analyze integration requirements from text content.

    Returns: 1-5 score
    - 1: No integrations
    - 2: Single external system
    - 3: 2-3 systems
    - 4: Multiple systems (4+)
    - 5: Many systems (5+)
    """
    # Integration indicators
    integration_keywords = [
        'api', 'integration', 'external', 'third-party', 'service',
        'endpoint', 'rest', 'graphql', 'soap', 'webhook',
        'database', 'redis', 'kafka', 'queue', 'message',
        'oauth', 'saml', 'ldap', 'active directory'
    ]

    # System/provider names
    systems = [
        'aws', 'azure', 'gcp', 'salesforce', 'sap', 'oracle',
        'stripe', 'paypal', 'google', 'microsoft', 'github',
        'rally', 'jira', 'confluence'
    ]

    integration_count = sum(1 for keyword in integration_keywords if keyword in text)
    system_count = sum(1 for system in systems if system in text)

    total_integrations = integration_count + system_count

    if total_integrations >= 5:
        return 5
    elif total_integrations >= 4:
        return 4
    elif total_integrations >= 2:
        return 3
    elif total_integrations >= 1:
        return 2
    else:
        return 1


def _analyze_risk_uncertainty(text: str) -> int:
    """
    Analyze risk and uncertainty from text content.

    Returns: 1-5 score
    - 1: Clear requirements
    - 2: Minor clarifications
    - 3: Some unknowns
    - 4: Significant uncertainty
    - 5: Major uncertainty
    """
    # High risk indicators
    high_risk = [
        'unclear', 'unknown', 'tbd', 'to be determined', 'investigate',
        'research', 'spike', 'not sure', 'uncertain', 'ambiguous',
        'depends on', 'may need', 'might require', 'unclear requirements'
    ]

    # Moderate risk indicators
    moderate_risk = [
        'new technology', 'unfamiliar', 'first time', 'no experience',
        'legacy', 'undocumented', 'poc', 'prototype', 'experiment'
    ]

    # Clarity indicators (negative risk)
    clear_indicators = [
        'clear', 'well-defined', 'straightforward', 'standard',
        'documented', 'known', 'established', 'proven'
    ]

    high_risk_count = sum(1 for keyword in high_risk if keyword in text)
    moderate_risk_count = sum(1 for keyword in moderate_risk if keyword in text)
    clear_count = sum(1 for keyword in clear_indicators if keyword in text)

    # Score based on risk indicators
    if high_risk_count >= 3:
        return 5
    elif high_risk_count >= 1:
        return 4
    elif moderate_risk_count >= 2:
        return 3
    elif moderate_risk_count >= 1:
        return 2
    elif clear_count >= 1:
        return 1
    else:
        return 2  # Default moderate risk


def generate_estimation_rationale(
    result: EstimationResult,
    story_name: str,
    story_description: str
) -> EstimationResult:
    """
    Generate detailed rationale for story point estimation.

    Adds key drivers, assumptions, and recommendation action to existing EstimationResult.
    US789694: Provide transparency in AI estimation decisions.

    Args:
        result: Base EstimationResult with complexity factors
        story_name: Story title for context
        story_description: Story description for context

    Returns:
        Updated EstimationResult with rationale fields populated
    """
    cf = result.complexity_factors
    key_drivers = []
    assumptions = []

    # Generate key drivers (2-3 bullets)
    if cf.technical_complexity >= 4:
        key_drivers.append("High technical complexity requiring advanced implementation")

    if cf.integration_requirements >= 3:
        if cf.integration_requirements >= 4:
            key_drivers.append("Integration requirements (multiple systems) increase coordination complexity")
        else:
            key_drivers.append("Integration with external systems adds complexity")

    if cf.risk_uncertainty >= 4:
        key_drivers.append("High uncertainty requires investigation/spike work")

    # Ensure 2-3 drivers
    if len(key_drivers) == 0:
        key_drivers.append("Standard complexity with well-understood requirements")
        key_drivers.append("Straightforward implementation with established patterns")
    elif len(key_drivers) == 1:
        if result.base_estimate >= 5:
            key_drivers.append("Moderate complexity with multiple implementation considerations")
        else:
            key_drivers.append("Low complexity with minimal dependencies")

    # Generate assumptions (1-2 bullets)
    if cf.technical_complexity >= 4:
        assumptions.append("Team has experience with similar complex implementations")
    else:
        assumptions.append("Team is familiar with standard implementation patterns")

    if cf.risk_uncertainty >= 4:
        assumptions.append("Story may require clarification or spike work before implementation")
    elif cf.risk_uncertainty <= 2:
        assumptions.append("Requirements are clear and well-defined")

    if cf.integration_requirements >= 3 and len(assumptions) < 2:
        assumptions.append("External systems/APIs are documented with test environments available")

    # Limit to 2 assumptions
    assumptions = assumptions[:2]

    # Determine rationale action
    if result.base_estimate > 8 or result.should_split:
        rationale_action = "Decompose"
    elif result.base_estimate >= 5 and cf.risk_uncertainty >= 4:
        rationale_action = "Adjust"
    else:
        rationale_action = "Accept"

    # Update result with rationale
    result.key_drivers = key_drivers
    result.assumptions = assumptions
    result.rationale_action = rationale_action

    return result


def format_rationale_markdown(result: EstimationResult) -> str:
    """
    Format estimation rationale as markdown for Rally Notes field.

    US789694: Store rationale in Rally for audit trail and team education.

    Args:
        result: EstimationResult with rationale fields

    Returns:
        Markdown-formatted rationale string
    """
    lines = [
        "## Story Point Estimation Rationale",
        "",
        f"**Estimate:** {result.base_estimate} points (Complexity Score: {result.total_complexity_score}/100)",
        "",
        "**Complexity Factors:**",
        f"- Technical Complexity: {result.complexity_factors.technical_complexity}/5",
        f"- Integration Requirements: {result.complexity_factors.integration_requirements}/5",
        f"- Risk/Uncertainty: {result.complexity_factors.risk_uncertainty}/5",
        "",
    ]

    # Add key drivers
    if result.key_drivers:
        lines.append("**Key Drivers:**")
        for driver in result.key_drivers:
            lines.append(f"- {driver}")
        lines.append("")

    # Add assumptions
    if result.assumptions:
        lines.append("**Assumptions:**")
        for assumption in result.assumptions:
            lines.append(f"- {assumption}")
        lines.append("")

    # Add recommendation
    action = result.rationale_action
    if action == "Accept":
        recommendation_text = f"**Recommendation:** {action} - Story is appropriately sized"
    elif action == "Adjust":
        recommendation_text = f"**Recommendation:** {action} - Consider spike story before implementation"
    elif action == "Decompose":
        recommendation_text = f"**Recommendation:** {action} - Story too large, split into smaller stories"
    else:
        recommendation_text = f"**Recommendation:** {action}"

    lines.append(recommendation_text)
    lines.append("")
    lines.append("---")
    lines.append("*Generated by GATHER-scrum-master estimation system*")

    return "\n".join(lines)


def format_estimation_result(result: EstimationResult) -> str:
    """
    Format estimation result as human-readable output.

    Args:
        result: EstimationResult to format

    Returns:
        Formatted string with estimation details
    """
    output = ["📊 Story Point Estimation", ""]

    # Complexity factors
    output.append("Complexity Factors:")
    output.append(f"  Technical Complexity:      {result.complexity_factors.technical_complexity}/5")
    output.append(f"  Integration Requirements:  {result.complexity_factors.integration_requirements}/5")
    output.append(f"  Risk/Uncertainty:          {result.complexity_factors.risk_uncertainty}/5")
    output.append("")
    output.append(f"  Sum: {result.complexity_factors.technical_complexity + result.complexity_factors.integration_requirements + result.complexity_factors.risk_uncertainty}")
    output.append(f"  Complexity Score: {result.total_complexity_score}/100")
    output.append("")

    # Base estimate
    output.append(f"Recommended Estimate: {result.base_estimate} points")
    output.append("")

    # Rationale section (US789694)
    if result.key_drivers or result.assumptions or result.rationale_action:
        output.append("📝 Rationale:")

        if result.key_drivers:
            output.append("  Key Drivers:")
            for driver in result.key_drivers:
                output.append(f"    • {driver}")
            output.append("")

        if result.assumptions:
            output.append("  Assumptions:")
            for assumption in result.assumptions:
                output.append(f"    • {assumption}")
            output.append("")

        if result.rationale_action:
            action = result.rationale_action
            if action == "Accept":
                output.append("  Recommendation: Accept")
                output.append(f"    [OK] Story is appropriately sized ({result.base_estimate} points <= 8 point threshold)")
            elif action == "Adjust":
                output.append("  Recommendation: Adjust")
                output.append("    [WARNING] Consider spike story to reduce uncertainty before implementation")
            elif action == "Decompose":
                output.append("  Recommendation: Decompose")
                output.append(f"    [WARNING] Story too large ({result.base_estimate} points > 8 point threshold)")
                output.append("    [TIP] Split into 2-3 smaller stories targeting <=5 points each")
            output.append("")

    # Team adjustment
    if result.team_member and result.adjusted_estimate is not None:
        output.append(f"Team Member: {result.team_member}")
        output.append(f"  Adjusted Estimate: {result.adjusted_estimate:.1f} points (advisory)")
        if result.task_hours:
            output.append(f"  Task Hours: {result.task_hours:.0f} hours ({result.base_estimate} × 8 × multiplier)")
        output.append("")

    # Recommendations
    output.append("Recommendations:")
    output.append(f"  - {result.recommendation}")

    if result.should_split:
        output.append("  - [WARNING] Story is too large (>8 points)")
        output.append("  - Recommend splitting into 2-3 smaller stories")
        output.append("  - Target: Each sub-story <=5 points")
    else:
        output.append("  - Story size is appropriate (≤8 points)")

    return "\n".join(output)
