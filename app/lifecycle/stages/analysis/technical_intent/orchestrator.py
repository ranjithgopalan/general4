"""Technical Intent Analysis Orchestrator.

Orchestrates the complete 4-phase technical intent analysis pipeline:
1. LLM-Based Intent Analysis (LLMIntentAnalyzer via Haiku)
2. Graph-Based Impact Tracing (ImpactTracer)
3. Blind Spot Detection (BlindSpotDetector)
4. Change List Generation (ChangeListGenerator)
"""

from __future__ import annotations

from typing import Any

from app.lifecycle.stages.analysis.technical_intent.blind_spot_detector import BlindSpotDetector
from app.lifecycle.stages.analysis.technical_intent.impact_tracer import ImpactTracer
from app.lifecycle.stages.analysis.technical_intent.intent_analyzer_llm import LLMIntentAnalyzer
from app.lifecycle.stages.analysis.technical_intent.models import ImpactResult, TechnicalPattern
from app.utils.logging import log


class TechnicalIntentAnalyzer:
    """Main orchestrator for technical intent analysis.

    Runs the complete 4-phase pipeline to transform a requirement into:
    1. Intent analysis via Haiku LLM (pattern + entity extraction)
    2. Full impact trace through knowledge graph
    3. Identified blind spots via systematic checks
    4. Prioritized change list

    Uses Haiku for pure LLM-based intent analysis (no regex fallback).
    """

    def __init__(self, graph: Any):
        """Initialize analyzer with a knowledge graph.

        Args:
            graph: Knowledge graph object (supports node/edge queries)
        """
        self.graph = graph
        self.llm_analyzer = LLMIntentAnalyzer()
        self.tracer = ImpactTracer(graph)
        self.detector = BlindSpotDetector(graph)

    async def analyze(self, requirement: str) -> ImpactResult | None:
        """Run complete technical intent analysis on a requirement.

        Uses Haiku LLM for intent analysis (no regex).

        Args:
            requirement: Raw requirement text

        Returns:
            ImpactResult with pattern, impact, blind spots, and change list
            Returns None if analysis fails or pattern is UNKNOWN

        Raises:
            ValueError: If requirement is empty
            Exception: If analysis fails
        """
        if not requirement or not requirement.strip():
            raise ValueError("Requirement text cannot be empty")

        log.info("\n" + "=" * 80)
        log.info("[ANALYZER] === TECHNICAL INTENT ANALYSIS PIPELINE ===")
        log.info("=" * 80)
        log.info(f"[ANALYZER] Requirement: {requirement[:100]}...")

        # PHASE 1-2: LLM-Based Intent Analysis (Pattern Detection + Entity Extraction)
        log.info("\n[ANALYZER] PHASE 1-2: LLM INTENT ANALYSIS")
        log.info("=" * 80)

        try:
            extraction = await self.llm_analyzer.analyze_intent(requirement)
        except Exception as e:
            log.error(f"[ANALYZER] LLM intent analysis failed: {e}")
            return None

        if extraction.pattern == TechnicalPattern.UNKNOWN:
            log.warning("[ANALYZER] LLM could not detect a recognized pattern")
            log.warning("[ANALYZER] Requirement may not match standard technical patterns")
            return None

        log.info("[ANALYZER] ✓ Intent analyzed via the config-routed classify LLM")
        log.info(f"  Pattern: {extraction.pattern.name}")
        log.info(f"  Confidence: {extraction.confidence:.0%}")
        log.info(f"  Target screen: {extraction.target_screen or '(none)'}")
        log.info(f"  Target table: {extraction.target_table or '(none)'}")
        log.info(f"  Fields: {[f.name for f in extraction.fields]}")
        log.info(f"  Affected flows: {extraction.flows_affected}")

        # PHASE 3: Graph-Based Impact Tracing
        log.info("\n[ANALYZER] PHASE 3: GRAPH-BASED IMPACT TRACING")
        log.info("=" * 80)
        impact = await self.tracer.trace_impact(extraction, extraction.pattern)
        impact.requirement_text = requirement  # carry raw text for blind-spot keyword checks (fixes discard)
        log.info(f"[ANALYZER] ✓ Impact traced")
        log.info(f"  Affected components: {len(impact.all_affected_components())}")
        log.info(f"    Screens: {len(impact.affected_screens)}")
        log.info(f"    Workflows: {len(impact.affected_workflows)}")
        log.info(f"    Processes: {len(impact.affected_processes)}")
        log.info(f"    Code: {len(impact.affected_code)}")
        log.info(f"    Databases: {len(impact.affected_databases)}")
        log.info(f"    Integrations: {len(impact.affected_integrations)}")
        log.info(f"    Downstream: {len(impact.affected_downstream_systems)}")

        # PHASE 4: Blind Spot Detection
        log.info("\n[ANALYZER] PHASE 4: BLIND SPOT DETECTION")
        log.info("=" * 80)
        blind_spots = await self.detector.detect_all(impact)
        impact.blind_spots = blind_spots
        log.info(f"[ANALYZER] ✓ Blind spots identified: {len(blind_spots)}")
        for bs in blind_spots:
            log.info(f"  [{bs.severity}] {bs.type}")

        # Summary
        log.info("\n" + "=" * 80)
        log.info("[ANALYZER] === ANALYSIS COMPLETE ===")
        log.info("=" * 80)
        log.info(f"Pattern: {extraction.pattern.name}")
        log.info(f"Risk Level: {impact.risk_level}")
        log.info(f"Files to change: {len(impact.files_to_change)}")
        log.info(f"Blind spots: {len(blind_spots)}")

        return impact

    async def get_query_keywords_for_retrieval(self, impact: ImpactResult) -> list[str]:
        """Extract keywords from impact for use in retrieval queries.

        Returns component IDs that should be retrieved from KB for context.
        This replaces Query 4 in the original handler.

        Args:
            impact: ImpactResult from analysis

        Returns:
            List of KB IDs relevant to the impact analysis
        """
        keywords = []

        # Collect all component IDs
        for comp in impact.all_affected_components():
            keywords.append(comp.id)

        # Also add natural language descriptions for better retrieval
        if impact.extraction.target_screen:
            keywords.append(impact.extraction.target_screen)
        if impact.extraction.target_table:
            keywords.append(impact.extraction.target_table)

        keywords.extend(impact.extraction.flows_affected)

        # Remove duplicates and filter empty strings
        keywords = list(set(k for k in keywords if k and k.strip()))

        log.info(f"[ANALYZER] Extracted {len(keywords)} keywords for retrieval queries")
        return keywords

    def get_analysis_summary(self, impact: ImpactResult) -> dict[str, Any]:
        """Generate a summary of the analysis for use in FE/output.

        Args:
            impact: ImpactResult from analysis

        Returns:
            Dictionary with analysis summary
        """
        return {
            "pattern": impact.pattern.name,
            "pattern_description": impact.pattern.value,
            "confidence": impact.extraction.confidence,
            "extraction": {
                "target_screen": impact.extraction.target_screen,
                "target_table": impact.extraction.target_table,
                "fields": [{"name": f.name, "type": f.type, "size": f.size} for f in impact.extraction.fields],
                "flows_affected": impact.extraction.flows_affected,
            },
            "affected_components": {
                "screens": [{"id": c.id, "label": c.label} for c in impact.affected_screens],
                "workflows": [{"id": c.id, "label": c.label} for c in impact.affected_workflows],
                "processes": [{"id": c.id, "label": c.label} for c in impact.affected_processes],
                "code": [{"id": c.id, "label": c.label} for c in impact.affected_code],
                "databases": [{"id": c.id, "label": c.label} for c in impact.affected_databases],
                "integrations": [{"id": c.id, "label": c.label} for c in impact.affected_integrations],
                "downstream_systems": [{"id": c.id, "label": c.label} for c in impact.affected_downstream_systems],
            },
            "files_to_change": [
                {
                    "priority": f.get("priority", "P?"),
                    "type": f.get("type", "?"),
                    "file": f.get("file", "?"),
                    "action": f.get("action", "?"),
                    "owner": f.get("owner", "?"),
                }
                for f in impact.files_to_change
            ],
            "blind_spots": [
                {
                    "severity": bs.severity,
                    "type": bs.type,
                    "description": bs.description,
                    "action": bs.action,
                    "impact": bs.impact,
                }
                for bs in impact.blind_spots
            ],
            "risk_assessment": {
                "risk_level": impact.risk_level,
                "risk_reasons": impact.risk_reasons,
            },
        }
