"""Relationship justification builder — constructs description text for artifact relationships.

Pattern: Deterministic extraction from ImpactAnalysis + formatting rules.
No LLM dependency — 100% reproducible, <50ms latency.

Used by stage handlers to populate fe_artifact_relationships.description field.
"""

from typing import Any, Dict, List, Optional


class RelationshipJustification:
    """Builder for artifact relationship justifications."""

    @staticmethod
    def build_analysis_from_requirement(
        requirement_text: Optional[str],
        classification_change_class: Optional[str],
        systems_affected: List[Dict[str, Any]],
        matched_kb_cards: List[Dict[str, str]],
        scope_items: Dict[str, List[Dict[str, str]]],
        coverage_pct: float,
    ) -> str:
        """Build justification for REQUIREMENT → ANALYSIS relationship.

        Args:
            requirement_text: Original requirement (or None if not available)
            classification_change_class: "NEW", "ENHANCEMENT", "EXISTING", "DERIVED"
            systems_affected: List of {system_id, system_name, impact_level}
            matched_kb_cards: List of {id, kind, label}
            scope_items: {in_scope: [...], out_of_scope: [...], deferred: [...]}
            coverage_pct: Float 0-100

        Returns:
            Human-readable justification string (1-2 sentences)
        """
        parts = []

        # 1. Core relationship statement
        parts.append("Analysis derived from requirement.")

        # 2. Classification + scope
        if classification_change_class:
            parts.append(f"{classification_change_class} classification:")

        # 3. Systems impact
        if systems_affected:
            system_count = len(systems_affected)
            system_names = ", ".join(s.get("system_name", s.get("system_id", "?")) for s in systems_affected[:3])
            suffix = f", ..." if system_count > 3 else ""
            parts.append(f"{system_count} systems affected ({system_names}{suffix}).")

        # 4. Scope summary
        in_scope = scope_items.get("in_scope", [])
        out_of_scope = scope_items.get("out_of_scope", [])
        scope_summary = []
        if in_scope:
            scope_summary.append(f"{len(in_scope)} in-scope items")
        if out_of_scope:
            scope_summary.append(f"{len(out_of_scope)} out-of-scope")
        if scope_summary:
            parts.append(", ".join(scope_summary) + ".")

        # 5. KB grounding
        if matched_kb_cards:
            card_count = len(matched_kb_cards)
            card_ids = ", ".join(c.get("id", "?") for c in matched_kb_cards[:3])
            suffix = ", ..." if card_count > 3 else ""
            parts.append(f"Matched {card_count} KB entities ({card_ids}{suffix}).")

        # 6. Coverage
        parts.append(f"Coverage: {coverage_pct:.0f}%.")

        # Join and clean up
        justification = " ".join(parts)
        # Remove double spaces
        while "  " in justification:
            justification = justification.replace("  ", " ")
        return justification.strip()

    @staticmethod
    def build_fsd_from_analysis(
        analysis_matched_count: int,
        analysis_affected_count: int,
        fsd_acceptance_criteria_count: int,
        fsd_grounded_ac_count: int,
        systems_affected: List[Dict[str, Any]],
        screen_specs_count: int,
        coverage_pct: float,
    ) -> str:
        """Build justification for ANALYSIS → FSD relationship.

        Args:
            analysis_matched_count: Count of matched cards from analysis
            analysis_affected_count: Count of affected nodes from analysis
            fsd_acceptance_criteria_count: Total AC rows in FSD
            fsd_grounded_ac_count: AC rows with KB links
            systems_affected: Systems touched by FSD
            screen_specs_count: Count of screen specs in FSD
            coverage_pct: Float 0-100

        Returns:
            Human-readable justification string (1-2 sentences)
        """
        parts = []

        # 1. Core relationship
        parts.append("FSD derived from analysis.")

        # 2. Analysis foundation
        total_matched = analysis_matched_count + analysis_affected_count
        parts.append(f"Analysis covered {total_matched} items ({analysis_matched_count} direct, {analysis_affected_count} graph-walked).")

        # 3. FSD details
        if fsd_acceptance_criteria_count > 0:
            grounding_pct = (fsd_grounded_ac_count / fsd_acceptance_criteria_count) * 100 if fsd_acceptance_criteria_count > 0 else 0
            parts.append(
                f"FSD specifies {fsd_acceptance_criteria_count} acceptance criteria "
                f"({fsd_grounded_ac_count} grounded to KB, {grounding_pct:.0f}%)."
            )

        # 4. Screen specs
        if screen_specs_count > 0:
            parts.append(f"{screen_specs_count} screen specifications defined.")

        # 5. Systems impact
        if systems_affected:
            system_names = ", ".join(s.get("system_name", "?") for s in systems_affected[:2])
            parts.append(f"Affects {len(systems_affected)} systems ({system_names}).")

        # 6. Coverage
        parts.append(f"Analysis coverage preserved: {coverage_pct:.0f}%.")

        # Join and clean
        justification = " ".join(parts)
        while "  " in justification:
            justification = justification.replace("  ", " ")
        return justification.strip()

    @staticmethod
    def build_architecture_from_fsd(
        fsd_acceptance_criteria_count: int,
        architecture_components_count: int,
        architecture_apis_count: int,
        systems_affected: List[Dict[str, Any]],
        database_changes: Optional[List[str]] = None,
        coverage_pct: float = 100.0,
    ) -> str:
        """Build justification for FSD → SRD (Architecture) relationship.

        Args:
            fsd_acceptance_criteria_count: ACs from FSD
            architecture_components_count: Components designed in SRD
            architecture_apis_count: APIs designed in SRD
            systems_affected: Systems touched by architecture
            database_changes: Optional list of DB changes (e.g., ["Add DELIVERY_STATUS column"])
            coverage_pct: Coverage percentage

        Returns:
            Human-readable justification string
        """
        parts = []

        # 1. Core relationship
        parts.append("SRD (architecture) implements FSD.")

        # 2. FSD foundation
        parts.append(f"Designs architecture for {fsd_acceptance_criteria_count} FSD acceptance criteria.")

        # 3. Component/API design
        design_items = []
        if architecture_components_count > 0:
            design_items.append(f"{architecture_components_count} components")
        if architecture_apis_count > 0:
            design_items.append(f"{architecture_apis_count} APIs")
        if design_items:
            parts.append(f"Includes {', '.join(design_items)}.")

        # 4. Database changes
        if database_changes and len(database_changes) > 0:
            db_summary = "; ".join(database_changes[:2])
            suffix = "; ..." if len(database_changes) > 2 else ""
            parts.append(f"Database: {db_summary}{suffix}.")

        # 5. Systems impact
        if systems_affected:
            system_names = ", ".join(s.get("system_name", "?") for s in systems_affected[:2])
            parts.append(f"Impacts {len(systems_affected)} systems ({system_names}).")

        # 6. Coverage
        if coverage_pct > 0:
            parts.append(f"Coverage: {coverage_pct:.0f}%.")

        # Join and clean
        justification = " ".join(parts)
        while "  " in justification:
            justification = justification.replace("  ", " ")
        return justification.strip()

    @staticmethod
    def build_stories_from_architecture(
        architecture_components_count: int,
        stories_count: int,
        total_story_points: int,
        systems_affected: List[Dict[str, Any]],
        coverage_pct: float = 100.0,
    ) -> str:
        """Build justification for SRD → STORIES relationship.

        Args:
            architecture_components_count: Components from SRD
            stories_count: User stories generated
            total_story_points: Sum of story points
            systems_affected: Systems touched
            coverage_pct: Coverage

        Returns:
            Human-readable justification string
        """
        parts = []

        # 1. Core relationship
        parts.append("User stories derived from SRD (architecture).")

        # 2. Design foundation
        parts.append(f"Based on {architecture_components_count} architectural components.")

        # 3. Stories breakdown
        if stories_count > 0:
            parts.append(f"Generated {stories_count} stories ({total_story_points} total points).")

        # 4. Systems impact
        if systems_affected:
            system_names = ", ".join(s.get("system_name", "?") for s in systems_affected[:2])
            parts.append(f"Development spans {len(systems_affected)} systems ({system_names}).")

        # 5. Coverage
        parts.append(f"Coverage: {coverage_pct:.0f}%.")

        # Join and clean
        justification = " ".join(parts)
        while "  " in justification:
            justification = justification.replace("  ", " ")
        return justification.strip()

    @staticmethod
    def build_developer_from_stories(
        stories_count: int,
        files_modified_count: int,
        systems_affected: List[Dict[str, Any]],
        components_implemented: List[str],
        apis_implemented: List[str],
        coverage_pct: float = 100.0,
    ) -> str:
        """Build justification for STORIES → DEVELOPER relationship.

        Args:
            stories_count: User stories implemented
            files_modified_count: Count of source files changed
            systems_affected: Systems touched
            components_implemented: List of component IDs implemented
            apis_implemented: List of API IDs implemented
            coverage_pct: Coverage

        Returns:
            Human-readable justification string
        """
        parts = []

        # 1. Core relationship
        parts.append("Implementation derived from user stories.")

        # 2. Stories coverage
        parts.append(f"Implements {stories_count} user stories.")

        # 3. Code changes
        parts.append(f"Modifies {files_modified_count} source files.")

        # 4. Components/APIs
        impl_items = []
        if components_implemented:
            impl_items.append(f"{len(components_implemented)} components")
        if apis_implemented:
            impl_items.append(f"{len(apis_implemented)} APIs")
        if impl_items:
            parts.append(f"Implements {', '.join(impl_items)}.")

        # 5. Systems impact
        if systems_affected:
            system_names = ", ".join(s.get("system_name", "?") for s in systems_affected[:2])
            parts.append(f"Touches {len(systems_affected)} systems ({system_names}).")

        # 6. Coverage
        parts.append(f"Coverage: {coverage_pct:.0f}%.")

        # Join and clean
        justification = " ".join(parts)
        while "  " in justification:
            justification = justification.replace("  ", " ")
        return justification.strip()

    @staticmethod
    def build_qa_from_developer(
        stories_count: int,
        test_cases_count: int,
        systems_tested: List[Dict[str, Any]],
        coverage_pct: float = 100.0,
    ) -> str:
        """Build justification for DEVELOPER → QA relationship.

        Args:
            stories_count: User stories to test
            test_cases_count: Test cases created
            systems_tested: Systems under test
            coverage_pct: Test coverage

        Returns:
            Human-readable justification string
        """
        parts = []

        # 1. Core relationship
        parts.append("QA test plan validates developer implementation.")

        # 2. Implementation coverage
        parts.append(f"Tests {stories_count} user stories.")

        # 3. Test details
        if test_cases_count > 0:
            parts.append(f"{test_cases_count} test cases defined.")

        # 4. Systems tested
        if systems_tested:
            system_names = ", ".join(s.get("system_name", "?") for s in systems_tested[:2])
            parts.append(f"Covers {len(systems_tested)} systems ({system_names}).")

        # 5. Coverage
        parts.append(f"Coverage: {coverage_pct:.0f}%.")

        # Join and clean
        justification = " ".join(parts)
        while "  " in justification:
            justification = justification.replace("  ", " ")
        return justification.strip()

    @staticmethod
    def build_merge_from_qa(
        stories_count: int,
        test_pass_rate: float,
        systems_released: List[Dict[str, Any]],
        coverage_pct: float = 100.0,
    ) -> str:
        """Build justification for QA → MERGE/RELEASE relationship.

        Args:
            stories_count: User stories released
            test_pass_rate: Percentage of tests passing (0-100)
            systems_released: Systems being deployed
            coverage_pct: Overall coverage

        Returns:
            Human-readable justification string
        """
        parts = []

        # 1. Core relationship
        parts.append("Release approved by QA.")

        # 2. Test results
        parts.append(f"Test pass rate: {test_pass_rate:.0f}%.")

        # 3. Stories released
        parts.append(f"Releases {stories_count} user stories.")

        # 4. Systems deployed
        if systems_released:
            system_names = ", ".join(s.get("system_name", "?") for s in systems_released[:2])
            parts.append(f"Deploys to {len(systems_released)} systems ({system_names}).")

        # 5. Coverage
        parts.append(f"Coverage: {coverage_pct:.0f}%.")

        # Join and clean
        justification = " ".join(parts)
        while "  " in justification:
            justification = justification.replace("  ", " ")
        return justification.strip()
